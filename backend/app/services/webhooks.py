"""Outbound event webhooks (e.g. to n8n) signed with HMAC-SHA256.

Payloads carry identifiers, statuses and codes only - never clinical text.
Receivers verify: hex(hmac_sha256(secret, f"{timestamp}.{body}")) == X-MedCoding-Signature.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def send_event(event: str, data: dict, url: str | None = None, attempts: int = 3) -> bool:
    s = get_settings()
    target = url or s.webhook_url
    if not target:
        return False
    body = json.dumps(
        {"id": str(uuid.uuid4()), "event": event, "created_at": int(time.time()), "data": data},
        default=str,
        separators=(",", ":"),
    ).encode()
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-MedCoding-Event": event, "X-MedCoding-Timestamp": ts}
    if s.webhook_secret:
        headers["X-MedCoding-Signature"] = sign(s.webhook_secret.get_secret_value(), ts, body)
    for attempt in range(1, attempts + 1):
        try:
            resp = httpx.post(target, content=body, headers=headers, timeout=s.webhook_timeout_s)
            if resp.status_code < 300:
                return True
            log.warning(
                "webhook_non_2xx", extra={"event": event, "status": resp.status_code, "attempt": attempt}
            )
        except httpx.HTTPError as exc:
            log.warning("webhook_error", extra={"event": event, "error": str(exc)[:200], "attempt": attempt})
        time.sleep(min(8, 2 ** (attempt - 1)))
    return False
