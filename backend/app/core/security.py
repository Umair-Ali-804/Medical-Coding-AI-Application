"""Password hashing, JWT access tokens and API keys."""

from __future__ import annotations

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.crypto import sha256_hex

API_KEY_PREFIX = "mcai_"


def hash_password(password: str) -> str:
    if len(password.encode()) > 72:
        raise ValueError("Password too long (max 72 bytes)")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def validate_password_strength(password: str) -> None:
    problems = []
    if len(password) < 12:
        problems.append("at least 12 characters")
    if not any(c.isupper() for c in password) or not any(c.islower() for c in password):
        problems.append("upper and lower case letters")
    if not any(c.isdigit() for c in password):
        problems.append("a digit")
    if problems:
        raise ValueError("Password must contain " + ", ".join(problems))


def create_access_token(subject: str, role: str, extra: dict[str, Any] | None = None) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=s.access_token_minutes)
    payload = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_hex(8),
        "iss": "medcoding-api",
        **(extra or {}),
    }
    token = jwt.encode(payload, s.jwt_secret.get_secret_value(), algorithm=s.jwt_algorithm)
    return token, s.access_token_minutes * 60


def decode_access_token(token: str) -> dict[str, Any]:
    s = get_settings()
    return jwt.decode(
        token,
        s.jwt_secret.get_secret_value(),
        algorithms=[s.jwt_algorithm],
        issuer="medcoding-api",
        options={"require": ["exp", "sub", "role"]},
    )


def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext_key, prefix, sha256_hash). Only the hash is stored."""
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[: len(API_KEY_PREFIX) + 8], sha256_hex(raw)


def api_key_matches(raw: str, stored_hash: str) -> bool:
    return hmac.compare_digest(sha256_hex(raw), stored_hash)
