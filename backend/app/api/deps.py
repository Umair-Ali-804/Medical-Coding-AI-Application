"""Authentication & authorization dependencies."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import jwt
from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, Unauthorized
from app.core.security import API_KEY_PREFIX, api_key_matches, decode_access_token
from app.db.base import utcnow
from app.db.session import get_db
from app.models import ApiKey, User
from app.models.enums import Role
from app.services.principal import Principal

_bearer = HTTPBearer(auto_error=False)
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False)


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def get_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    api_key: str | None = Depends(_api_key),
    db: Session = Depends(get_db),
) -> Principal:
    ip = client_ip(request)
    if api_key:
        if not api_key.startswith(API_KEY_PREFIX):
            raise Unauthorized("Invalid API key")
        prefix = api_key[: len(API_KEY_PREFIX) + 8]
        for row in db.execute(
            select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.revoked.is_(False))
        ).scalars():
            if api_key_matches(api_key, row.key_hash):
                row.last_used_at = utcnow()
                db.commit()
                return Principal(
                    id=str(row.id), type="api_key", role=Role(row.role), label=f"apikey:{row.name}", ip=ip
                )
        raise Unauthorized("Invalid API key")
    if creds is None:
        raise Unauthorized("Not authenticated")
    try:
        payload = decode_access_token(creds.credentials)
    except jwt.PyJWTError:
        raise Unauthorized("Invalid or expired token")
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise Unauthorized("User inactive or not found")
    return Principal(id=str(user.id), type="user", role=Role(user.role), label=user.email, ip=ip)


def require(*roles: Role) -> Callable[..., Principal]:
    allowed = set(roles) | {Role.ADMIN}

    def dep(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role not in allowed:
            raise Forbidden(f"Requires role: {', '.join(sorted(r.value for r in allowed))}")
        return principal

    return dep


# Common role sets
Reader = require(Role.CODER, Role.AUDITOR, Role.SERVICE)
Uploader = require(Role.CODER, Role.SERVICE)
CoderOnly = require(Role.CODER)
AuditReader = require(Role.AUDITOR, Role.CODER)
AdminOnly = require()
