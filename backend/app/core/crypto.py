"""Field- and file-level encryption for PHI at rest.

Uses Fernet (AES-128-CBC + HMAC-SHA256) with MultiFernet for key rotation:
the first key in ENCRYPTION_KEYS encrypts, every key can decrypt. Rotate by
prepending a new key, then run `python -m app.cli rotate-keys`.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings


@lru_cache
def _cipher() -> MultiFernet:
    raw = get_settings().encryption_keys.get_secret_value()
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise RuntimeError("ENCRYPTION_KEYS is empty")
    return MultiFernet([Fernet(k.encode()) for k in keys])


def reset_cipher_cache() -> None:
    _cipher.cache_clear()


def encrypt_bytes(data: bytes) -> bytes:
    return _cipher().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    try:
        return _cipher().decrypt(token)
    except InvalidToken as exc:  # pragma: no cover - indicates key misconfiguration
        raise RuntimeError("Unable to decrypt data: wrong or missing encryption key") from exc


def encrypt_str(value: str) -> str:
    return encrypt_bytes(value.encode("utf-8")).decode("ascii")


def decrypt_str(value: str) -> str:
    return decrypt_bytes(value.encode("ascii")).decode("utf-8")


def rotate_token(token: str) -> str:
    return _cipher().rotate(token.encode("ascii")).decode("ascii")


def generate_key() -> str:
    return Fernet.generate_key().decode()


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
