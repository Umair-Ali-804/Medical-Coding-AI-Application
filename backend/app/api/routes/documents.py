from __future__ import annotations

import csv
import io
import uuid
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import AuditReader, Reader, Uploader
from app.core.config import get_settings
from app.core.errors import AppError, Conflict, InvalidInput, TooLarge
from app.db.session import get_db
from app.models import AuditLog, CodingSuggestion, Document, ModelRun, Review
from app.models.enums import DocumentStatus, JobType, SuggestionStatus
from app.schemas.api import (
    AddCodeIn,
    AuditOut,
    DocumentDetail,
    DocumentList,
    DocumentSummary,
    FinalCodeOut,
    ModelRunOut,
    ProcessOut,
    ReopenIn,
    ReviewOut,
    SuggestionOut,
    TextDocumentIn,
)
from app.services import audit, jobs, pipeline, review
from app.services.principal import Principal
from app.services.webhooks import send_event

router = APIRouter(prefix="/documents", tags=["documents"])


def _validate_callback(url: str | None) -> str | None:
    if not url:
        return None
    allowed = get_settings().callback_allowed_hosts
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme not in ("http", "https") or host not in allowed:
        raise InvalidInput("callback_url host is not in CALLBACK_ALLOWED_HOSTS")
    return url


def _queue(
    db: Session, doc: Document, actor: Principal, job_type: JobType, callback: str | None
) -> ProcessOut:
    job = jobs.enqueue(db, job_type, doc.id, created_by=actor.id, callback_url=callback)
    doc.status = DocumentStatus.PROCESSING if job_type != JobType.EXTRACT else doc.status
    audit.record(
        db,
        actor,
        "job.queued",
        entity_type="job",
        entity_id=job.id,
        document_id=doc.id,
        details={"job_type": job_type},
    )
    db.commit()
    return ProcessOut(document_id=doc.id, job_id=job.id, status="queued")


@router.post(
    "", response_model=ProcessOut, status_code=201, summary="Upload a clinical document (PDF, DOCX, TXT)"
)
async def upload_document(
    file: UploadFile = File(...),
    external_id: str | None = Form(default=None, max_length=200),
    encounter_type: Literal["outpatient", "inpatient"] | None = Form(default=None),
    patient_sex: Literal["M", "F", "U"] | None = Form(default=None),
    patient_age: int | None = Form(default=None, ge=0, le=125),
    process: bool = Form(default=False),
    callback_url: str | None = Form(default=None),
    db: Session = Depends(get_db),
    actor: Principal = Depends(Uploader),
) -> ProcessOut:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise TooLarge(f"File exceeds {get_settings().max_upload_mb} MB")
    callback = _validate_callback(callback_url)
    doc = pipeline.ingest_document(
        db,
        actor,
        data=data,
        filename=file.filename or "document",
        declared_mime=file.content_type,
        external_id=external_id or None,
        source="api" if actor.type == "api_key" else "upload",
        encounter_type=encounter_type,
        patient_sex=patient_sex,
        patient_age=patient_age,
    )
    if process:
        return _queue(db, doc, actor, JobType.PROCESS_DOCUMENT, callback)
    db.commit()
    return ProcessOut(document_id=doc.id, status=doc.status)


@router.post(
    "/text", response_model=ProcessOut, status_code=201, summary="Submit clinical text directly (JSON)"
)
def submit_text(
    body: TextDocumentIn, db: Session = Depends(get_db), actor: Principal = Depends(Uploader)
) -> ProcessOut:
    callback = _validate_callback(body.callback_url)
    doc = pipeline.ingest_document(
        db,
        actor,
        data=body.text.encode("utf-8"),
        filename=body.filename,
        declared_mime="text/plain",
        external_id=body.external_id,
        source="api" if actor.type == "api_key" else "upload",
        encounter_type=body.encounter_type,
        patient_sex=body.patient_sex,
        patient_age=body.patient_age,
    )
    if body.process:
        return _queue(db, doc, actor, JobType.PROCESS_DOCUMENT, callback)
    db.commit()
    return ProcessOut(document_id=doc.id, status=doc.status)


