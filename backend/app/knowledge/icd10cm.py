"""Parser for the official ICD-10-CM Tabular List XML (CDC/NCHS, public domain).

Source: https://www.cdc.gov/nchs/icd/icd-10-cm/files.html  ->  icd10cm-tabular-<YEAR>.xml
(also bundled in the `simple-icd-10-cm` PyPI package as a convenience).

Produces one record per code, including non-billable headers, with:
  * billable flag (leaf codes, and 7th-character extensions where required)
  * 7th-character expansion with 'X' placeholders (S93.401 + A -> S93.401A, T78.40 -> T78.40XA)
  * own instructional notes (inclusion terms, includes, excludes1/2, code first,
    use additional code, code also). Notes are inherited down the hierarchy at
    query time via `parent_code`.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET  # noqa: S405 - trusted, official source file
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CodeRecord:
    code: str
    description: str
    billable: bool
    parent_code: str | None
    category: str
    chapter: str | None
    chapter_description: str | None
    section: str | None
    section_description: str | None
    inclusion_terms: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    excludes1: list[str] = field(default_factory=list)
    excludes2: list[str] = field(default_factory=list)
    code_first: list[str] = field(default_factory=list)
    use_additional_code: list[str] = field(default_factory=list)
    code_also: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)


_NOTE_TAGS = {
    "inclusionTerm": "inclusion_terms",
    "includes": "includes",
    "excludes1": "excludes1",
    "excludes2": "excludes2",
    "codeFirst": "code_first",
    "useAdditionalCode": "use_additional_code",
    "codeAlso": "code_also",
}


def _notes(elem: ET.Element, tag: str) -> list[str]:
    out: list[str] = []
    for child in elem.findall(tag):
        out.extend((n.text or "").strip() for n in child.findall("note") if (n.text or "").strip())
    return out


def _seven_chr(elem: ET.Element) -> dict[str, str] | None:
    d = elem.find("sevenChrDef")
    if d is None:
        return None
    return {e.get("char", ""): (e.text or "").strip() for e in d.findall("extension")}


def _with_seventh(code: str, char: str) -> str:
    raw = code.replace(".", "")
    raw = raw.ljust(6, "X")
    return f"{raw[:3]}.{raw[3:]}{char}"


def parse_tabular(path: str | Path) -> tuple[str, list[CodeRecord]]:
    """Return (version, records)."""
    tree = ET.parse(str(path))  # noqa: S314 - trusted official file
    root = tree.getroot()
    version = (root.findtext("version") or "").strip() or "unknown"
    records: list[CodeRecord] = []

    for chapter in root.findall("chapter"):
        ch_name = (chapter.findtext("name") or "").strip()
        ch_desc = (chapter.findtext("desc") or "").strip()
        for section in chapter.findall("section"):
            sec_id = section.get("id")
            sec_desc = (section.findtext("desc") or "").strip()
            for diag in section.findall("diag"):
                records.extend(_walk(diag, None, None, ch_name, ch_desc, sec_id, sec_desc))
    return version, records


def _walk(
    diag: ET.Element,
    parent: str | None,
    inherited_7th: dict[str, str] | None,
    ch: str,
    ch_desc: str,
    sec: str | None,
    sec_desc: str,
) -> Iterator[CodeRecord]:
    code = (diag.findtext("name") or "").strip()
    desc = (diag.findtext("desc") or "").strip()
    seventh = _seven_chr(diag) or inherited_7th
    children = diag.findall("diag")
    notes = {attr: _notes(diag, tag) for tag, attr in _NOTE_TAGS.items()}
    is_leaf = not children
    needs_7th = is_leaf and seventh is not None

    yield CodeRecord(
        code=code,
        description=desc,
        billable=is_leaf and not needs_7th,
        parent_code=parent,
        category=code[:3],
        chapter=ch,
        chapter_description=ch_desc,
        section=sec,
        section_description=sec_desc,
        **notes,
    )
    if needs_7th and seventh:
        for char, ext_desc in seventh.items():
            yield CodeRecord(
                code=_with_seventh(code, char),
                description=f"{desc}, {ext_desc}",
                billable=True,
                parent_code=code,
                category=code[:3],
                chapter=ch,
                chapter_description=ch_desc,
                section=sec,
                section_description=sec_desc,
            )
    for child in children:
        yield from _walk(child, code, seventh, ch, ch_desc, sec, sec_desc)


def file_checksum(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def default_tabular_path() -> Path | None:
    """Locate the tabular XML bundled with the optional `simple-icd-10-cm` package."""
    try:
        import simple_icd_10_cm  # type: ignore
    except ImportError:
        return None
    data = Path(simple_icd_10_cm.__file__).parent / "data"
    matches = sorted(data.glob("icd10c*-tabular-*.xml"))
    return matches[-1] if matches else None
