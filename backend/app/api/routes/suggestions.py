from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Reader
from app.core.errors import NotFound
from app.db.session import get_db
from app.models import CodingSuggestion
from app.schemas.api import ApproveIn, EditIn, RejectIn, SuggestionOut
from app.services import review
from app.services.principal import Principal

router = APIRouter(prefix="/suggestions", tags=["review"])


def _load(db: Session, sid: uuid.UUID) -> CodingSuggestion:
    sug = db.execute(
        select(CodingSuggestion)
        .where(CodingSuggestion.id == sid)
        .options(selectinload(CodingSuggestion.evidence))
    ).scalar_one_or_none()
    if sug is None:
        raise NotFound("Suggestion not found")
    return sug


@router.get("/{sid}", response_model=SuggestionOut)
def get_suggestion(
    sid: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(Reader)
) -> CodingSuggestion:
    return _load(db, sid)


@router.post("/{sid}/approve", response_model=SuggestionOut)
def approve(
    sid: uuid.UUID,
    body: ApproveIn | None = None,
    db: Session = Depends(get_db),
    actor: Principal = Depends(Reader),
) -> CodingSuggestion:
    review.approve(db, actor, sid, body.comment if body else None)
    db.commit()
    return _load(db, sid)


@router.post("/{sid}/reject", response_model=SuggestionOut)
def reject(
    sid: uuid.UUID, body: RejectIn, db: Session = Depends(get_db), actor: Principal = Depends(Reader)
) -> CodingSuggestion:
    review.reject(db, actor, sid, body.reason, body.error_category)
    db.commit()
    return _load(db, sid)


@router.post("/{sid}/edit", response_model=SuggestionOut)
def edit(
    sid: uuid.UUID, body: EditIn, db: Session = Depends(get_db), actor: Principal = Depends(Reader)
) -> CodingSuggestion:
    review.edit(db, actor, sid, body.code, body.reason, body.error_category)
    db.commit()
    return _load(db, sid)
