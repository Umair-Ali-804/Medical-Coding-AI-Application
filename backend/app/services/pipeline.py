"""Document pipeline orchestration with persistence and traceability.

upload -> text extraction/cleaning -> sections -> clinical entities (rules+LLM+ConText)
       -> hybrid RAG -> model proposals -> deterministic validation -> confidence & routing
       -> suggestions pending human review
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.coding.engine import CodingEngine, EngineResult
from app.coding.guideline_rules import RULES_VERSION
from app.coding.models import CodingModel, build_model
from app.coding.procedures import procedure_systems_for
from app.confidence.scorer import assess, load_calibration, retrieval_signal, validation_signal
from app.core.config import get_settings
from app.core.crypto import sha256_hex
from app.core.errors import Conflict, InvalidInput, NotFound
from app.core.metrics import PIPELINE_RUNS, PIPELINE_STAGE_LATENCY, SUGGESTIONS
from app.extraction.pipeline import ClinicalExtractor
from app.extraction.types import Entity
from app.ingestion.cleaning import clean_text
from app.ingestion.extractors import detect_mime, extract_text
from app.ingestion.sections import Section, detect_sections
from app.ingestion.storage import get_storage
from app.knowledge import guidelines
from app.knowledge.codes import PROCEDURE_SYSTEMS, normalize_code
from app.knowledge.repository import find_in_other_versions, get_snapshot
from app.models import (
    ClinicalEntity,
    CodingEvidence,
    CodingSuggestion,
    Document,
    DocumentSection,
    ModelRun,
)
from app.models.enums import DocumentStatus, ReviewRoute, SuggestionStatus
from app.rag.retriever import HybridRetriever
from app.services import audit
from app.services.principal import Principal
from app.validation.engine import Proposal, ValidationContext, ValidationEngine

log = logging.getLogger(__name__)
MAX_TEXT_CHARS = 500_000


@lru_cache
def get_coding_model() -> CodingModel:
    return build_model()


@lru_cache
def get_secondary_model() -> CodingModel | None:
    s = get_settings()
    if s.llm_provider == "openrouter" and s.llm_secondary_model:
        return build_model(model=s.llm_secondary_model)
    return None


# ---------------------------------------------------------------------------- ingest
def ingest_document(
    db: Session,
    actor: Principal,
    *,
    data: bytes,
    filename: str,
    declared_mime: str | None = None,
    external_id: str | None = None,
    source: str = "upload",
    encounter_type: str | None = None,
    patient_sex: str | None = None,
    patient_age: int | None = None,
) -> Document:
    s = get_settings()
    if not data:
        raise InvalidInput("Empty document")
    if len(data) > s.max_upload_mb * 1024 * 1024:
        from app.core.errors import TooLarge

        raise TooLarge(f"Document exceeds {s.max_upload_mb} MB")
    mime = detect_mime(data, filename, declared_mime)
    started = time.perf_counter()
    extracted = extract_text(data, mime)
    text = clean_text(extracted.text)
    if len(text) < 20:
        raise InvalidInput("Document contains too little text to code")
    if len(text) > MAX_TEXT_CHARS:
        raise InvalidInput(f"Document text exceeds {MAX_TEXT_CHARS} characters")
    PIPELINE_STAGE_LATENCY.labels("text_extraction").observe(time.perf_counter() - started)

    key = get_storage().put(data, suffix="." + filename.rsplit(".", 1)[-1][:8] if "." in filename else "")
    doc = Document(
        external_id=external_id,
        filename=filename[:300],
        mime_type=mime,
        size_bytes=len(data),
        sha256=sha256_hex(data),
        storage_key=key,
        source=source,
        status=DocumentStatus.TEXT_EXTRACTED,
        encounter_type=encounter_type or s.encounter_type_default,
        patient_sex=patient_sex,
        patient_age=patient_age,
        text=text,
        text_sha256=sha256_hex(text),
        page_count=extracted.page_count,
        extraction_method=extracted.method,
        uploaded_by=uuid.UUID(actor.id) if actor.is_user else None,
    )
    db.add(doc)
    db.flush()
    for sec in detect_sections(text):
        db.add(
            DocumentSection(
                document_id=doc.id,
                name=sec.name[:100],
                header=(sec.header or "")[:200] or None,
                start=sec.start,
                end=sec.end,
                order=sec.order,
            )
        )
    audit.record(
        db,
        actor,
        "document.uploaded",
        entity_type="document",
        entity_id=doc.id,
        document_id=doc.id,
        details={
            "filename": doc.filename,
            "mime": mime,
            "size": len(data),
            "sha256": doc.sha256,
            "method": extracted.method,
            "chars": len(text),
            "source": source,
        },
    )
    return doc


def get_document(db: Session, doc_id: uuid.UUID, *, for_update: bool = False) -> Document:
    stmt = select(Document).where(Document.id == doc_id)
    if for_update and db.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    doc = db.execute(stmt).scalar_one_or_none()
    if doc is None:
        raise NotFound("Document not found")
    return doc


def _sections(doc: Document) -> list[Section]:
    return [Section(s.name, s.header, s.start, s.end, s.order) for s in doc.sections]


def _entities(doc: Document) -> list[Entity]:
    return [
        Entity(
            text=e.text,
            category=e.category,
            start=e.start,
            end=e.end,
            normalized=e.normalized,
            section=e.section,
            negated=e.negated,
            uncertain=e.uncertain,
            historical=e.historical,
            family=e.family,
            laterality=e.laterality,
            severity=e.severity,
            temporal=e.temporal,
            source=e.source,
            assertion_conflict=e.assertion_conflict,
            confidence=e.confidence,
        )
        for e in doc.entities
    ]


def _guard_editable(doc: Document) -> None:
    if doc.status == DocumentStatus.COMPLETED:
        raise Conflict("Document is finalized; reopen it before re-processing")
    if not doc.text:
        raise InvalidInput("Document has no extracted text")


# ---------------------------------------------------------------------------- extraction
def run_extraction(db: Session, doc_id: uuid.UUID, actor: Principal) -> Document:
    doc = get_document(db, doc_id, for_update=True)
    _guard_editable(doc)
    s = get_settings()
    started = time.perf_counter()
    sections = _sections(doc) or detect_sections(doc.text or "")
    model = get_coding_model() if s.llm_provider == "openrouter" and s.llm_extraction_enabled else None
    try:
        result = ClinicalExtractor(llm_model=model).extract(doc.text or "", sections)
    except Exception:
        PIPELINE_RUNS.labels("extraction", "error").inc()
        raise

    run = ModelRun(
        document_id=doc.id,
        run_type="extraction",
        provider=result.provider,
        model=result.model,
        prompt_version=result.prompt_version,
        pipeline_version=s.pipeline_version,
        config={"llm_extraction": model is not None, "warnings": result.warnings[:20]},
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
        raw_response=result.raw_response,
    )
    db.add(run)
    db.flush()
    db.execute(update(CodingSuggestion).where(CodingSuggestion.document_id == doc.id).values(entity_id=None))
    doc.entities.clear()
    db.flush()
    for e in result.entities:
        doc.entities.append(
            ClinicalEntity(
                document_id=doc.id,
                model_run_id=run.id,
                text=e.text,
                normalized=e.normalized,
                category=e.category,
                start=e.start,
                end=e.end,
                section=e.section,
                negated=e.negated,
                uncertain=e.uncertain or e.hypothetical,
                historical=e.historical,
                family=e.family,
                laterality=e.laterality,
                severity=e.severity,
                temporal=e.temporal,
                source=e.source,
                assertion_conflict=e.assertion_conflict,
                confidence=e.confidence,
            )
        )
    doc.status = DocumentStatus.ENTITIES_EXTRACTED
    doc.error = None
    PIPELINE_STAGE_LATENCY.labels("clinical_extraction").observe(time.perf_counter() - started)
    PIPELINE_RUNS.labels("extraction", "ok").inc()
    audit.record(
        db,
        actor,
        "document.extracted",
        entity_type="model_run",
        entity_id=run.id,
        document_id=doc.id,
        details={
            "entities": len(result.entities),
            "provider": result.provider,
            "model": result.model,
            "negated": sum(e.negated for e in result.entities),
            "conflicts": sum(e.assertion_conflict for e in result.entities),
        },
    )
    db.flush()
    return doc


# ---------------------------------------------------------------------------- coding
@dataclass
class CodingSummary:
    document_id: uuid.UUID
    suggestions: int
    route: str
    by_route: dict[str, int]
    model_run_id: uuid.UUID


def run_coding(db: Session, doc_id: uuid.UUID, actor: Principal) -> CodingSummary:
    doc = get_document(db, doc_id, for_update=True)
    _guard_editable(doc)
    if doc.status in (DocumentStatus.UPLOADED, DocumentStatus.TEXT_EXTRACTED) or not doc.entities:
        run_extraction(db, doc_id, actor)
        db.refresh(doc)
    s = get_settings()
    started = time.perf_counter()
    model = get_coding_model()
    retriever = HybridRetriever(db, "ICD-10-CM")
    engine = CodingEngine(
        model,
        retriever,
        procedure_retrievers=procedure_retrievers(db, doc.encounter_type),
        guideline_lookup=_guideline_lookup(db, doc.encounter_type)
        if s.llm_provider == "openrouter"
        else None,
    )
    text, sections, entities = doc.text or "", _sections(doc), _entities(doc)
    try:
        result = engine.run(
            text,
            sections,
            entities,
            encounter_type=doc.encounter_type,
            patient_sex=doc.patient_sex,
            patient_age=doc.patient_age,
            systems=s.enabled_code_systems,
        )
    except Exception:
        PIPELINE_RUNS.labels("coding", "error").inc()
        raise

    agreement_codes = _secondary_codes(engine, result, text, sections, entities, doc)
    summary = persist_coding_result(db, doc, result, entities, actor, agreement_codes, started)
    return summary


def procedure_retrievers(db: Session, encounter_type: str) -> dict[str, HybridRetriever]:
    """Retrievers for the procedure code sets that are enabled AND loaded for this encounter type."""
    s = get_settings()
    available = [x for x in PROCEDURE_SYSTEMS if x in s.enabled_code_systems and get_snapshot(db, x)]
    return {x: HybridRetriever(db, x) for x in procedure_systems_for(encounter_type, available)}


def _guideline_lookup(db: Session, encounter_type: str):  # noqa: ANN202
    def lookup(query: str, code: str | None, kind: str) -> list[tuple[str, str]]:
        system = "ICD-10-PCS" if kind == "procedure" and encounter_type == "inpatient" else "ICD-10-CM"
        if kind == "procedure" and system != "ICD-10-PCS":
            return []
        hits = guidelines.guidelines_for(db, code or "", system, query, encounter_type, k=1)
        return [(h.ref, f"[{h.citation()}] {h.title}: {h.snippet(700)}") for h in hits]

    return lookup


def _secondary_codes(
    engine: CodingEngine,
    primary: EngineResult,
    text: str,
    sections: list[Section],
    entities: list[Entity],
    doc: Document,
) -> set[str] | None:
    sec = get_secondary_model()
    if sec is None:
        return None
    try:
        from app.coding.models import CodingRequest

        req = CodingRequest(
            text=text,
            groups=primary.groups,
            encounter_type=doc.encounter_type,
            patient_sex=doc.patient_sex,
            patient_age=doc.patient_age,
            systems=["ICD-10-CM"],
            kb_label=f"ICD-10-CM {primary.kb_version}",
        )
        out = sec.propose(req)
        return {normalize_code(c.code) for c in out.codes}
    except Exception as exc:  # noqa: BLE001 - agreement is optional
        log.warning("secondary_model_failed", extra={"error": str(exc)[:200]})
        return None


def _reference_warnings(db: Session) -> list[str]:
    """Code sets that are not valid today (coding/billing must use the release in force)."""
    from datetime import date

    from app.knowledge.loader_service import freshness_warnings

    try:
        return freshness_warnings(db, date.today())[:10]
    except Exception:  # never block coding on the check itself  # noqa: BLE001
        log.exception("reference_freshness_check_failed")
        return []


def persist_coding_result(
    db: Session,
    doc: Document,
    result: EngineResult,
    entities: list[Entity],
    actor: Principal,
    agreement_codes: set[str] | None,
    started: float,
) -> CodingSummary:
    s = get_settings()
    out = result.output
    snapshots = {
        sys: get_snapshot(db, sys)
        for sys in ("ICD-10-CM", "HCPCS", "CPT", "ICD-10-PCS")
        if sys in s.enabled_code_systems
    }
    proposals = [
        Proposal(
            code=c.code,
            system=c.code_system,
            evidence=c.evidence,
            entity_text=c.entity,
            rationale=c.rationale,
            llm_confidence=c.confidence,
            from_candidates=c.from_candidates,
            llm_description=c.description,
        )
        for c in out.codes
    ]
    other_versions = {}
    for p in proposals:
        snap = snapshots.get(p.system)
        code = normalize_code(p.code, p.system)
        if snap and snap.get(code) is None:
            other_versions[(p.system, code)] = find_in_other_versions(db, p.system, code, snap.version)
    vctx = ValidationContext(
        text=doc.text or "",
        entities=entities,
        snapshots=snapshots,
        encounter_type=doc.encounter_type,
        patient_sex=doc.patient_sex,
        patient_age=doc.patient_age,
        other_versions=other_versions,
    )
    validated = ValidationEngine().validate(proposals, vctx)

    run = ModelRun(
        document_id=doc.id,
        run_type="coding",
        provider=out.provider,
        model=out.model,
        prompt_version=out.prompt_version,
        retrieval_version=result.retrieval_version,
        kb_version=result.kb_version,
        pipeline_version=s.pipeline_version,
        config={
            "mode": result.mode,
            "top_k": s.retrieval_top_k,
            "temperature": s.llm_temperature,
            "secondary_model": s.llm_secondary_model,
            "groups": len(result.groups),
            "retrieval_ms": result.retrieval_ms,
            "not_coded": [{"entity": n.entity[:120], "reason": n.reason[:300]} for n in out.not_coded][:50],
            # every automatic guideline correction (what changed, why, which guideline) for audit
            "guideline_rules_version": RULES_VERSION,
            "guideline_rule_changes": [c.as_dict() for c in result.rule_changes][:50],
        },
        input_tokens=out.input_tokens,
        output_tokens=out.output_tokens,
        cost_usd=out.cost_usd,
        latency_ms=int((time.perf_counter() - started) * 1000),
        raw_response=out.raw_text,
    )
    db.add(run)
    db.flush()

    # supersede earlier, still-pending AI suggestions
    db.execute(
        update(CodingSuggestion)
        .where(
            CodingSuggestion.document_id == doc.id,
            CodingSuggestion.status == SuggestionStatus.PENDING,
            CodingSuggestion.source == "ai",
        )
        .values(status=SuggestionStatus.SUPERSEDED)
    )

    cal = load_calibration()
    entity_rows = {(e.start, e.end): e for e in doc.entities}
    by_route: dict[str, int] = {}
    for v in validated:
        rc = result.candidates_by_code.get(v.code)
        agreement = (
            None
            if agreement_codes is None
            else (
                1.0
                if v.code in agreement_codes
                else 0.5
                if any(c[:3] == v.code[:3] for c in agreement_codes)
                else 0.0
            )
        )
        n_flags = sum(1 for i in v.issues if i.severity == "flag")
        signals = {
            "evidence": v.best_evidence_score,
            "retrieval": retrieval_signal(rc.score if rc else None, rc.rank if rc else None),
            "validation": validation_signal(v.status, n_flags),
            "entity": v.entity_similarity if (v.linked_entity and v.linked_entity.affirmed) else 0.0,
            "agreement": agreement,
            "llm": v.proposal.llm_confidence,
        }
        conf = assess(signals, v.status, cal)
        route = conf.route
        if v.proposal.system in PROCEDURE_SYSTEMS and route == ReviewRoute.STANDARD:
            route = ReviewRoute.MANDATORY  # procedure coding has no calibration data yet: always reviewed
        cites = [
            {"ref": h.ref, "title": h.title, "page": h.page, "citation": h.citation(), "snippet": h.snippet()}
            for h in guidelines.guidelines_for(
                db,
                v.code,
                v.proposal.system,
                " ".join(filter(None, [v.proposal.entity_text, v.info.description if v.info else ""])),
                doc.encounter_type,
                k=2,
            )
        ]
        linked_row = (
            entity_rows.get((v.linked_entity.start, v.linked_entity.end)) if v.linked_entity else None
        )
        sug = CodingSuggestion(
            document_id=doc.id,
            model_run_id=run.id,
            entity_id=linked_row.id if linked_row else None,
            code_system=v.proposal.system,
            code=v.code,
            description=v.info.description if v.info else (v.proposal.llm_description or "(unknown code)"),
            kb_version=v.info.version if v.info else None,
            entity_text=v.proposal.entity_text or None,
            rationale=v.proposal.rationale or None,
            sequence=v.proposal.sequence,
            source="ai",
            llm_confidence=v.proposal.llm_confidence,
            confidence=conf.score,
            confidence_breakdown=conf.breakdown(cal),
            retrieval_score=rc.score if rc else None,
            retrieval_rank=rc.rank if rc else None,
            validation_status=v.status,
            validation_issues=v.issue_dicts(),
            guidelines=cites or None,
            review_route=route,
            status=SuggestionStatus.PENDING,
        )
        for ev in v.evidence:
            sug.evidence.append(
                CodingEvidence(
                    quote=ev.quote[:2000],
                    start=ev.start,
                    end=ev.end,
                    match_score=ev.score,
                )
            )
        db.add(sug)
        by_route[route] = by_route.get(route, 0) + 1
        SUGGESTIONS.labels(v.status, route).inc()

    doc_route = ReviewRoute.STANDARD
    if not validated or by_route.get(ReviewRoute.MANDATORY) or by_route.get(ReviewRoute.SYSTEM_REJECTED):
        doc_route = ReviewRoute.MANDATORY
    # Expired reference data (e.g. FY2026 ICD-10-CM after 2026-09-30): never let a chart pass as "standard".
    stale = _reference_warnings(db)
    if stale:
        doc_route = ReviewRoute.MANDATORY
        run.config = {**(run.config or {}), "reference_data_warnings": stale}
    doc.review_route = doc_route
    doc.status = DocumentStatus.CODED
    doc.error = None
    db.flush()
    PIPELINE_STAGE_LATENCY.labels("coding").observe(time.perf_counter() - started)
    PIPELINE_RUNS.labels("coding", "ok").inc()
    audit.record(
        db,
        actor,
        "document.coded",
        entity_type="model_run",
        entity_id=run.id,
        document_id=doc.id,
        details={
            "suggestions": len(validated),
            "route": doc_route,
            "by_route": by_route,
            "model": out.model,
            "prompt_version": out.prompt_version,
            "retrieval_version": result.retrieval_version,
            "kb_version": result.kb_version,
            "cost_usd": round(out.cost_usd, 6),
        },
    )
    return CodingSummary(doc.id, len(validated), doc_route, by_route, run.id)


def mark_failed(doc_id: uuid.UUID, actor: Principal, stage: str, exc: BaseException) -> None:
    """Record a failure in its own transaction (the caller's transaction has been rolled back)."""
    from app.db.session import session_scope

    try:
        with session_scope() as db2:
            doc = db2.get(Document, doc_id)
            if doc is None:
                return
            doc.status = DocumentStatus.FAILED
            doc.error = f"{stage} failed: {type(exc).__name__}: {str(exc)[:400]}"
            db2.add(
                ModelRun(
                    document_id=doc.id,
                    run_type=stage,
                    provider="-",
                    model="-",
                    pipeline_version=get_settings().pipeline_version,
                    status="failed",
                    error=str(exc)[:2000],
                )
            )
            audit.record(
                db2,
                actor,
                f"document.{stage}_failed",
                entity_type="document",
                entity_id=doc.id,
                document_id=doc.id,
                details={"error_type": type(exc).__name__},
            )
    except Exception:  # noqa: BLE001
        log.exception("mark_failed_error")


def process_document(db: Session, doc_id: uuid.UUID, actor: Principal) -> CodingSummary:
    run_extraction(db, doc_id, actor)
    return run_coding(db, doc_id, actor)
