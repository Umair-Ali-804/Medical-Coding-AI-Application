"""ORM models. PHI-bearing text columns use EncryptedText."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONType, TimestampMixin, utcnow
from app.db.types import EncryptedText
from app.models.enums import (
    DocumentStatus,
    JobStatus,
    ReviewRoute,
    SuggestionStatus,
    ValidationStatus,
)

__all__ = [
    "Base",
    "ClaimEdit",
    "GuidelineChunk",
    "User",
    "ApiKey",
    "Document",
    "DocumentSection",
    "ClinicalEntity",
    "ModelRun",
    "CodingSuggestion",
    "CodingEvidence",
    "Review",
    "FinalCode",
    "AuditLog",
    "Job",
    "CodeReference",
    "KnowledgeBaseVersion",
]


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    hashed_password: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(20), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------
# Documents & extraction
# ---------------------------------------------------------------------------
class Document(TimestampMixin, Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    external_id: Mapped[str | None] = mapped_column(String(200), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str | None] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(50), default="upload")  # upload | api | n8n
    status: Mapped[str] = mapped_column(String(30), default=DocumentStatus.UPLOADED, index=True)
    encounter_type: Mapped[str] = mapped_column(String(20), default="outpatient")
    patient_sex: Mapped[str | None] = mapped_column(String(1))  # M | F | U
    patient_age: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(EncryptedText)
    text_sha256: Mapped[str | None] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    extraction_method: Mapped[str | None] = mapped_column(String(50))
    review_route: Mapped[str | None] = mapped_column(String(20), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sections: Mapped[list[DocumentSection]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentSection.order"
    )
    entities: Mapped[list[ClinicalEntity]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="ClinicalEntity.start"
    )
    suggestions: Mapped[list[CodingSuggestion]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="CodingSuggestion.sequence"
    )
    final_codes: Mapped[list[FinalCode]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="FinalCode.sequence"
    )


class DocumentSection(Base):
    __tablename__ = "document_sections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    header: Mapped[str | None] = mapped_column(String(200))
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    order: Mapped[int] = mapped_column(Integer)

    document: Mapped[Document] = relationship(back_populates="sections")


class ClinicalEntity(Base):
    __tablename__ = "clinical_entities"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_runs.id"))
    text: Mapped[str] = mapped_column(EncryptedText)
    normalized: Mapped[str | None] = mapped_column(EncryptedText)
    category: Mapped[str] = mapped_column(String(20), index=True)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(100))
    negated: Mapped[bool] = mapped_column(Boolean, default=False)
    uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    historical: Mapped[bool] = mapped_column(Boolean, default=False)
    family: Mapped[bool] = mapped_column(Boolean, default=False)
    laterality: Mapped[str | None] = mapped_column(String(20))
    severity: Mapped[str | None] = mapped_column(String(50))
    temporal: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(20))  # rules | llm | medspacy | merged
    assertion_conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float | None] = mapped_column(Float)

    document: Mapped[Document] = relationship(back_populates="entities")


# ---------------------------------------------------------------------------
# AI runs & suggestions
# ---------------------------------------------------------------------------
class ModelRun(Base):
    """One pipeline stage execution: full traceability of model/prompt/retrieval versions."""

    __tablename__ = "model_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    run_type: Mapped[str] = mapped_column(String(30))  # extraction | coding
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    retrieval_version: Mapped[str | None] = mapped_column(String(200))
    kb_version: Mapped[str | None] = mapped_column(String(50))
    pipeline_version: Mapped[str] = mapped_column(String(30))
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="succeeded")
    error: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[str | None] = mapped_column(EncryptedText)  # may quote PHI
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class CodingSuggestion(TimestampMixin, Base):
    __tablename__ = "coding_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_runs.id"))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clinical_entities.id", ondelete="SET NULL")
    )
    code_system: Mapped[str] = mapped_column(String(20))
    code: Mapped[str] = mapped_column(String(20), index=True)
    description: Mapped[str] = mapped_column(Text)
    kb_version: Mapped[str | None] = mapped_column(String(50))
    entity_text: Mapped[str | None] = mapped_column(EncryptedText)
    rationale: Mapped[str | None] = mapped_column(EncryptedText)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(20), default="ai")  # ai | manual
    llm_confidence: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_breakdown: Mapped[dict] = mapped_column(JSONType, default=dict)
    retrieval_score: Mapped[float | None] = mapped_column(Float)
    retrieval_rank: Mapped[int | None] = mapped_column(Integer)
    validation_status: Mapped[str] = mapped_column(String(20), default=ValidationStatus.PASSED, index=True)
    validation_issues: Mapped[list] = mapped_column(JSONType, default=list)
    # Official Guideline passages supporting/constraining this code: [{ref, title, page, snippet}]
    guidelines: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    review_route: Mapped[str] = mapped_column(String(20), default=ReviewRoute.MANDATORY, index=True)
    status: Mapped[str] = mapped_column(String(20), default=SuggestionStatus.PENDING, index=True)
    final_code: Mapped[str | None] = mapped_column(String(20))
    final_description: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship(back_populates="suggestions")
    evidence: Mapped[list[CodingEvidence]] = relationship(
        back_populates="suggestion", cascade="all, delete-orphan", order_by="CodingEvidence.start"
    )

    __table_args__ = (Index("ix_suggestions_doc_status", "document_id", "status"),)


class CodingEvidence(Base):
    __tablename__ = "coding_evidence"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("coding_suggestions.id", ondelete="CASCADE"), index=True
    )
    quote: Mapped[str] = mapped_column(EncryptedText)
    start: Mapped[int | None] = mapped_column(Integer)
    end: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(100))
    match_score: Mapped[float] = mapped_column(Float, default=0.0)  # 1.0 = verbatim in document

    suggestion: Mapped[CodingSuggestion] = relationship(back_populates="evidence")


# ---------------------------------------------------------------------------
# Human review
# ---------------------------------------------------------------------------
class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    suggestion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("coding_suggestions.id", ondelete="SET NULL")
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(20), index=True)
    original_code: Mapped[str | None] = mapped_column(String(20))
    final_code: Mapped[str | None] = mapped_column(String(20))
    original_confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class FinalCode(Base):
    __tablename__ = "final_codes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    suggestion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("coding_suggestions.id", ondelete="SET NULL")
    )
    code_system: Mapped[str] = mapped_column(String(20))
    code: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer)
    kb_version: Mapped[str | None] = mapped_column(String(50))
    ai_suggested: Mapped[bool] = mapped_column(Boolean, default=True)
    ai_modified: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="final_codes")

    __table_args__ = (UniqueConstraint("document_id", "code_system", "code", name="uq_final_codes_doc_code"),)


class AuditLog(Base):
    """Append-only, hash-chained audit trail (tamper-evident)."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), index=True)
    actor_type: Mapped[str] = mapped_column(String(20))  # user | api_key | system
    actor_label: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


