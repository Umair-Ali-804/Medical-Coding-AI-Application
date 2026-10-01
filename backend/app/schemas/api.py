"""Request/response models for the public API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import ErrorCategory, Role


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ auth / users
class UserOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(default="", max_length=200)
    password: str = Field(min_length=12, max_length=72)
    role: Role = Role.CODER


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=72)


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    role: Literal["service", "auditor"] = "service"


class ApiKeyOut(ORM):
    id: uuid.UUID
    name: str
    prefix: str
    role: str
    revoked: bool
    created_at: datetime
    last_used_at: datetime | None = None


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(description="Shown once. Store it securely.")


# ------------------------------------------------------------------ documents
class TextDocumentIn(BaseModel):
    text: str = Field(min_length=20, max_length=500_000)
    filename: str = Field(default="note.txt", max_length=300)
    external_id: str | None = Field(default=None, max_length=200)
    encounter_type: Literal["outpatient", "inpatient"] | None = None
    patient_sex: Literal["M", "F", "U"] | None = None
    patient_age: int | None = Field(default=None, ge=0, le=125)
    process: bool = Field(default=False, description="Queue extraction+coding immediately")
    callback_url: str | None = Field(default=None, max_length=500)


class SectionOut(ORM):
    name: str
    header: str | None
    start: int
    end: int


class EntityOut(ORM):
    id: uuid.UUID
    text: str
    normalized: str | None
    category: str
    start: int
    end: int
    section: str | None
    negated: bool
    uncertain: bool
    historical: bool
    family: bool
    laterality: str | None
    severity: str | None
    temporal: str | None
    source: str
    assertion_conflict: bool


class EvidenceOut(ORM):
    quote: str
    start: int | None
    end: int | None
    match_score: float


class SuggestionOut(ORM):
    id: uuid.UUID
    document_id: uuid.UUID
    code_system: str
    code: str
    description: str
    kb_version: str | None
    entity_text: str | None
    rationale: str | None
    sequence: int
    source: str
    llm_confidence: float | None
    confidence: float
    confidence_breakdown: dict
    retrieval_score: float | None
    retrieval_rank: int | None
    validation_status: str
    validation_issues: list[dict]
    guidelines: list[dict] | None = None
    review_route: str
    status: str
    final_code: str | None
    final_description: str | None
    reviewed_at: datetime | None
    evidence: list[EvidenceOut]
    created_at: datetime


class FinalCodeOut(ORM):
    code_system: str
    code: str
    description: str
    sequence: int
    kb_version: str | None
    ai_suggested: bool
    ai_modified: bool
    approved_at: datetime


class DocumentSummary(ORM):
    id: uuid.UUID
    external_id: str | None
    filename: str
    mime_type: str
    size_bytes: int
    source: str
    status: str
    review_route: str | None
    encounter_type: str
    page_count: int | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    finalized_at: datetime | None


class DocumentDetail(DocumentSummary):
    text: str | None
    patient_sex: str | None
    patient_age: int | None
    extraction_method: str | None
    sections: list[SectionOut]
    entities: list[EntityOut]
    final_codes: list[FinalCodeOut]


class DocumentList(BaseModel):
    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int


class ProcessOut(BaseModel):
    document_id: uuid.UUID
    job_id: uuid.UUID | None = None
    status: str
    suggestions: int | None = None
    review_route: str | None = None
    by_route: dict[str, int] | None = None


class JobOut(ORM):
    id: uuid.UUID
    job_type: str
    document_id: uuid.UUID | None
    status: str
    attempts: int
    max_attempts: int
    result: dict | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


# ------------------------------------------------------------------ review
class ApproveIn(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)
    error_category: ErrorCategory | None = None


class EditIn(BaseModel):
    code: str = Field(min_length=3, max_length=10)
    reason: str = Field(min_length=3, max_length=2000)
    error_category: ErrorCategory | None = None


class AddCodeIn(BaseModel):
    code: str = Field(min_length=3, max_length=10)
    code_system: Literal["ICD-10-CM", "CPT", "HCPCS", "ICD-10-PCS"] = "ICD-10-CM"
    reason: str = Field(min_length=3, max_length=2000)
    evidence: str | None = Field(default=None, max_length=2000)


class ReopenIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class ReviewOut(ORM):
    id: uuid.UUID
    suggestion_id: uuid.UUID | None
    reviewer_id: uuid.UUID
    action: str
    original_code: str | None
    final_code: str | None
    reason: str | None
    error_category: str | None
    created_at: datetime


# ------------------------------------------------------------------ audit / kb / stats
class AuditOut(ORM):
    id: int
    ts: datetime
    actor_id: str | None
    actor_type: str
    actor_label: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    document_id: uuid.UUID | None
    details: dict
    request_id: str | None
    hash: str


class ModelRunOut(ORM):
    id: uuid.UUID
    run_type: str
    provider: str
    model: str
    prompt_version: str | None
    retrieval_version: str | None
    kb_version: str | None
    pipeline_version: str
    config: dict
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    status: str
    error: str | None
    created_at: datetime


class CodeOut(BaseModel):
    code_system: str
    version: str
    code: str
    description: str
    billable: bool
    parent_code: str | None
    chapter: str | None
    chapter_description: str | None
    section: str | None
    section_description: str | None
    inclusion_terms: list[str]
    includes: list[str]
    excludes1: list[str]
    excludes2: list[str]
    code_first: list[str]
    use_additional_code: list[str]
    code_also: list[str]
    inherited_excludes1: list[str] = []


class CodeSearchHit(BaseModel):
    code: str
    description: str
    billable: bool
    score: float


class KBVersionOut(ORM):
    code_system: str
    version: str
    source: str
    code_count: int
    active: bool
    indexed: bool
    loaded_at: datetime


class StatsOut(BaseModel):
    documents_by_status: dict[str, int]
    pending_review: int
    mandatory_review: int
    suggestions_by_status: dict[str, int]
    reviews_by_action: dict[str, int]
    acceptance_rate: float | None
    correction_rate: float | None
    ai_precision: float | None
    error_categories: dict[str, int]
    avg_confidence_approved: float | None
    avg_confidence_rejected: float | None
    llm_cost_usd_total: float
    avg_coding_latency_ms: float | None
