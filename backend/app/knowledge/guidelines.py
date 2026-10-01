"""Official Coding Guidelines as a searchable, citable knowledge source.

The ICD-10-CM and ICD-10-PCS Official Guidelines (CMS/NCHS PDFs) are split into passages keyed by
their official reference ("I.C.4.a", "IV.H", "B3.1b"). Each passage knows its chapter code range,
so a suggested code can be linked deterministically to the guideline chapter that governs it, and
BM25 (plus optional dense similarity) ranks passages within that chapter by the clinical concept.

Used for: citations shown to coders next to each suggestion, the LLM prompt (grounding in the
actual rule text), and free-text guideline search.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.knowledge.cms_loaders import ICD10CM_CHAPTERS
from app.models import GuidelineChunk
from app.rag.lexical import tokenize

log = logging.getLogger(__name__)
MAX_CHUNK = 1800


@dataclass
class Chunk:
    ref: str
    title: str
    text: str
    page: int | None = None
    code_range: str | None = None


# ----------------------------------------------------------------------------- PDF -> pages


def extract_pages(pdf_path: str | Path) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    return [(p.extract_text() or "") for p in reader.pages]


def detect_system(pages: list[str]) -> str | None:
    head = " ".join(pages[:2])[:3000]
    if "ICD-10-PCS Official Guidelines" in head or "ICD-10-PCS" in head[:400]:
        return "ICD-10-PCS"
    if "ICD-10-CM Official Guidelines" in head:
        return "ICD-10-CM"
    return None


def detect_version(pages: list[str]) -> str:
    m = re.search(r"(?:FY\s*)?(20\d\d)", " ".join(pages[:1])[:600])
    return m.group(1) if m else "unknown"


def _split_long(ref: str, title: str, text: str, page: int | None, code_range: str | None) -> list[Chunk]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    text = re.sub(r"\n{2,}", "\n", text)
    if len(text) <= MAX_CHUNK:
        return [Chunk(ref, title, text, page, code_range)] if text else []
    out, buf, part = [], [], 1
    for para in re.split(r"(?<=[.:])\n", text):
        if sum(len(b) for b in buf) + len(para) > MAX_CHUNK and buf:
            out.append(Chunk(ref, f"{title} (part {part})", "\n".join(buf), page, code_range))
            buf, part = [], part + 1
        buf.append(para)
    if buf:
        out.append(
            Chunk(ref, f"{title} (part {part})" if part > 1 else title, "\n".join(buf), page, code_range)
        )
    return out


# ----------------------------------------------------------------------------- ICD-10-CM chunker

_CM_NOISE = re.compile(
    r"^(ICD-10-CM Official Guidelines for Coding and Reporting|FY 20\d\d.*|Page \d+ of \d+)\s*$"
)
_SECTION = re.compile(r"^Section (I|II|III|IV)\.\s+([A-Z][a-z].*)$")
_LETTER = re.compile(r"^([A-Z])\.\s+([A-Z(].{2,110})$")
_NUMBER = re.compile(r"^(\d{1,2})\.\s+([A-Z(].{2,110})$")
_CHAPTER = re.compile(r"^(\d{1,2})\.\s+Chapter\s+(\d{1,2}):\s*(.*)$")
_SUBLETTER = re.compile(r"^([a-z])\.\s+([A-Z(].{2,110})$")


def chunk_icd10cm(pages: list[str]) -> list[Chunk]:
    lines: list[tuple[int, str]] = []
    for pno, page in enumerate(pages, start=1):
        for ln in page.splitlines():
            s = ln.strip()
            if s and not _CM_NOISE.match(s):
                lines.append((pno, s))
    # body starts at the first "Section I." heading that is not a table-of-contents line
    start = next(
        (i for i, (_p, s) in enumerate(lines) if _SECTION.match(s) and "...." not in s and i > 20), 0
    )
    chunks: list[Chunk] = []
    sec = letter = num = chap = sub = None
    title, code_range, page = "Introduction", None, None
    buf: list[str] = []

    def ref() -> str:
        parts = [p for p in (sec, letter, chap or num, sub) if p]
        return ".".join(parts) if parts else "intro"

    def flush() -> None:
        if buf:
            chunks.extend(_split_long(ref(), title, "\n".join(buf), page, code_range))
        buf.clear()

    for pno, s in lines[start:]:
        m_sec, m_ch = _SECTION.match(s), _CHAPTER.match(s)
        m_let = _LETTER.match(s) if not m_ch else None
        if m_sec:
            flush()
            sec, letter, num, chap, sub = m_sec.group(1), None, None, None, None
            title, code_range, page = f"Section {sec}. {m_sec.group(2)}", None, pno
            continue
        if m_ch:
            flush()
            chap, sub = m_ch.group(2), None
            n = int(chap)
            lo_hi = next(((lo, hi) for c, lo, hi, _d in ICD10CM_CHAPTERS if c == str(n)), None)
            code_range = f"{lo_hi[0]}-{lo_hi[1]}" if lo_hi else None
            title, page = f"Chapter {n}: {m_ch.group(3).strip()}", pno
            continue
        if m_let and sec and len(s) < 115 and not s.endswith("."):
            flush()
            letter, num, chap, sub = m_let.group(1), None, None, None
            title, code_range, page = f"{m_let.group(1)}. {m_let.group(2)}", None, pno
            continue
        m_sub = _SUBLETTER.match(s)
        if m_sub and chap and len(s) < 115:
            flush()
            sub = m_sub.group(1)
            title, page = f"Chapter {chap} {sub}. {m_sub.group(2)}", pno
            continue
        m_num = _NUMBER.match(s)
        if m_num and sec == "I" and letter in ("A", "B") and not chap and len(s) < 115:
            flush()
            num, sub = m_num.group(1), None
            title, page = f"{letter}.{num} {m_num.group(2)}", pno
            continue
        if page is None:
            page = pno
        buf.append(s)
    flush()
    return [c for c in chunks if len(c.text) > 40]


# ----------------------------------------------------------------------------- ICD-10-PCS chunker

_PCS_ID = re.compile(r"^([A-F]\d{1,2}(?:\.\d{1,2}[a-z]?)?)\.?\s*(.*)$")
_PCS_GROUP = {
    "A": "Conventions",
    "B": "Medical and Surgical Section",
    "C": "Obstetrics Section",
    "D": "Radiation Therapy Section",
    "E": "New Technology Section",
    "F": "Selection of Principal Procedure",
}


def chunk_pcs(pages: list[str]) -> list[Chunk]:
    chunks: list[Chunk] = []
    cur_id, cur_title, cur_page, group_title = None, None, None, ""
    buf: list[str] = []

    def flush() -> None:
        if cur_id and buf:
            chunks.extend(_split_long(cur_id, cur_title or cur_id, "\n".join(buf), cur_page, None))
        buf.clear()

    for pno, page in enumerate(pages, start=1):
        if pno <= 2:  # cover text + table of contents
            continue
        for ln in page.splitlines():
            s = ln.strip()
            if not s or re.fullmatch(r"\d{1,3}", s):
                continue
            m = _PCS_ID.match(s)
            if m and len(s) <= 60 and not re.search(r"\.\s*\.\s*\.", s):
                ident, rest = m.group(1), m.group(2).strip()
                if "." not in ident and rest and re.match(r"B\d", ident):
                    group_title = rest  # "B3. Root Operation"
                    continue
                flush()
                cur_id, cur_page = ident, pno
                grp = _PCS_GROUP.get(ident[0], "")
                cur_title = (
                    f"{ident} {grp}{' - ' + group_title if ident[0] == 'B' and group_title else ''}".strip()
                )
                if rest:
                    buf.append(rest)
                continue
            buf.append(s)
    flush()
    return [c for c in chunks if len(c.text) > 30]


# ----------------------------------------------------------------------------- persistence


def load_guidelines(db: Session, system: str, version: str, chunks: list[Chunk]) -> int:
    db.execute(delete(GuidelineChunk).where(GuidelineChunk.code_system == system))
    for i, c in enumerate(chunks):
        db.add(
            GuidelineChunk(
                code_system=system,
                version=version,
                ref=c.ref[:60],
                title=c.title[:300],
                code_range=c.code_range,
                page=c.page,
                ordinal=i,
                text=c.text,
            )
        )
    db.flush()
    clear_cache()
    return len(chunks)


def load_pdf(db: Session, pdf_path: str | Path) -> tuple[str, str, int]:
    pages = extract_pages(pdf_path)
    system = detect_system(pages)
    if system is None:
        raise ValueError(f"{pdf_path}: not an ICD-10-CM/PCS Official Guidelines PDF")
    version = detect_version(pages)
    chunks = chunk_icd10cm(pages) if system == "ICD-10-CM" else chunk_pcs(pages)
    n = load_guidelines(db, system, version, chunks)
    return system, version, n


# ----------------------------------------------------------------------------- retrieval


@dataclass
class GuidelineHit:
    system: str
    version: str
    ref: str
    title: str
    page: int | None
    text: str
    score: float

    def citation(self) -> str:
        loc = f", p. {self.page}" if self.page else ""
        return f"{self.system} Official Guidelines FY{self.version} {self.ref}{loc}"

    def snippet(self, n: int = 420) -> str:
        t = re.sub(r"\s+", " ", self.text)
        return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


class GuidelineIndex:
    def __init__(self, rows: list[GuidelineChunk]):
        self.rows = rows
        self.docs = [Counter(tokenize(f"{r.title} {r.text}")) for r in rows]
        self.len = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(rows)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def _bm25(self, i: int, q: list[str]) -> float:
        d, dl, s = self.docs[i], self.len[i], 0.0
        for t in q:
            tf = d.get(t)
            if tf:
                s += self.idf[t] * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * dl / self.avg))
        return s

    def search(
        self, query: str, k: int = 5, code: str | None = None, encounter: str | None = None
    ) -> list[GuidelineHit]:
        q = list(set(tokenize(query, expand=True)))
        raw = [self._bm25(i, q) for i in range(len(self.rows))]
        top = max(raw) if raw and max(raw) > 0 else 1.0
        scored = []
        compact = code.replace(".", "") if code else None
        for i, r in enumerate(self.rows):
            s = raw[i] / top
            mentioned = False
            if code and r.code_system == "ICD-10-CM":
                in_chapter = bool(r.code_range and _in_range(code, r.code_range))
                general = not r.ref.startswith("I.C")
                if in_chapter:
                    s += 0.35
                cat = code.replace(".", "")[:3]
                if re.search(rf"\b{re.escape(code)}\b", r.text):
                    s += 0.45
                    mentioned = True
                elif re.search(rf"\b{cat}(?:\.|\b|-)", r.text):
                    s += 0.2
                    mentioned = True
                if not (in_chapter or mentioned or (general and raw[i] / top >= 0.5)):
                    continue  # another chapter's rule does not govern this code
                inpatient_only = r.ref.startswith(("II", "III")) and not r.ref.startswith("IV")
                if encounter == "outpatient" and inpatient_only:
                    s -= 0.3
                if encounter == "inpatient" and r.ref.startswith("IV"):
                    s -= 0.3
            elif (
                code
                and compact
                and r.code_system == "ICD-10-PCS"
                and compact[:1] == "0"
                and r.ref.startswith("B")
            ):
                s += 0.1
            if s > 0.15 and (raw[i] > 0 or mentioned):
                scored.append((s, r))
        scored.sort(key=lambda x: -x[0])
        seen: set[str] = set()
        hits = []
        for s, r in scored:
            key = r.ref
            if key in seen:
                continue
            seen.add(key)
            hits.append(GuidelineHit(r.code_system, r.version, r.ref, r.title, r.page, r.text, round(s, 3)))
            if len(hits) >= k:
                break
        return hits


def _in_range(code: str, rng: str) -> bool:
    lo, _, hi = rng.partition("-")
    cat = code.replace(".", "")[:3].upper()
    return lo <= cat <= (hi or lo)


_lock = Lock()
_indexes: dict[str, GuidelineIndex] = {}


def clear_cache() -> None:
    with _lock:
        _indexes.clear()


def get_index(db: Session, system: str) -> GuidelineIndex | None:
    idx = _indexes.get(system)
    if idx is not None:
        return idx
    with _lock:
        idx = _indexes.get(system)
        if idx is None:
            rows = list(
                db.execute(
                    select(GuidelineChunk)
                    .where(GuidelineChunk.code_system == system)
                    .order_by(GuidelineChunk.ordinal)
                ).scalars()
            )
            if not rows:
                return None
            for r in rows:
                db.expunge(r)
            idx = GuidelineIndex(rows)
            _indexes[system] = idx
    return idx


def guidelines_for(
    db: Session, code: str, system: str, concept: str, encounter: str | None = None, k: int = 2
) -> list[GuidelineHit]:
    """Most relevant guideline passages for a suggested code (system-aware)."""
    gsys = "ICD-10-PCS" if system == "ICD-10-PCS" else "ICD-10-CM" if system == "ICD-10-CM" else None
    if gsys is None:
        return []
    idx = get_index(db, gsys)
    if idx is None:
        return []
    try:
        hits = idx.search(concept, k=k, code=code, encounter=encounter)
        # keep secondary passages only when they are nearly as relevant as the best one
        return [h for i, h in enumerate(hits) if i == 0 or h.score >= 0.85 * hits[0].score]
    except Exception:  # noqa: BLE001 - citations are best-effort
        log.exception("guideline_search_failed")
        return []


def search(db: Session, query: str, system: str | None = None, k: int = 5) -> list[GuidelineHit]:
    hits: list[GuidelineHit] = []
    for sysname in [system] if system else ["ICD-10-CM", "ICD-10-PCS"]:
        idx = get_index(db, sysname)
        if idx:
            hits.extend(idx.search(query, k=k))
    hits.sort(key=lambda h: -h.score)
    return hits[:k]


def group_by_ref(hits: list[GuidelineHit]) -> dict[str, list[GuidelineHit]]:
    out: dict[str, list[GuidelineHit]] = defaultdict(list)
    for h in hits:
        out[h.ref].append(h)
    return out