# ---------------------------------------------------------------------------
# Workflow / jobs
# ---------------------------------------------------------------------------
class Job(TimestampMixin, Base):
    """Durable Postgres-backed job queue (FOR UPDATE SKIP LOCKED) = workflow_runs."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    job_type: Mapped[str] = mapped_column(String(30))
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(64))
    callback_url: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------
class KnowledgeBaseVersion(Base):
    __tablename__ = "kb_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_system: Mapped[str] = mapped_column(String(20))
    version: Mapped[str] = mapped_column(String(50))
    source: Mapped[str] = mapped_column(String(300))
    checksum: Mapped[str] = mapped_column(String(64))
    code_count: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Dates of service this reference set is valid for (e.g. ICD-10-CM FY: Oct 1 - Sep 30,
    # MUE/HCPCS: quarterly). Used by the claim scrubber and `kb freshness`.
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (UniqueConstraint("code_system", "version", name="uq_kb_versions_system_version"),)


class CodeReference(Base):
    __tablename__ = "code_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_system: Mapped[str] = mapped_column(String(20))
    version: Mapped[str] = mapped_column(String(50))
    code: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    billable: Mapped[bool] = mapped_column(Boolean, default=True)
    parent_code: Mapped[str | None] = mapped_column(String(20))
    category: Mapped[str | None] = mapped_column(String(10), index=True)
    chapter: Mapped[str | None] = mapped_column(String(10))
    chapter_description: Mapped[str | None] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(String(20))
    section_description: Mapped[str | None] = mapped_column(Text)
    inclusion_terms: Mapped[list] = mapped_column(JSONType, default=list)
    includes: Mapped[list] = mapped_column(JSONType, default=list)
    excludes1: Mapped[list] = mapped_column(JSONType, default=list)
    excludes2: Mapped[list] = mapped_column(JSONType, default=list)
    code_first: Mapped[list] = mapped_column(JSONType, default=list)
    use_additional_code: Mapped[list] = mapped_column(JSONType, default=list)
    code_also: Mapped[list] = mapped_column(JSONType, default=list)
    source: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # System-specific metadata: HCPCS coverage/BETOS/term date/processing notes, CPT category,
    # ICD-10-PCS section/body system/root operation...
    attributes: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    __table_args__ = (
        UniqueConstraint("code_system", "version", "code", name="uq_code_references_system_version_code"),
        Index("ix_code_references_lookup", "code_system", "code"),
    )


class ClaimEdit(Base):
    """Payer/CMS claim edits: MUE unit limits, NCCI procedure-to-procedure pairs,
    HCPCS processing notes, fee schedule amounts. Versioned through kb_versions
    (code_system = edit set name, e.g. 'MUE', 'NCCI-PTP', 'HCPCS-NOTES', 'FEE')."""

    __tablename__ = "claim_edits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    edit_set: Mapped[str] = mapped_column(String(20))
    version: Mapped[str] = mapped_column(String(50))
    code: Mapped[str] = mapped_column(String(20))
    code2: Mapped[str | None] = mapped_column(String(20), nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    indicator: Mapped[str | None] = mapped_column(String(10), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (Index("ix_claim_edits_lookup", "edit_set", "version", "code"),)


class GuidelineChunk(Base):
    """Passages of the Official Coding Guidelines (ICD-10-CM, ICD-10-PCS), for citation and RAG."""

    __tablename__ = "guideline_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_system: Mapped[str] = mapped_column(String(20))
    version: Mapped[str] = mapped_column(String(50))
    ref: Mapped[str] = mapped_column(String(60))  # e.g. "I.C.4.a" or "B3.1a"
    title: Mapped[str] = mapped_column(String(300))
    code_range: Mapped[str | None] = mapped_column(String(60), nullable=True)  # e.g. "E00-E89"
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)

    __table_args__ = (Index("ix_guideline_chunks_system_version", "code_system", "version"),)
