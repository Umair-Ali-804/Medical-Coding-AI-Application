from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from functools import lru_cache
from threading import Lock

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_principal
from app.core.config import get_settings
from app.core.errors import RateLimited, Unauthorized
from app.core.security import create_access_token, hash_password, verify_password
from app.db.base import utcnow
from app.db.session import get_db
from app.models import User
from app.models.enums import Role
from app.schemas.api import TokenOut, UserOut
from app.services import audit
from app.services.principal import Principal

router = APIRouter(prefix="/auth", tags=["auth"])

# Per-IP sliding-window limiter for credential stuffing protection. For multi-replica
# deployments also enforce limits at the reverse proxy / WAF.
_attempts: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


@lru_cache
def _dummy_hash() -> str:
    """Real bcrypt hash so unknown-user logins cost the same time as real ones."""
    return hash_password("timing-equalizer-" + uuid.uuid4().hex[:16])


def _rate_limit(ip: str) -> None:
    limit = get_settings().login_rate_limit_per_minute
    now = time.monotonic()
    with _lock:
        q = _attempts[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            raise RateLimited("Too many login attempts; try again in a minute")
        q.append(now)


@router.post("/login", response_model=TokenOut)
def login(
    request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
) -> TokenOut:
    ip = client_ip(request) or "unknown"
    _rate_limit(ip)
    email = form.username.strip().lower()
    user = db.execute(select(User).where(func.lower(User.email) == email)).scalar_one_or_none()
    # constant-time-ish: always run bcrypt
    ok = verify_password(form.password, user.hashed_password if user else _dummy_hash())
    if not user or not ok or not user.is_active:
        audit.record(
            db,
            Principal(id=email[:64], type="anonymous", role=Role.AUDITOR, label=email[:320], ip=ip),
            "auth.login_failed",
            details={"email": email[:320]},
        )
        db.commit()
        raise Unauthorized("Incorrect email or password")
    user.last_login_at = utcnow()
    token, expires = create_access_token(str(user.id), user.role)
    audit.record(
        db,
        Principal(id=str(user.id), type="user", role=Role(user.role), label=user.email, ip=ip),
        "auth.login",
        entity_type="user",
        entity_id=user.id,
    )
    db.commit()
    return TokenOut(access_token=token, expires_in=expires, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> UserOut:
    if not principal.is_user:
        raise Unauthorized("API keys have no user profile")
    return UserOut.model_validate(db.get(User, uuid.UUID(principal.id)))
