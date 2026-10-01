"""Knowledge-base access: versioned code lookups, inherited notes, loading.

Lookups are hot (validation, retrieval hydration), so active codes are cached
in-process per (system, version). The cache is invalidated when a new version
is activated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from threading import Lock

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from app.knowledge.codes import normalize_code
from app.knowledge.icd10cm import CodeRecord
from app.models import CodeReference, KnowledgeBaseVersion

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodeInfo:
    system: str
    version: str
    code: str
    description: str
    billable: bool
    parent_code: str | None
    category: str | None
    chapter: str | None
    chapter_description: str | None
    section: str | None
    section_description: str | None
    inclusion_terms: tuple[str, ...] = ()
    includes: tuple[str, ...] = ()
    excludes1: tuple[str, ...] = ()
    excludes2: tuple[str, ...] = ()
    code_first: tuple[str, ...] = ()
    use_additional_code: tuple[str, ...] = ()
    code_also: tuple[str, ...] = ()
    attributes: dict = field(default_factory=dict, compare=False, hash=False)

    def search_text(self) -> str:
        parts = [self.description, *self.inclusion_terms]
        return " ; ".join(p for p in parts if p)


@dataclass
class KBSnapshot:
    system: str
    version: str
    codes: dict[str, CodeInfo] = field(default_factory=dict)
    children: dict[str, list[str]] = field(default_factory=dict)

    def get(self, code: str) -> CodeInfo | None:
        return self.codes.get(normalize_code(code, self.system))

    def ancestors(self, code: str) -> list[CodeInfo]:
        out: list[CodeInfo] = []
        info = self.get(code)
        seen = set()
        while info and info.parent_code and info.parent_code not in seen:
            seen.add(info.parent_code)
            info = self.codes.get(info.parent_code)
            if info:
                out.append(info)
        return out

    def inherited(self, code: str, attr: str) -> list[tuple[str, str]]:
        """Notes of `attr` on the code and all its ancestors, as (owner_code, note)."""
        info = self.get(code)
        if not info:
            return []
        out = [(info.code, n) for n in getattr(info, attr)]
        for anc in self.ancestors(code):
            out.extend((anc.code, n) for n in getattr(anc, attr))
        return out

    def billable_descendants(self, code: str, limit: int = 50) -> list[CodeInfo]:
        out: list[CodeInfo] = []
        stack = list(self.children.get(normalize_code(code, self.system), []))
        while stack and len(out) < limit:
            c = stack.pop(0)
            info = self.codes.get(c)
            if not info:
                continue
            if info.billable:
                out.append(info)
            stack.extend(self.children.get(c, []))
        return out


_lock = Lock()
_snapshots: dict[tuple[str, str], KBSnapshot] = {}


def _row_to_info(r: CodeReference) -> CodeInfo:
    return CodeInfo(
        system=r.code_system,
        version=r.version,
        code=r.code,
        description=r.description,
        billable=r.billable,
        parent_code=r.parent_code,
        category=r.category,
        chapter=r.chapter,
        chapter_description=r.chapter_description,
        section=r.section,
        section_description=r.section_description,
        inclusion_terms=tuple(r.inclusion_terms or ()),
        includes=tuple(r.includes or ()),
        excludes1=tuple(r.excludes1 or ()),
        excludes2=tuple(r.excludes2 or ()),
        code_first=tuple(r.code_first or ()),
        use_additional_code=tuple(r.use_additional_code or ()),
        code_also=tuple(r.code_also or ()),
        attributes=dict(r.attributes or {}),
    )


def active_version(db: Session, system: str) -> str | None:
    return db.execute(
        select(KnowledgeBaseVersion.version).where(
            KnowledgeBaseVersion.code_system == system, KnowledgeBaseVersion.active.is_(True)
        )
    ).scalar_one_or_none()


def get_snapshot(db: Session, system: str, version: str | None = None) -> KBSnapshot | None:
    version = version or active_version(db, system)
    if not version:
        return None
    key = (system, version)
    snap = _snapshots.get(key)
    if snap is not None:
        return snap
    with _lock:
        snap = _snapshots.get(key)
        if snap is not None:
            return snap
        rows = db.execute(
            select(CodeReference).where(CodeReference.code_system == system, CodeReference.version == version)
        ).scalars()
        snap = KBSnapshot(system=system, version=version)
        for r in rows:
            info = _row_to_info(r)
            snap.codes[info.code] = info
            if info.parent_code:
                snap.children.setdefault(info.parent_code, []).append(info.code)
        _snapshots[key] = snap
        log.info("kb_snapshot_loaded", extra={"system": system, "version": version, "codes": len(snap.codes)})
        return snap


def clear_cache() -> None:
    with _lock:
        _snapshots.clear()


def find_in_other_versions(db: Session, system: str, code: str, exclude_version: str) -> list[str]:
    return list(
        db.execute(
            select(CodeReference.version).where(
                CodeReference.code_system == system,
                CodeReference.code == code,
                CodeReference.version != exclude_version,
            )
        ).scalars()
    )


def load_records(
    db: Session,
    system: str,
    version: str,
    records: list[CodeRecord],
    *,
    source: str,
    checksum: str,
    activate: bool = True,
    batch_size: int = 5000,
    effective_from: date | None = None,
    effective_to: date | None = None,
) -> KnowledgeBaseVersion:
    """Idempotently (re)load a code system version."""
    db.execute(
        delete(CodeReference).where(CodeReference.code_system == system, CodeReference.version == version)
    )
    existing = db.execute(
        select(KnowledgeBaseVersion).where(
            KnowledgeBaseVersion.code_system == system, KnowledgeBaseVersion.version == version
        )
    ).scalar_one_or_none()
    if existing:
        db.delete(existing)
        db.flush()

    unique: dict[str, CodeRecord] = {}
    for r in records:  # first occurrence wins; source files occasionally repeat a code
        unique.setdefault(normalize_code(r.code, system), r)
    records = list(unique.values())
    rows = [
        {
            "code_system": system,
            "version": version,
            "code": normalize_code(r.code, system),
            "description": r.description,
            "billable": r.billable,
            "parent_code": normalize_code(r.parent_code, system) if r.parent_code else None,
            "category": r.category,
            "chapter": r.chapter,
            "chapter_description": r.chapter_description,
            "section": r.section,
            "section_description": r.section_description,
            "inclusion_terms": r.inclusion_terms,
            "includes": r.includes,
            "excludes1": r.excludes1,
            "excludes2": r.excludes2,
            "code_first": r.code_first,
            "use_additional_code": r.use_additional_code,
            "code_also": r.code_also,
            "attributes": r.attributes or None,
            "source": source[:200],
            "active": True,
        }
        for r in records
    ]
    for i in range(0, len(rows), batch_size):
        db.execute(insert(CodeReference), rows[i : i + batch_size])

    if activate:
        db.execute(
            update(KnowledgeBaseVersion)
            .where(KnowledgeBaseVersion.code_system == system)
            .values(active=False)
        )
    kbv = KnowledgeBaseVersion(
        code_system=system,
        version=version,
        source=source[:300],
        checksum=checksum,
        code_count=len(rows),
        active=activate,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    db.add(kbv)
    db.flush()
    clear_cache()
    return kbv
