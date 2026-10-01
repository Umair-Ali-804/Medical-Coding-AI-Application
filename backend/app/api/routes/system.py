"""Knowledge base, stats, jobs, audit verification, health and metrics."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import AdminOnly, AuditReader, Reader
from app.core.config import get_settings
from app.core.errors import NotFound
from app.db.session import get_db
from app.knowledge.codes import normalize_code
from app.knowledge.repository import get_snapshot
from app.models import AuditLog, CodingSuggestion, Document, KnowledgeBaseVersion, ModelRun, Review
from app.models.enums import DocumentStatus, ReviewAction, ReviewRoute, SuggestionStatus
from app.rag.lexical import get_bm25
from app.rag.vector_store import get_vector_store
from app.schemas.api import AuditOut, CodeOut, CodeSearchHit, JobOut, KBVersionOut, StatsOut
from app.services import audit, jobs
from app.services.principal import Principal

router = APIRouter()


# ------------------------------------------------------------------ knowledge base
@router.get("/knowledge/versions", response_model=list[KBVersionOut], tags=["knowledge"])
def kb_versions(db: Session = Depends(get_db), _: Principal = Depends(Reader)) -> list:
    return list(
        db.execute(select(KnowledgeBaseVersion).order_by(KnowledgeBaseVersion.loaded_at.desc())).scalars()
    )


@router.get("/knowledge/search", response_model=list[CodeSearchHit], tags=["knowledge"])
def kb_search(
    q: str = Query(min_length=2, max_length=200),
    system: str = "ICD-10-CM",
    limit: int = Query(default=15, ge=1, le=50),
    db: Session = Depends(get_db),
    _: Principal = Depends(Reader),
) -> list[CodeSearchHit]:
    snap = get_snapshot(db, system)
    if snap is None:
        raise NotFound(f"No active {system} knowledge base")
    hits: list[CodeSearchHit] = []
    direct = snap.get(q.strip())
    if direct:
        hits.append(
            CodeSearchHit(
                code=direct.code, description=direct.description, billable=direct.billable, score=99.0
            )
        )
    for code, score in get_bm25(snap).search(q, limit):
        info = snap.codes[code]
        if not direct or code != direct.code:
            hits.append(
                CodeSearchHit(
                    code=code, description=info.description, billable=info.billable, score=round(score, 3)
                )
            )
    return hits[:limit]


@router.get("/knowledge/codes/{system}/{code}", response_model=CodeOut, tags=["knowledge"])
def kb_code(system: str, code: str, db: Session = Depends(get_db), _: Principal = Depends(Reader)) -> CodeOut:
    snap = get_snapshot(db, system)
    info = snap.get(normalize_code(code, system)) if snap else None
    if info is None or snap is None:
        raise NotFound(f"{code} not found in {system}")
    inherited = [f"{owner}: {n}" for owner, n in snap.inherited(info.code, "excludes1") if owner != info.code]
    return CodeOut(
        code_system=system,
        version=info.version,
        code=info.code,
        description=info.description,
        billable=info.billable,
        parent_code=info.parent_code,
        chapter=info.chapter,
        chapter_description=info.chapter_description,
        section=info.section,
        section_description=info.section_description,
        inclusion_terms=list(info.inclusion_terms),
        includes=list(info.includes),
        excludes1=list(info.excludes1),
        excludes2=list(info.excludes2),
        code_first=list(info.code_first),
        use_additional_code=list(info.use_additional_code),
        code_also=list(info.code_also),
        inherited_excludes1=inherited,
    )


# ------------------------------------------------------------------ jobs
@router.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(Reader)):  # noqa: ANN201
    return jobs.get(db, job_id)


# ------------------------------------------------------------------ stats
@router.get("/stats", response_model=StatsOut, tags=["stats"])
def stats(db: Session = Depends(get_db), _: Principal = Depends(Reader)) -> StatsOut:
    docs = dict(db.execute(select(Document.status, func.count()).group_by(Document.status)).all())
    pending = sum(docs.get(s, 0) for s in (DocumentStatus.CODED, DocumentStatus.IN_REVIEW))
    mandatory = db.execute(
        select(func.count())
        .select_from(Document)
        .where(
            Document.status.in_([DocumentStatus.CODED, DocumentStatus.IN_REVIEW]),
            Document.review_route == ReviewRoute.MANDATORY,
        )
    ).scalar_one()
    sugg = dict(
        db.execute(
            select(CodingSuggestion.status, func.count())
            .where(CodingSuggestion.source == "ai")
            .group_by(CodingSuggestion.status)
        ).all()
    )
    reviews = dict(db.execute(select(Review.action, func.count()).group_by(Review.action)).all())
    approved = reviews.get(ReviewAction.APPROVE, 0)
    rejected = reviews.get(ReviewAction.REJECT, 0)
    edited = reviews.get(ReviewAction.EDIT, 0)
    added = reviews.get(ReviewAction.ADD, 0)
    decided = approved + rejected + edited
    errors = dict(
        db.execute(
            select(Review.error_category, func.count())
            .where(Review.error_category.is_not(None))
            .group_by(Review.error_category)
        ).all()
    )

    def avg_conf(status: str) -> float | None:
        v = db.execute(
            select(func.avg(CodingSuggestion.confidence)).where(
                CodingSuggestion.status == status, CodingSuggestion.source == "ai"
            )
        ).scalar_one()
        return round(float(v), 4) if v is not None else None

    cost = db.execute(select(func.coalesce(func.sum(ModelRun.cost_usd), 0.0))).scalar_one()
    lat = db.execute(
        select(func.avg(ModelRun.latency_ms)).where(
            ModelRun.run_type == "coding", ModelRun.status == "succeeded"
        )
    ).scalar_one()
    return StatsOut(
        documents_by_status={str(k): v for k, v in docs.items()},
        pending_review=pending,
        mandatory_review=mandatory,
        suggestions_by_status={str(k): v for k, v in sugg.items()},
        reviews_by_action={str(k): v for k, v in reviews.items()},
        acceptance_rate=round(approved / decided, 4) if decided else None,
        correction_rate=round((edited + rejected + added) / (decided + added), 4)
        if (decided + added)
        else None,
        ai_precision=round(approved / decided, 4) if decided else None,
        error_categories={str(k): v for k, v in errors.items()},
        avg_confidence_approved=avg_conf(SuggestionStatus.APPROVED),
        avg_confidence_rejected=avg_conf(SuggestionStatus.REJECTED),
        llm_cost_usd_total=round(float(cost or 0.0), 4),
        avg_coding_latency_ms=round(float(lat), 1) if lat is not None else None,
    )


# ------------------------------------------------------------------ audit
@router.get("/audit", response_model=list[AuditOut], tags=["audit"])
def audit_log(
    action: str | None = None,
    actor_id: str | None = None,
    limit: int = Query(default=100, le=1000),
    before_id: int | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(AuditReader),
) -> list:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if before_id:
        stmt = stmt.where(AuditLog.id < before_id)
    return list(db.execute(stmt.order_by(AuditLog.id.desc()).limit(limit)).scalars())


@router.get("/audit/verify", tags=["audit"])
def audit_verify(db: Session = Depends(get_db), actor: Principal = Depends(AdminOnly)) -> dict:
    result = audit.verify_chain(db)
    audit.record(db, actor, "audit.verified", details=result)
    db.commit()
    return result


# ------------------------------------------------------------------ health & metrics
@router.get("/health/live", tags=["health"])
def live() -> dict:
    return {"status": "ok"}


@router.get("/health/ready", tags=["health"])
def ready(db: Session = Depends(get_db)) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        checks["database"] = "error"
    kb = (
        db.execute(
            select(func.count())
            .select_from(KnowledgeBaseVersion)
            .where(KnowledgeBaseVersion.active.is_(True))
        ).scalar_one()
        if checks["database"] == "ok"
        else 0
    )
    checks["knowledge_base"] = "ok" if kb else "missing"
    s = get_settings()
    if s.vector_store == "qdrant":
        checks["vector_store"] = "ok" if get_vector_store().healthy() else "error"
    checks["llm"] = s.llm_provider
    if checks["database"] == "ok":
        from datetime import date

        from app.knowledge.loader_service import freshness_warnings

        stale = freshness_warnings(db, date.today())
        # informational: expired code sets do not stop the service, but every chart goes to mandatory review
        checks["reference_data"] = "ok" if not stale else "expired: " + "; ".join(stale)[:500]
    healthy = (
        checks["database"] == "ok"
        and checks["knowledge_base"] == "ok"
        and checks.get("vector_store") != "error"
    )
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks}, status_code=200 if healthy else 503
    )


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
