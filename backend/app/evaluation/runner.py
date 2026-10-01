"""Evaluation runner: compare baselines on a labeled split.

    python -m app.evaluation.runner --split dev --baselines all
    python -m app.evaluation.runner --split validation --calibrate out/calibration.json

Baselines
  llm_only            B1: LLM reads the note and predicts codes (no RAG, no validation)
  llm_rag             B2: LLM + retrieval over raw sentences (no NLP, no validation)
  llm_rag_validation  B3: B2 + deterministic validation (rejected codes dropped)
  full                B4: NLP (entities + ConText) + entity-level RAG + LLM + validation + confidence
  retrieval_only      heuristic coder over the full pipeline (no LLM) - runs offline
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from sqlalchemy.orm import Session

from app.coding.engine import CodingEngine
from app.coding.models import CodingModel, HeuristicCodingModel, build_model
from app.confidence.calibration import LabeledSuggestion, calibrate
from app.confidence.scorer import assess, load_calibration, retrieval_signal, validation_signal
from app.core.config import get_settings
from app.evaluation.metrics import DocResult, aggregate, categorize_errors
from app.extraction.pipeline import ClinicalExtractor
from app.ingestion.cleaning import clean_text
from app.ingestion.sections import detect_sections
from app.knowledge.codes import normalize_code
from app.knowledge.repository import get_snapshot
from app.models.enums import ReviewRoute, ValidationStatus
from app.rag.retriever import HybridRetriever
from app.validation.engine import Proposal, ValidationContext, ValidationEngine

BASELINES = ("llm_only", "llm_rag", "llm_rag_validation", "full", "retrieval_only")
LLM_BASELINES = {"llm_only", "llm_rag", "llm_rag_validation", "full"}


def load_split(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_document(
    rec: dict,
    baseline: str,
    model: CodingModel,
    retriever: HybridRetriever,
    db: Session,
    collect: list[LabeledSuggestion] | None = None,
) -> DocResult:
    started = time.perf_counter()
    text = clean_text(rec["clinical_text"])
    sections = detect_sections(text)
    gold = {
        normalize_code(g["code"]) for g in rec["gold_codes"] if g.get("system", "ICD-10-CM") == "ICD-10-CM"
    }
    use_nlp = baseline in ("full", "retrieval_only")
    llm_extract = model if (use_nlp and getattr(model, "supports_extraction", False)) else None
    extraction = ClinicalExtractor(llm_model=llm_extract).extract(text, sections)
    entities = extraction.entities if use_nlp else []
    mode = {"llm_only": "direct", "llm_rag": "rag", "llm_rag_validation": "rag"}.get(baseline, "full")
    engine = CodingEngine(model, retriever)
    res = engine.run(
        text,
        sections,
        entities if use_nlp else extraction.entities,
        encounter_type=rec.get("encounter_type", "outpatient"),
        patient_sex=rec.get("patient_sex"),
        patient_age=rec.get("patient_age"),
        mode=mode,
    )

    snap = retriever.snapshot
    proposals = [
        Proposal(
            code=c.code,
            system=c.code_system,
            evidence=c.evidence,
            entity_text=c.entity,
            rationale=c.rationale,
            llm_confidence=c.confidence,
            from_candidates=c.from_candidates,
        )
        for c in res.output.codes
    ]
    vctx = ValidationContext(
        text=text,
        entities=extraction.entities,
        snapshots={"ICD-10-CM": snap},
        encounter_type=rec.get("encounter_type", "outpatient"),
        patient_sex=rec.get("patient_sex"),
        patient_age=rec.get("patient_age"),
    )
    validated = ValidationEngine().validate(proposals, vctx)
    cal = load_calibration()

    doc = DocResult(
        document_id=rec["document_id"],
        gold=gold,
        predicted=[],
        retrieved={rc.code for g in res.groups for rc in g.candidates},
    )
    doc.total_suggestions = len(validated)
    for v in validated:
        rc = res.candidates_by_code.get(v.code)
        signals = {
            "evidence": v.best_evidence_score,
            "retrieval": retrieval_signal(rc.score if rc else None, rc.rank if rc else None),
            "validation": validation_signal(v.status, sum(1 for i in v.issues if i.severity == "flag")),
            "entity": v.entity_similarity if (v.linked_entity and v.linked_entity.affirmed) else 0.0,
            "agreement": None,
            "llm": v.proposal.llm_confidence,
        }
        conf = assess(signals, v.status, cal)
        if v.info is None or not v.info.billable:
            doc.invalid.append(v.code)
        if v.best_evidence_score < 0.85 or v.info is None:
            doc.unsupported.append(v.code)
        if any(
            i.rule in ("negation_contradiction", "family_history", "uncertain_diagnosis") for i in v.issues
        ):
            doc.negated_predictions.append(v.code)
        if collect is not None and v.status != ValidationStatus.REJECTED:
            collect.append(LabeledSuggestion(signals, v.status == ValidationStatus.PASSED, v.code in gold))
        keep = baseline in ("llm_only", "llm_rag") or v.status != ValidationStatus.REJECTED
        if keep and v.code not in doc.predicted:
            doc.predicted.append(v.code)
            if conf.route == ReviewRoute.STANDARD:
                doc.auto_accept.append(v.code)
            elif conf.route == ReviewRoute.MANDATORY:
                doc.mandatory += 1
    # Acceptable-but-not-required codes (e.g. external cause, Z79.4 with type 1 diabetes) are neutral:
    # neither credited as missing nor penalized as extra.
    optional = {normalize_code(c) for c in rec.get("optional_codes", [])} - gold
    if optional:
        doc.predicted = [c for c in doc.predicted if c not in optional]
        doc.auto_accept = [c for c in doc.auto_accept if c not in optional]
    doc.latency_ms = int((time.perf_counter() - started) * 1000)
    doc.cost_usd = res.output.cost_usd + extraction.cost_usd
    doc.errors = categorize_errors(doc)
    return doc


def evaluate(
    db: Session,
    records: list[dict],
    baselines: list[str],
    model: CodingModel | None = None,
    collect: list[LabeledSuggestion] | None = None,
) -> dict:
    s = get_settings()
    model = model or build_model()
    retriever = HybridRetriever(db, "ICD-10-CM", get_snapshot(db, "ICD-10-CM"))
    report: dict = {
        "config": {
            "llm_provider": s.llm_provider,
            "model": getattr(model, "model", "?"),
            "retrieval_version": retriever.version,
            "kb_version": retriever.snapshot.version,
            "pipeline_version": s.pipeline_version,
        },
        "baselines": {},
    }
    heuristic = isinstance(model, HeuristicCodingModel)
    primary = "retrieval_only" if heuristic else "full"
    for b in baselines:
        if b in LLM_BASELINES and heuristic:
            report["baselines"][b] = {"skipped": "requires LLM_PROVIDER=openrouter"}
            continue
        m = HeuristicCodingModel() if b == "retrieval_only" else model
        docs = [run_document(r, b, m, retriever, db, collect if b == primary else None) for r in records]
        report["baselines"][b] = {
            "metrics": aggregate(docs),
            "documents": [{**asdict(d), "gold": sorted(d.gold), "retrieved": len(d.retrieved)} for d in docs],
        }
    return report


def to_markdown(report: dict) -> str:
    cols = [
        "precision",
        "recall",
        "f1",
        "category_f1",
        "exact_match_accuracy",
        "unsupported_code_rate",
        "retrieval_recall",
        "auto_accept_precision",
        "mandatory_review_share",
        "latency_ms_p50",
        "cost_per_document_usd",
    ]
    lines = ["| baseline | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for name, b in report["baselines"].items():
        if "skipped" in b:
            lines.append(f"| {name} | " + " | ".join(["-"] * len(cols)) + " |")
            continue
        m = b["metrics"]
        lines.append(
            f"| {name} | " + " | ".join("" if m.get(c) is None else str(m.get(c)) for c in cols) + " |"
        )
    lines.append("")
    for name, b in report["baselines"].items():
        if "metrics" in b:
            lines.append(f"**{name} errors:** {b['metrics']['error_breakdown']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    from app.core.logging import configure_logging
    from app.db.session import session_scope

    p = argparse.ArgumentParser(prog="python -m app.evaluation.runner")
    p.add_argument("--data-dir", default=str(Path(__file__).resolve().parents[3] / "data" / "evaluation"))
    p.add_argument(
        "--split", default="dev", choices=["dev", "validation", "test", "realworld", "blind", "blind2"]
    )
    p.add_argument("--baselines", default="all")
    p.add_argument("--out", help="write JSON report here")
    p.add_argument("--calibrate", help="fit confidence weights/threshold on this split and write JSON here")
    p.add_argument("--target-precision", type=float, default=0.95)
    a = p.parse_args(argv)
    configure_logging("WARNING", json_logs=False)
    if a.split == "test" and a.calibrate:
        sys.exit("Never calibrate on the test split.")
    baselines = list(BASELINES) if a.baselines == "all" else a.baselines.split(",")
    records = load_split(Path(a.data_dir) / f"{a.split}.jsonl")
    collect: list[LabeledSuggestion] | None = [] if a.calibrate else None
    with session_scope() as db:
        if a.calibrate:
            baselines = ["retrieval_only"] if get_settings().llm_provider == "heuristic" else ["full"]
        report = evaluate(db, records, baselines, collect=collect)
    report["split"] = a.split
    print(to_markdown(report))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, indent=2, default=str))
    if a.calibrate and collect is not None:
        cal = calibrate(collect, a.target_precision)
        Path(a.calibrate).parent.mkdir(parents=True, exist_ok=True)
        Path(a.calibrate).write_text(json.dumps(cal.to_json(), indent=2))
        print(
            f"\ncalibration written to {a.calibrate}: threshold={cal.threshold} source={cal.source} "
            f"metrics={cal.metrics}"
        )


if __name__ == "__main__":
    main()
