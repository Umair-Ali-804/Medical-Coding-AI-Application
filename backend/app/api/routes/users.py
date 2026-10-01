from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AdminOnly
from app.core.errors import Conflict, InvalidInput, NotFound
from app.core.security import generate_api_key, hash_password, validate_password_strength
from app.db.session import get_db
from app.models import ApiKey, User
from app.schemas.api import ApiKeyCreate, ApiKeyCreated, ApiKeyOut, UserCreate, UserOut, UserUpdate
from app.services import audit
from app.services.principal import Principal

router = APIRouter(tags=["admin"])


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: Principal = Depends(AdminOnly)) -> list[User]:
    return list(db.execute(select(User).order_by(User.created_at)).scalars())


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate, db: Session = Depends(get_db), actor: Principal = Depends(AdminOnly)
) -> User:
    try:
        validate_password_strength(body.password)
    except ValueError as exc:
        raise InvalidInput(str(exc))
    email = body.email.lower()
    if db.execute(select(User).where(func.lower(User.email) == email)).scalar_one_or_none():
        raise Conflict("A user with this email already exists")
    user = User(
        email=email, full_name=body.full_name, hashed_password=hash_password(body.password), role=body.role
    )
    db.add(user)
    db.flush()
    audit.record(
        db,
        actor,
        "user.created",
        entity_type="user",
        entity_id=user.id,
        details={"email": email, "role": body.role},
    )
    db.commit()
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID, body: UserUpdate, db: Session = Depends(get_db), actor: Principal = Depends(AdminOnly)
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    changes: dict = {}
    if body.full_name is not None:
        user.full_name = changes["full_name"] = body.full_name
    if body.role is not None:
        user.role = changes["role"] = body.role
    if body.is_active is not None:
        if str(user.id) == actor.id and body.is_active is False:
            raise Conflict("You cannot deactivate yourself")
        user.is_active = changes["is_active"] = body.is_active
    if body.password:
        try:
            validate_password_strength(body.password)
        except ValueError as exc:
            raise InvalidInput(str(exc))
        user.hashed_password = hash_password(body.password)
        changes["password"] = "changed"
    audit.record(db, actor, "user.updated", entity_type="user", entity_id=user.id, details=changes)
    db.commit()
    return user


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_keys(db: Session = Depends(get_db), _: Principal = Depends(AdminOnly)) -> list[ApiKey]:
    return list(db.execute(select(ApiKey).order_by(ApiKey.created_at.desc())).scalars())


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_key(
    body: ApiKeyCreate, db: Session = Depends(get_db), actor: Principal = Depends(AdminOnly)
) -> ApiKeyCreated:
    raw, prefix, key_hash = generate_api_key()
    row = ApiKey(
        name=body.name,
        prefix=prefix,
        key_hash=key_hash,
        role=body.role,
        created_by=uuid.UUID(actor.id) if actor.is_user else None,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        actor,
        "api_key.created",
        entity_type="api_key",
        entity_id=row.id,
        details={"name": body.name, "role": body.role, "prefix": prefix},
    )
    db.commit()
    return ApiKeyCreated(**ApiKeyOut.model_validate(row).model_dump(), key=raw)


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_key(
    key_id: uuid.UUID, db: Session = Depends(get_db), actor: Principal = Depends(AdminOnly)
) -> None:
    row = db.get(ApiKey, key_id)
    if row is None:
        raise NotFound("API key not found")
    row.revoked = True
    audit.record(
        db, actor, "api_key.revoked", entity_type="api_key", entity_id=row.id, details={"name": row.name}
    )
    db.commit()
