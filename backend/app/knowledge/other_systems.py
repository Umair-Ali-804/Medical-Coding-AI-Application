"""Loaders for HCPCS Level II and CPT.

HCPCS Level II: public CMS file (https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update).
Accepts the CMS annual/quarterly XLSX or a CSV export of it. Columns are found
by header name ("HCPC", "LONG DESCRIPTION", optional "TERM DT" for terminated codes).

CPT: copyright American Medical Association. CPT content is NOT bundled with this
project and must never be scraped from the web. Load it only from a file you are
licensed to use (AMA data file license or a licensed distributor). The loader
requires an explicit license acknowledgement and records it in the KB metadata.
"""

from __future__ import annotations

import csv
from collections import OrderedDict
from pathlib import Path

from app.knowledge.icd10cm import CodeRecord


def _rows_from_file(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        try:
            import openpyxl  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Reading XLSX requires `pip install openpyxl` (or export to CSV)") from exc
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = [str(h or "").strip().upper() for h in next(it)]
        return [
            {header[i]: ("" if v is None else str(v)) for i, v in enumerate(row) if i < len(header)}
            for row in it
        ]
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [{(k or "").strip().upper(): (v or "") for k, v in row.items()} for row in reader]


def _pick(row: dict[str, str], *names: str) -> str:
    for n in names:
        if n in row and row[n].strip():
            return row[n].strip()
    return ""


def parse_hcpcs(path: str | Path) -> list[CodeRecord]:
    rows = _rows_from_file(Path(path))
    by_code: OrderedDict[str, list[str]] = OrderedDict()
    terminated: set[str] = set()
    for row in rows:
        code = _pick(row, "HCPC", "HCPCS", "CODE").upper()
        if not code or len(code) != 5:
            continue
        desc = _pick(row, "LONG DESCRIPTION", "LONG_DESCRIPTION", "DESCRIPTION", "SHORT DESCRIPTION")
        if _pick(row, "TERM DT", "TERM_DT", "TERMINATION DATE"):
            terminated.add(code)
        by_code.setdefault(code, []).append(desc)
    records = []
    for code, parts in by_code.items():
        if code in terminated:
            continue
        records.append(
            CodeRecord(
                code=code,
                description=" ".join(p for p in parts if p).strip(),
                billable=True,
                parent_code=None,
                category=code[0],
                chapter=code[0],
                chapter_description=None,
                section=None,
                section_description=None,
            )
        )
    return records


def parse_cpt(path: str | Path, *, license_acknowledged: bool) -> list[CodeRecord]:
    if not license_acknowledged:
        raise PermissionError(
            "CPT is AMA-copyrighted. Pass --i-have-a-cpt-license only if your organization holds a valid "
            "CPT license covering this use."
        )
    rows = _rows_from_file(Path(path))
    records = []
    for row in rows:
        code = _pick(row, "CODE", "CPT", "CPT CODE").upper()
        desc = _pick(row, "LONG DESCRIPTION", "DESCRIPTION", "LONG_DESCRIPTION", "DESCRIPTOR")
        if code and desc:
            records.append(
                CodeRecord(
                    code=code,
                    description=desc,
                    billable=True,
                    parent_code=None,
                    category=code[:2],
                    chapter=None,
                    chapter_description=None,
                    section=None,
                    section_description=None,
                )
            )
    return records
