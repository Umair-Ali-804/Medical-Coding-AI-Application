from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    CODER = "coder"  # reviews and finalizes codes
    AUDITOR = "auditor"  # read-only incl. audit trail
    SERVICE = "service"  # integrations (n8n): upload/process/read, never approve


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    TEXT_EXTRACTED = "text_extracted"
    ENTITIES_EXTRACTED = "entities_extracted"
    CODED = "coded"  # suggestions ready, awaiting review
    IN_REVIEW = "in_review"
    COMPLETED = "completed"  # finalized by a coder
    FAILED = "failed"


class CodeSystem(StrEnum):
    ICD10CM = "ICD-10-CM"
    CPT = "CPT"
    HCPCS = "HCPCS"


class EntityCategory(StrEnum):
    CONDITION = "condition"
    SYMPTOM = "symptom"
    PROCEDURE = "procedure"
    MEDICATION = "medication"
    FINDING = "finding"
    ANATOMY = "anatomy"
    OTHER = "other"


class ValidationStatus(StrEnum):
    PASSED = "passed"
    FLAGGED = "flagged"
    REJECTED = "rejected"


class ReviewRoute(StrEnum):
    STANDARD = "standard"  # high confidence: normal review
    MANDATORY = "mandatory"  # low confidence / flagged: must be looked at carefully
    SYSTEM_REJECTED = "system_rejected"  # failed hard validation; shown for transparency


class SuggestionStatus(StrEnum):
    PENDING = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"
    SUPERSEDED = "superseded"  # replaced by a newer coding run


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    ADD = "add"
    FINALIZE = "finalize"
    REOPEN = "reopen"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobType(StrEnum):
    PROCESS_DOCUMENT = "process_document"  # extract + code
    EXTRACT = "extract"
    CODE = "code"


class ErrorCategory(StrEnum):
    """Error-analysis taxonomy recorded on coder corrections."""

    WRONG_CODE = "wrong_code"
    MISSING_CODE = "missing_code"
    EXTRA_CODE = "extra_code"
    WRONG_SPECIFICITY = "wrong_specificity"
    NEGATION_ERROR = "negation_error"
    WRONG_PROCEDURE = "wrong_procedure"
    WRONG_TERMINOLOGY = "wrong_terminology"
    RETRIEVAL_FAILURE = "retrieval_failure"
    REASONING_FAILURE = "reasoning_failure"
    VALIDATION_FAILURE = "validation_failure"
    OTHER = "other"
