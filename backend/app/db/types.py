"""Custom column types."""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.core.crypto import decrypt_str, encrypt_str


class EncryptedText(TypeDecorator):
    """Transparently encrypts text at rest (PHI columns).

    Encrypted columns cannot be searched or indexed by content; that is the point.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:  # noqa: ANN001
        if value is None:
            return None
        return encrypt_str(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:  # noqa: ANN001
        if value is None:
            return None
        return decrypt_str(value)
