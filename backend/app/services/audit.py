"""Tamper-evident audit logging.

Every entry stores sha256(prev_hash + canonical_json(entry)). Rewriting or
deleting a historical row breaks the chain, which `verify_chain` detects.
Writes are serialized with a Postgres advisory lock so concurrent writers
cannot fork the chain.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.logging import request_id_var
from app.db.base import utcnow
from app.models import AuditLog
from app.services.principal import Principal

GENESIS = "0" * 64
_AUDIT_LOCK_KEY = 815_004_221


def _canonical(entry: dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)


def _entry_hash(prev_hash: str, fields: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical(fields)).encode()).hexdigest()


def _hash_fields(row: AuditLog) -> dict[str, Any]:
    ts: datetime = row.ts
    if ts.tzinfo is not None:
        ts = ts.astimezone(UTC).replace(tzinfo=None)
    return {
        "ts": ts.isoformat(timespec="microseconds"),
        "actor_id": row.actor_id,
        "actor_type": row.actor_type,
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "document_id": str(row.document_id) if row.document_id else None,
        "details": row.details,
    }


def record(
    db: Session,
    actor: Principal,
    action: str,
    *,
    entity_type: str | None = None,
    entity_id: str | uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Append an audit entry in the caller's transaction. Never put clinical text in `details`."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_LOCK_KEY})
    prev = db.execute(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)).scalar_one_or_none()
    row = AuditLog(
        ts=utcnow(),
        actor_id=actor.id,
        actor_type=actor.type,
        actor_label=actor.label,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
        document_id=document_id,
        details=json.loads(_canonical(details or {})),
        ip_address=actor.ip,
        request_id=request_id_var.get(),
        prev_hash=prev or GENESIS,
    )
    row.hash = _entry_hash(row.prev_hash, _hash_fields(row))
    db.add(row)
    db.flush()
    return row


def verify_chain(db: Session, batch: int = 5000) -> dict[str, Any]:
    """Walk the whole chain; return first broken link if any."""
    prev = GENESIS
    checked = 0
    last_id = 0
    while True:
        rows = (
            db.execute(select(AuditLog).where(AuditLog.id > last_id).order_by(AuditLog.id).limit(batch))
            .scalars()
            .all()
        )
        if not rows:
            break
        for row in rows:
            if row.prev_hash != prev or _entry_hash(row.prev_hash, _hash_fields(row)) != row.hash:
                return {"valid": False, "checked": checked, "broken_at_id": row.id}
            prev = row.hash
            checked += 1
            last_id = row.id
    return {"valid": True, "checked": checked, "broken_at_id": None}
