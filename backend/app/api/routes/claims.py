"""Pre-bill claim scrubbing, guideline search and reference-data freshness."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import Reader
from app.claims.scrubber import Claim, ClaimScrubber, claim_from_suggestions
from app.db.session import get_db
from app.knowledge import guidelines
from app.knowledge.edits import get_edits
from app.knowledge.loader_service import active_versions, freshness_warnings
from app.models.enums import SuggestionStatus
from app.services.pipeline import get_document
from app.services.principal import Principal

router = APIRouter()


class ClaimLineIn(BaseModel):
    code: str = Field(min_length=3, max_length=12)
    units: float = Field(default=1, ge=0, le=100000)
    modifiers: list[str] = Field(default_factory=list, max_length=4)
    dx_pointers: list[int] = Field(default_factory=list, max_length=12)
    system: Literal["CPT", "HCPCS", "ICD-10-PCS"] | None = None
    charge: float | None = None


class ClaimIn(BaseModel):
    diagnoses: list[str] = Field(default_factory=list, max_length=25)
    lines: list[ClaimLineIn] = Field(default_factory=list, max_length=50)
    date_of_service: date = Field(default_factory=date.today)
    claim_type: Literal["professional", "institutional"] = "professional"
    encounter_type: Literal["outpatient", "inpatient"] = "outpatient"
    patient_sex: Literal["M", "F"] | None = None
    patient_age: int | None = Field(default=None, ge=0, le=125)
    payer: str = "medicare"


@router.post("/claims/scrub", tags=["claims"])
def scrub(body: ClaimIn, db: Session = Depends(get_db), _: Principal = Depends(Reader)) -> dict:
    """Check a claim for denial risks before submission (CARC-mapped, with fixes)."""
    claim = Claim.from_dict(body.model_dump(mode="json"))
    return ClaimScrubber(db).scrub(claim).as_dict()


@router.get("/documents/{doc_id}/claim-check", tags=["claims"])
def document_claim_check(
    doc_id: uuid.UUID,
    dos: date | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(Reader),
) -> dict:
    """Draft a claim from the document's current codes (approved/final, else pending AI suggestions) and scrub it."""
    doc = get_document(db, doc_id)
    keep = {SuggestionStatus.PENDING, SuggestionStatus.APPROVED, SuggestionStatus.EDITED}
    sugs = sorted(
        (s for s in doc.suggestions if s.status in keep and s.validation_status != "rejected"),
        key=lambda s: (s.code_system != "ICD-10-CM", s.sequence),
    )
    items = [
        {
            "code": s.final_code or s.code,
            "code_system": s.code_system,
            "description": s.final_description or s.description,
            "evidence": [e.quote for e in s.evidence],
        }
        for s in sugs
    ]
    claim = claim_from_suggestions(
        items,
        date_of_service=dos or doc.created_at.date(),
        encounter_type=doc.encounter_type,
        patient_sex=doc.patient_sex,
        patient_age=doc.patient_age,
    )
    res = ClaimScrubber(db).scrub(claim).as_dict()
    res["claim"] = {
        "diagnoses": claim.diagnoses,
        "lines": [{"code": x.code, "system": x.system, "units": x.units} for x in claim.lines],
        "date_of_service": str(claim.date_of_service),
        "claim_type": claim.claim_type,
    }
    return res


@router.get("/knowledge/guidelines/search", tags=["knowledge"])
def guideline_search(
    q: str = Query(min_length=2, max_length=300),
    system: Literal["ICD-10-CM", "ICD-10-PCS"] | None = None,
    code: str | None = None,
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
    _: Principal = Depends(Reader),
) -> list[dict]:
    hits = (
        guidelines.guidelines_for(db, code, system or "ICD-10-CM", q, k=limit)
        if code
        else guidelines.search(db, q, system, k=limit)
    )
    return [
        {
            "ref": h.ref,
            "title": h.title,
            "page": h.page,
            "citation": h.citation(),
            "text": h.text,
            "score": h.score,
        }
        for h in hits
    ]


@router.get("/knowledge/freshness", tags=["knowledge"])
def freshness(dos: date | None = None, db: Session = Depends(get_db), _: Principal = Depends(Reader)) -> dict:
    """Which loaded reference sets are valid for a date of service (default today)."""
    on = dos or date.today()
    sets = [
        {
            "set": v.code_system,
            "version": v.version,
            "effective_from": v.effective_from,
            "effective_to": v.effective_to,
            "valid": (not v.effective_from or on >= v.effective_from)
            and (not v.effective_to or on <= v.effective_to),
        }
        for v in active_versions(db)
    ]
    return {"date_of_service": on, "sets": sets, "warnings": freshness_warnings(db, on)}


@router.get("/knowledge/edits/mue/{code}", tags=["knowledge"])
def mue(code: str, db: Session = Depends(get_db), _: Principal = Depends(Reader)) -> dict:
    e = get_edits(db)
    out = {}
    for name, table in e.mue.items():
        if code.upper() in table:
            v, mai, rat = table[code.upper()]
            out[name] = {
                "units_per_day": v,
                "adjudication_indicator": mai,
                "rationale": rat,
                "version": e.sets[name].version,
            }
    return {"code": code.upper(), "mue": out}
