"""Single-call workflows for notebooks, the Gradio demo and the CLI.

`code_note` runs the production pipeline (ingest -> NLP -> RAG -> coder -> validation -> confidence ->
guideline citations), then drafts the claim from the suggested codes and scrubs it for denial risks.
It uses the same services and database as the API, so results are stored and auditable.
"""

from __future__ import annotations

from datetime import date

from app.claims.scrubber import Claim, ClaimScrubber, ScrubResult, claim_from_suggestions
from app.db.session import session_scope
from app.models import ModelRun
from app.models.enums import SuggestionStatus
from app.services import pipeline
from app.services.principal import SYSTEM


def code_note(
    text: str,
    *,
    encounter_type: str = "outpatient",
    patient_sex: str | None = None,
    patient_age: int | None = None,
    dos: str | date | None = None,
    filename: str = "note.txt",
) -> dict:
    dos_d = date.fromisoformat(dos) if isinstance(dos, str) and dos else (dos or date.today())
    with session_scope() as db:
        doc = pipeline.ingest_document(
            db,
            SYSTEM,
            data=text.encode("utf-8"),
            filename=filename,
            source="demo",
            encounter_type=encounter_type,
            patient_sex=patient_sex,
            patient_age=patient_age,
        )
        summary = pipeline.process_document(db, doc.id, SYSTEM)
        db.refresh(doc)
        entities = [
            {
                "text": e.text,
                "category": e.category,
                "section": e.section,
                "status": "negated"
                if e.negated
                else "family"
                if e.family
                else "uncertain"
                if e.uncertain
                else "historical"
                if e.historical
                else "affirmed",
                "source": e.source,
            }
            for e in sorted(doc.entities, key=lambda x: x.start)
        ]
        sugs = [
            {
                "sequence": s.sequence,
                "code_system": s.code_system,
                "code": s.code,
                "description": s.description,
                "entity": s.entity_text,
                "confidence": round(s.confidence, 3),
                "validation": s.validation_status,
                "route": s.review_route,
                "issues": [i["message"] for i in (s.validation_issues or []) if i.get("severity") != "info"],
                "hints": [i["message"] for i in (s.validation_issues or []) if i.get("severity") == "info"],
                "evidence": [ev.quote for ev in s.evidence],
                "guidelines": s.guidelines or [],
            }
            for s in sorted(doc.suggestions, key=lambda x: (x.code_system != "ICD-10-CM", x.sequence))
            if s.status == SuggestionStatus.PENDING
        ]
        run = db.get(ModelRun, summary.model_run_id)
        not_coded = (run.config or {}).get("not_coded", []) if run else []
        usable = [s for s in sugs if s["validation"] != "rejected"]
        claim = claim_from_suggestions(
            usable,
            date_of_service=dos_d,
            encounter_type=encounter_type,
            patient_sex=patient_sex,
            patient_age=patient_age,
        )
        scrub = ClaimScrubber(db).scrub(claim)
        doc_id = str(doc.id)
    return {
        "document_id": doc_id,
        "route": summary.route,
        "entities": entities,
        "suggestions": sugs,
        "not_coded": not_coded,
        "claim": claim,
        "scrub": scrub,
        "report": render_report(sugs, scrub, entities, summary.route, not_coded, claim),
    }


def scrub_claim(data: dict) -> ScrubResult:
    with session_scope() as db:
        return ClaimScrubber(db).scrub(Claim.from_dict(data))


def render_report(
    sugs: list[dict],
    scrub: ScrubResult,
    entities: list[dict],
    route: str,
    not_coded: list[dict] | None = None,
    claim: Claim | None = None,
) -> str:
    lines = [f"Review route: {route.upper()}", "", "SUGGESTED CODES"]
    for s in sugs:
        lines.append(
            f"  {s['code_system']:10} {s['code']:9} {s['description'][:70]:70} conf={s['confidence']:.2f} "
            f"[{s['validation']}/{s['route']}]"
        )
        for i in s["issues"]:
            lines.append(f"      ! {i}")
        for g in s["guidelines"][:1]:
            lines.append(f"      guideline {g['citation']}: {g['snippet'][:160]}")
    neg = [e["text"] for e in entities if e["status"] in ("negated", "family", "uncertain")]
    if neg:
        lines += ["", "NOT CODED (negated / family / uncertain): " + "; ".join(neg[:12])]
    proc_nc = [n for n in (not_coded or []) if n.get("reason", "").startswith("procedure")]
    if proc_nc:
        lines += ["", "PROCEDURES NEEDING MANUAL CODING"]
        lines += [f"  - {n['entity']}: {n['reason']}" for n in proc_nc]
    if claim is not None:
        lines += [
            "",
            f"DRAFT CLAIM ({claim.claim_type}, DOS {claim.date_of_service}): Dx "
            + ", ".join(claim.diagnoses),
        ]
        for n, ln in enumerate(claim.lines, start=1):
            lines.append(f"  line {n}: {ln.system} {ln.code} x{ln.units:g}  {(ln.description or '')[:60]}")
    lines += ["", "CLAIM CHECK: " + scrub.summary()]
    for i in scrub.issues:
        where = f"line {i.line}" if i.line else "claim"
        carc = f" [CARC {i.carc}]" if i.carc else ""
        lines.append(f"  {i.severity.upper():6} {where:7} {i.message}{carc}")
    for n in scrub.not_checked:
        lines.append(f"  not checked: {n}")
    return "\n".join(lines)
