"""Human-in-the-loop review. The coder is always the final decision-maker."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.core.metrics import REVIEW_ACTIONS
from app.db.base import utcnow
from app.knowledge.codes import looks_valid, normalize_code
from app.knowledge.repository import get_snapshot
from app.models import CodingEvidence, CodingSuggestion, Document, FinalCode, Review
from app.models.enums import (
    DocumentStatus,
    ErrorCategory,
    ReviewAction,
    ReviewRoute,
    Role,
    SuggestionStatus,
    ValidationStatus,
)
from app.services import audit
from app.services.principal import Principal

REVIEWABLE = {
    SuggestionStatus.PENDING,
    SuggestionStatus.APPROVED,
    SuggestionStatus.REJECTED,
    SuggestionStatus.EDITED,
}


def _require_coder(actor: Principal) -> uuid.UUID:
    if not actor.is_user or actor.role not in (Role.CODER, Role.ADMIN):
        raise Forbidden("Only human coders can review codes")
    return uuid.UUID(actor.id)


def _suggestion(db: Session, sid: uuid.UUID) -> tuple[CodingSuggestion, Document]:
    sug = db.get(CodingSuggestion, sid)
    if sug is None:
        raise NotFound("Suggestion not found")
    doc = db.get(Document, sug.document_id)
    assert doc is not None
    if doc.status == DocumentStatus.COMPLETED:
        raise Conflict("Document is finalized; reopen it to change codes")
    if sug.status not in REVIEWABLE:
        raise Conflict(f"Suggestion is {sug.status} and cannot be reviewed")
    return sug, doc


def _check_code(db: Session, system: str, code: str) -> tuple[str, str, str]:
    code = normalize_code(code, system)
    if not looks_valid(code, system):
        raise InvalidInput(f"'{code}' is not a valid {system} code format")
    snap = get_snapshot(db, system)
    if snap is None:
        raise InvalidInput(f"{system} reference data is not loaded")
    info = snap.get(code)
    if info is None:
        raise InvalidInput(f"{code} does not exist in {system} {snap.version}")
    if not info.billable:
        kids = ", ".join(k.code for k in snap.billable_descendants(code, 6))
        raise InvalidInput(f"{code} is not billable; choose a more specific code ({kids})")
    return code, info.description, snap.version


def _error_category(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return ErrorCategory(value).value
    except ValueError:
        raise InvalidInput(f"Unknown error category '{value}'")


def _mark_in_review(doc: Document) -> None:
    if doc.status == DocumentStatus.CODED:
        doc.status = DocumentStatus.IN_REVIEW


def approve(db: Session, actor: Principal, sid: uuid.UUID, comment: str | None = None) -> CodingSuggestion:
    uid = _require_coder(actor)
    sug, doc = _suggestion(db, sid)
    if sug.validation_status == ValidationStatus.REJECTED:
        raise Conflict("This code failed hard validation; use Edit to choose a valid code or Reject it")
    sug.status, sug.final_code, sug.final_description = SuggestionStatus.APPROVED, sug.code, sug.description
    sug.reviewed_by, sug.reviewed_at = uid, utcnow()
    db.add(
        Review(
            document_id=doc.id,
            suggestion_id=sug.id,
            reviewer_id=uid,
            action=ReviewAction.APPROVE,
            original_code=sug.code,
            final_code=sug.code,
            original_confidence=sug.confidence,
            reason=comment,
        )
    )
    _mark_in_review(doc)
    audit.record(
        db,
        actor,
        "suggestion.approved",
        entity_type="suggestion",
        entity_id=sug.id,
        document_id=doc.id,
        details={"code": sug.code, "confidence": sug.confidence, "route": sug.review_route},
    )
    REVIEW_ACTIONS.labels("approve").inc()
    return sug


def reject(
    db: Session, actor: Principal, sid: uuid.UUID, reason: str, error_category: str | None
) -> CodingSuggestion:
    uid = _require_coder(actor)
    if not reason or not reason.strip():
        raise InvalidInput("A reason is required to reject a code")
    sug, doc = _suggestion(db, sid)
    cat = _error_category(error_category) or ErrorCategory.EXTRA_CODE.value
    sug.status, sug.final_code, sug.final_description = SuggestionStatus.REJECTED, None, None
    sug.reviewed_by, sug.reviewed_at = uid, utcnow()
    db.add(
        Review(
            document_id=doc.id,
            suggestion_id=sug.id,
            reviewer_id=uid,
            action=ReviewAction.REJECT,
            original_code=sug.code,
            final_code=None,
            original_confidence=sug.confidence,
            reason=reason,
            error_category=cat,
        )
    )
    _mark_in_review(doc)
    audit.record(
        db,
        actor,
        "suggestion.rejected",
        entity_type="suggestion",
        entity_id=sug.id,
        document_id=doc.id,
        details={"code": sug.code, "error_category": cat, "confidence": sug.confidence},
    )
    REVIEW_ACTIONS.labels("reject").inc()
    return sug


def edit(
    db: Session, actor: Principal, sid: uuid.UUID, new_code: str, reason: str, error_category: str | None
) -> CodingSuggestion:
    uid = _require_coder(actor)
    if not reason or not reason.strip():
        raise InvalidInput("A reason is required to change a code")
    sug, doc = _suggestion(db, sid)
    code, desc, _version = _check_code(db, sug.code_system, new_code)
    cat = _error_category(error_category) or (
        ErrorCategory.WRONG_SPECIFICITY.value if code[:3] == sug.code[:3] else ErrorCategory.WRONG_CODE.value
    )
    sug.status, sug.final_code, sug.final_description = SuggestionStatus.EDITED, code, desc
    sug.reviewed_by, sug.reviewed_at = uid, utcnow()
    db.add(
        Review(
            document_id=doc.id,
            suggestion_id=sug.id,
            reviewer_id=uid,
            action=ReviewAction.EDIT,
            original_code=sug.code,
            final_code=code,
            original_confidence=sug.confidence,
            reason=reason,
            error_category=cat,
        )
    )
    _mark_in_review(doc)
    audit.record(
        db,
        actor,
        "suggestion.edited",
        entity_type="suggestion",
        entity_id=sug.id,
        document_id=doc.id,
        details={"from": sug.code, "to": code, "error_category": cat},
    )
    REVIEW_ACTIONS.labels("edit").inc()
    return sug


def add_code(
    db: Session,
    actor: Principal,
    doc_id: uuid.UUID,
    system: str,
    code: str,
    reason: str,
    evidence: str | None = None,
) -> CodingSuggestion:
    """Coder adds a code the AI missed (recorded as a missing-code error for analysis)."""
    uid = _require_coder(actor)
    doc = db.get(Document, doc_id)
    if doc is None:
        raise NotFound("Document not found")
    if doc.status == DocumentStatus.COMPLETED:
        raise Conflict("Document is finalized; reopen it to change codes")
    code, desc, version = _check_code(db, system, code)
    dup = db.execute(
        select(CodingSuggestion).where(
            CodingSuggestion.document_id == doc.id,
            CodingSuggestion.code == code,
            CodingSuggestion.status.in_([SuggestionStatus.APPROVED, SuggestionStatus.EDITED]),
        )
    ).scalar_one_or_none()
    if dup:
        raise Conflict(f"{code} is already approved for this document")
    seq = max((s.sequence for s in doc.suggestions), default=0) + 1
    sug = CodingSuggestion(
        document_id=doc.id,
        code_system=system,
        code=code,
        description=desc,
        kb_version=version,
        sequence=seq,
        source="manual",
        confidence=1.0,
        validation_status=ValidationStatus.PASSED,
        validation_issues=[],
        review_route=ReviewRoute.STANDARD,
        status=SuggestionStatus.APPROVED,
        final_code=code,
        final_description=desc,
        reviewed_by=uid,
        reviewed_at=utcnow(),
        rationale=reason,
    )
    if evidence:
        idx = (doc.text or "").find(evidence)
        sug.evidence.append(
            CodingEvidence(
                quote=evidence[:2000],
                start=idx if idx >= 0 else None,
                end=idx + len(evidence) if idx >= 0 else None,
                match_score=1.0 if idx >= 0 else 0.0,
            )
        )
    db.add(sug)
    db.flush()
    db.add(
        Review(
            document_id=doc.id,
            suggestion_id=sug.id,
            reviewer_id=uid,
            action=ReviewAction.ADD,
            original_code=None,
            final_code=code,
            reason=reason,
            error_category=ErrorCategory.MISSING_CODE,
        )
    )
    _mark_in_review(doc)
    audit.record(
        db,
        actor,
        "suggestion.added",
        entity_type="suggestion",
        entity_id=sug.id,
        document_id=doc.id,
        details={"code": code, "system": system},
    )
    REVIEW_ACTIONS.labels("add").inc()
    return sug


def finalize(db: Session, actor: Principal, doc_id: uuid.UUID) -> Document:
    uid = _require_coder(actor)
    doc = db.get(Document, doc_id)
    if doc is None:
        raise NotFound("Document not found")
    if doc.status == DocumentStatus.COMPLETED:
        raise Conflict("Document already finalized")
    active = [s for s in doc.suggestions if s.status != SuggestionStatus.SUPERSEDED]
    pending = [s for s in active if s.status == SuggestionStatus.PENDING]
    if pending:
        raise Conflict(
            f"{len(pending)} suggestion(s) still pending review",
            details={"pending": [str(s.id) for s in pending]},
        )
    accepted = [s for s in active if s.status in (SuggestionStatus.APPROVED, SuggestionStatus.EDITED)]
    if not accepted:
        raise Conflict("No approved codes; add at least one code before finalizing")
    doc.final_codes.clear()
    db.flush()
    seen: set[tuple[str, str]] = set()
    for seq, s in enumerate(sorted(accepted, key=lambda x: x.sequence), start=1):
        key = (s.code_system, s.final_code or s.code)
        if key in seen:
            continue
        seen.add(key)
        doc.final_codes.append(
            FinalCode(
                document_id=doc.id,
                suggestion_id=s.id,
                code_system=s.code_system,
                code=s.final_code or s.code,
                description=s.final_description or s.description,
                sequence=seq,
                kb_version=s.kb_version,
                ai_suggested=s.source == "ai",
                ai_modified=s.status == SuggestionStatus.EDITED,
                approved_by=uid,
            )
        )
    doc.status, doc.finalized_by, doc.finalized_at = DocumentStatus.COMPLETED, uid, utcnow()
    db.add(Review(document_id=doc.id, reviewer_id=uid, action=ReviewAction.FINALIZE))
    audit.record(
        db,
        actor,
        "document.finalized",
        entity_type="document",
        entity_id=doc.id,
        document_id=doc.id,
        details={"final_codes": [c.code for c in doc.final_codes]},
    )
    REVIEW_ACTIONS.labels("finalize").inc()
    db.flush()
    return doc


def reopen(db: Session, actor: Principal, doc_id: uuid.UUID, reason: str) -> Document:
    uid = _require_coder(actor)
    if actor.role != Role.ADMIN:
        raise Forbidden("Only administrators can reopen finalized documents")
    doc = db.get(Document, doc_id)
    if doc is None:
        raise NotFound("Document not found")
    if doc.status != DocumentStatus.COMPLETED:
        raise Conflict("Only finalized documents can be reopened")
    doc.status, doc.finalized_at, doc.finalized_by = DocumentStatus.IN_REVIEW, None, None
    db.add(Review(document_id=doc.id, reviewer_id=uid, action=ReviewAction.REOPEN, reason=reason))
    audit.record(
        db,
        actor,
        "document.reopened",
        entity_type="document",
        entity_id=doc.id,
        document_id=doc.id,
        details={"reason": reason[:500]},
    )
    return doc
