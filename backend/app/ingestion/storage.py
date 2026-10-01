"""Encrypted object storage for original uploads.

Local filesystem implementation; swap for S3/Azure Blob by implementing the
same three methods (use server-side KMS encryption there as well).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import get_settings
from app.core.crypto import decrypt_bytes, encrypt_bytes


class LocalEncryptedStorage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("Invalid storage key")
        return p

    def put(self, data: bytes, suffix: str = "") -> str:
        key = f"{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}{suffix}.enc"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encrypt_bytes(data))
        return key

    def get(self, key: str) -> bytes:
        return decrypt_bytes(self._path(key).read_bytes())

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def get_storage() -> LocalEncryptedStorage:
    return LocalEncryptedStorage(get_settings().storage_dir)