@router.get("", response_model=DocumentList)
def list_documents(
    status: str | None = None,
    review_route: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: Principal = Depends(Reader),
) -> DocumentList:
    stmt = select(Document)
    if status:
        stmt = stmt.where(Document.status == status)
    if review_route:
        stmt = stmt.where(Document.review_route == review_route)
    if q:  # metadata search only; clinical text is encrypted and never searched in SQL
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(Document.filename).like(like) | func.lower(Document.external_id).like(like)
        )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    return DocumentList(
        items=[DocumentSummary.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{doc_id}", response_model=DocumentDetail)
def get_document(
    doc_id: uuid.UUID, db: Session = Depends(get_db), actor: Principal = Depends(Reader)
) -> DocumentDetail:
    doc = db.execute(
        select(Document)
        .where(Document.id == doc_id)
        .options(
            selectinload(Document.sections),
            selectinload(Document.entities),
            selectinload(Document.final_codes),
        )
    ).scalar_one_or_none()
    if doc is None:
        from app.core.errors import NotFound

        raise NotFound("Document not found")
    audit.record(db, actor, "document.viewed", entity_type="document", entity_id=doc.id, document_id=doc.id)
    db.commit()
    return DocumentDetail.model_validate(doc)


def _sync_or_async(
    db: Session,
    doc_id: uuid.UUID,
    actor: Principal,
    job_type: JobType,
    run_async: bool,
    background: BackgroundTasks,
    callback: str | None,
) -> ProcessOut:
    doc = pipeline.get_document(db, doc_id)
    if doc.status == DocumentStatus.COMPLETED:
        raise Conflict("Document is finalized; reopen it before re-processing")
    if run_async:
        return _queue(db, doc, actor, job_type, callback)
    try:
        if job_type == JobType.EXTRACT:
            doc = pipeline.run_extraction(db, doc_id, actor)
            db.commit()
            return ProcessOut(document_id=doc.id, status=doc.status)
        summary = (pipeline.run_coding if job_type == JobType.CODE else pipeline.process_document)(
            db, doc_id, actor
        )
        db.commit()
    except AppError as exc:
        db.rollback()
        if exc.status_code >= 500:
            pipeline.mark_failed(doc_id, actor, job_type, exc)
        raise
    except Exception as exc:
        db.rollback()
        pipeline.mark_failed(doc_id, actor, job_type, exc)
        raise
    background.add_task(
        send_event,
        "document.coded",
        {
            "document_id": str(summary.document_id),
            "suggestions": summary.suggestions,
            "review_route": summary.route,
            "by_route": summary.by_route,
        },
        callback,
    )
    return ProcessOut(
        document_id=summary.document_id,
        status=DocumentStatus.CODED,
        suggestions=summary.suggestions,
        review_route=summary.route,
        by_route=summary.by_route,
    )


@router.post("/{doc_id}/extract", response_model=ProcessOut, summary="Clinical information extraction")
def extract(
    doc_id: uuid.UUID,
    background: BackgroundTasks,
    run_async: bool = Query(default=False, alias="async"),
    db: Session = Depends(get_db),
    actor: Principal = Depends(Uploader),
) -> ProcessOut:
    return _sync_or_async(db, doc_id, actor, JobType.EXTRACT, run_async, background, None)


@router.post(
    "/{doc_id}/code", response_model=ProcessOut, summary="RAG + LLM coding + validation + confidence"
)
def code(
    doc_id: uuid.UUID,
    background: BackgroundTasks,
    run_async: bool = Query(default=False, alias="async"),
    callback_url: str | None = None,
    db: Session = Depends(get_db),
    actor: Principal = Depends(Uploader),
) -> ProcessOut:
    return _sync_or_async(
        db, doc_id, actor, JobType.CODE, run_async, background, _validate_callback(callback_url)
    )


@router.post("/{doc_id}/process", response_model=ProcessOut, summary="Extraction + coding in one call")
def process(
    doc_id: uuid.UUID,
    background: BackgroundTasks,
    run_async: bool = Query(default=False, alias="async"),
    callback_url: str | None = None,
    db: Session = Depends(get_db),
    actor: Principal = Depends(Uploader),
) -> ProcessOut:
    return _sync_or_async(
        db, doc_id, actor, JobType.PROCESS_DOCUMENT, run_async, background, _validate_callback(callback_url)
    )


@router.get("/{doc_id}/suggestions", response_model=list[SuggestionOut])
def suggestions(
    doc_id: uuid.UUID,
    include_superseded: bool = False,
    db: Session = Depends(get_db),
    _: Principal = Depends(Reader),
) -> list[CodingSuggestion]:
    pipeline.get_document(db, doc_id)
    stmt = (
        select(CodingSuggestion)
        .where(CodingSuggestion.document_id == doc_id)
        .options(selectinload(CodingSuggestion.evidence))
    )
    if not include_superseded:
        stmt = stmt.where(CodingSuggestion.status != SuggestionStatus.SUPERSEDED)
    return list(db.execute(stmt.order_by(CodingSuggestion.sequence, CodingSuggestion.created_at)).scalars())


@router.post(
    "/{doc_id}/suggestions",
    response_model=SuggestionOut,
    status_code=201,
    summary="Coder adds a code the AI missed",
)
def add_suggestion(
    doc_id: uuid.UUID, body: AddCodeIn, db: Session = Depends(get_db), actor: Principal = Depends(Reader)
) -> CodingSuggestion:
    sug = review.add_code(db, actor, doc_id, body.code_system, body.code, body.reason, body.evidence)
    db.commit()
    db.refresh(sug)
    return sug


@router.post("/{doc_id}/finalize", response_model=list[FinalCodeOut])
def finalize(
    doc_id: uuid.UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: Principal = Depends(Reader),
) -> list:
    doc = review.finalize(db, actor, doc_id)
    db.commit()
    codes = [FinalCodeOut.model_validate(c) for c in doc.final_codes]
    background.add_task(
        send_event,
        "document.finalized",
        {
            "document_id": str(doc.id),
            "external_id": doc.external_id,
            "final_codes": [{"system": c.code_system, "code": c.code, "sequence": c.sequence} for c in codes],
        },
    )
    return codes


@router.post("/{doc_id}/reopen", response_model=DocumentSummary)
def reopen(
    doc_id: uuid.UUID, body: ReopenIn, db: Session = Depends(get_db), actor: Principal = Depends(Reader)
) -> Document:
    doc = review.reopen(db, actor, doc_id, body.reason)
    db.commit()
    return doc


@router.get("/{doc_id}/audit", response_model=list[AuditOut])
def document_audit(
    doc_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(AuditReader)
) -> list:
    return list(
        db.execute(select(AuditLog).where(AuditLog.document_id == doc_id).order_by(AuditLog.id)).scalars()
    )


@router.get("/{doc_id}/reviews", response_model=list[ReviewOut])
def document_reviews(
    doc_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(AuditReader)
) -> list:
    return list(
        db.execute(select(Review).where(Review.document_id == doc_id).order_by(Review.created_at)).scalars()
    )


@router.get("/{doc_id}/runs", response_model=list[ModelRunOut])
def document_runs(doc_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(Reader)) -> list:
    return list(
        db.execute(
            select(ModelRun).where(ModelRun.document_id == doc_id).order_by(ModelRun.created_at)
        ).scalars()
    )


@router.get("/{doc_id}/export", summary="Export final codes (JSON or CSV)")
def export(
    doc_id: uuid.UUID,
    fmt: Literal["json", "csv"] = Query(default="json", alias="format"),
    db: Session = Depends(get_db),
    actor: Principal = Depends(Reader),
) -> Response:
    doc = pipeline.get_document(db, doc_id)
    if doc.status != DocumentStatus.COMPLETED:
        raise Conflict("Only finalized documents can be exported")
    audit.record(
        db,
        actor,
        "document.exported",
        entity_type="document",
        entity_id=doc.id,
        document_id=doc.id,
        details={"format": fmt},
    )
    db.commit()
    rows = [
        {
            "document_id": str(doc.id),
            "external_id": doc.external_id or "",
            "sequence": c.sequence,
            "code_system": c.code_system,
            "code": c.code,
            "description": c.description,
            "kb_version": c.kb_version or "",
            "ai_suggested": c.ai_suggested,
            "ai_modified": c.ai_modified,
            "approved_at": c.approved_at.isoformat(),
        }
        for c in doc.final_codes
    ]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["document_id"])
        w.writeheader()
        w.writerows(rows)
        return Response(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="codes-{doc.id}.csv"'},
        )
    import json

    return Response(
        json.dumps(
            {
                "document_id": str(doc.id),
                "external_id": doc.external_id,
                "finalized_at": doc.finalized_at.isoformat() if doc.finalized_at else None,
                "codes": rows,
            }
        ),
        media_type="application/json",
    )
