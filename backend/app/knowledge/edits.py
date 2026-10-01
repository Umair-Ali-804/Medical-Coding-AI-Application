"""Claim edit sets (MUE, NCCI PTP, HCPCS processing notes, modifiers, fee schedule, coverage).

Stored in `claim_edits` and versioned through `kb_versions` (code_system = edit set name).
The scrubber reads an in-process `EditsSnapshot` of every active set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from threading import Lock

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from app.knowledge.cms_loaders import EditRow
from app.models import ClaimEdit, KnowledgeBaseVersion

EDIT_SETS = ("MUE-PRAC", "MUE-OPH", "MUE-DME", "NCCI-PTP", "HCPCS-NOTES", "MODIFIERS", "FEE", "COVERAGE")


@dataclass
class EditSetInfo:
    name: str
    version: str
    effective_from: date | None
    effective_to: date | None
    count: int


@dataclass
class EditsSnapshot:
    sets: dict[str, EditSetInfo] = field(default_factory=dict)
    mue: dict[str, dict[str, tuple[float, str | None, str | None]]] = field(default_factory=dict)
    ptp: dict[tuple[str, str], tuple[str | None, date | None, date | None, str | None]] = field(
        default_factory=dict
    )
    notes: dict[str, str] = field(default_factory=dict)
    modifiers: dict[str, str] = field(default_factory=dict)
    fee: dict[str, float] = field(default_factory=dict)
    coverage: dict[str, list[str]] = field(default_factory=dict)

    def has(self, name: str) -> bool:
        return name in self.sets


_lock = Lock()
_cache: EditsSnapshot | None = None


def clear_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def load_edit_set(
    db: Session,
    name: str,
    version: str,
    rows: list[EditRow],
    *,
    source: str,
    checksum: str,
    effective_from: date | None = None,
    effective_to: date | None = None,
    batch_size: int = 5000,
) -> KnowledgeBaseVersion:
    db.execute(delete(ClaimEdit).where(ClaimEdit.edit_set == name, ClaimEdit.version == version))
    old = db.execute(
        select(KnowledgeBaseVersion).where(
            KnowledgeBaseVersion.code_system == name, KnowledgeBaseVersion.version == version
        )
    ).scalar_one_or_none()
    if old:
        db.delete(old)
        db.flush()
    payload = [
        {
            "edit_set": name,
            "version": version,
            "code": r.code[:20],
            "code2": (r.code2 or None) and r.code2[:20],
            "value": r.value,
            "indicator": r.indicator,
            "text": r.text,
            "effective_from": r.effective_from,
            "effective_to": r.effective_to,
        }
        for r in rows
    ]
    for i in range(0, len(payload), batch_size):
        db.execute(insert(ClaimEdit), payload[i : i + batch_size])
    db.execute(
        update(KnowledgeBaseVersion).where(KnowledgeBaseVersion.code_system == name).values(active=False)
    )
    kbv = KnowledgeBaseVersion(
        code_system=name,
        version=version,
        source=source[:300],
        checksum=checksum,
        code_count=len(payload),
        active=True,
        indexed=True,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    db.add(kbv)
    db.flush()
    clear_cache()
    return kbv


def get_edits(db: Session) -> EditsSnapshot:
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is not None:
            return _cache
        snap = EditsSnapshot()
        versions = db.execute(
            select(KnowledgeBaseVersion).where(
                KnowledgeBaseVersion.code_system.in_(EDIT_SETS), KnowledgeBaseVersion.active.is_(True)
            )
        ).scalars()
        for v in versions:
            snap.sets[v.code_system] = EditSetInfo(
                v.code_system, v.version, v.effective_from, v.effective_to, v.code_count
            )
        for name, info in snap.sets.items():
            rows = db.execute(
                select(ClaimEdit).where(ClaimEdit.edit_set == name, ClaimEdit.version == info.version)
            ).scalars()
            for r in rows:
                if name.startswith("MUE"):
                    snap.mue.setdefault(name, {})[r.code] = (r.value or 0, r.indicator, r.text)
                elif name == "NCCI-PTP" and r.code2:
                    snap.ptp[(r.code, r.code2)] = (r.indicator, r.effective_from, r.effective_to, r.text)
                elif name == "HCPCS-NOTES":
                    snap.notes[r.code] = r.text or ""
                elif name == "MODIFIERS":
                    snap.modifiers[r.code] = r.text or ""
                elif name == "FEE" and r.value is not None:
                    snap.fee[r.code] = r.value
                elif name == "COVERAGE" and r.code2:
                    snap.coverage.setdefault(r.code, []).append(r.code2)
        _cache = snap
        return snap
