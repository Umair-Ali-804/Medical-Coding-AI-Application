"""Loaders for the official CMS / CDC reference files, in the formats they are published in.

Every parser returns plain records; persistence lives in `repository` (code sets) and
`edits` (claim edits). Supported inputs:

| File (as published)                               | Parser                   | Loaded as          |
|---------------------------------------------------|--------------------------|--------------------|
| icd10cm_order_<YEAR>.txt   (CMS/CDC, fixed width) | parse_icd10cm_order      | ICD-10-CM          |
| icd10cm_codes_<YEAR>.txt   (code + description)   | parse_icd10cm_codes      | ICD-10-CM          |
| icd10pcs_codes_<YEAR>.txt / icd10pcs_order_<YEAR>  | parse_icd10pcs           | ICD-10-PCS         |
| HCPC<YEAR>_<MON>_ANWEB*.txt (fixed width)         | parse_hcpcs_anweb        | HCPCS (+modifiers) |
| cpt_codes.csv (your licensed CPT extract)         | parse_cpt_csv            | CPT                |
| *MUE*PractitionerServices*.txt / OutpatientHosp.. | parse_mue                | MUE-PRAC / MUE-OPH |
| proc_notes_<MON><YEAR>.txt (HCPCS processing notes)| parse_proc_notes        | HCPCS-NOTES        |
| NCCI PTP edits (ccipra*/ccioph*.txt|xlsx|csv)     | parse_ncci_ptp           | NCCI-PTP           |
| fee schedule CSV (code, amount)                   | parse_fee_schedule       | FEE                |
| coverage crosswalk CSV (procedure, icd10)         | parse_coverage           | COVERAGE           |
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.knowledge.icd10cm import CodeRecord

# ----------------------------------------------------------------------------- shared helpers


@dataclass
class EditRow:
    code: str
    code2: str | None = None
    value: float | None = None
    indicator: str | None = None
    text: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None


@dataclass
class LoadedSet:
    """Result of parsing one file: what it is, which version, and its validity window."""

    kind: str  # code system ("ICD-10-PCS") or edit set ("MUE-PRAC")
    version: str
    records: list = field(default_factory=list)  # CodeRecord | EditRow
    effective_from: date | None = None
    effective_to: date | None = None
    extra: dict = field(default_factory=dict)  # e.g. HCPCS modifiers


def fiscal_year_window(year: int) -> tuple[date, date]:
    """ICD-10-CM/PCS fiscal year N is valid for discharges/dates of service Oct 1 (N-1) - Sep 30 (N)."""
    return date(year - 1, 10, 1), date(year, 9, 30)


def _year_from_name(path: Path, default: int = 2026) -> int:
    m = re.search(r"(20\d\d)", path.name)
    return int(m.group(1)) if m else default


def _yyyymmdd(v: str) -> date | None:
    v = (v or "").strip()
    if len(v) == 8 and v.isdigit():
        try:
            return date(int(v[:4]), int(v[4:6]), int(v[6:]))
        except ValueError:
            return None
    return None


_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
    )
}


def _quarter_window(year: int, month: int) -> tuple[date, date]:
    q_start = ((month - 1) // 3) * 3 + 1
    end_month = q_start + 2
    end_day = {3: 31, 6: 30, 9: 30, 12: 31}[end_month]
    return date(year, q_start, 1), date(year, end_month, end_day)


# ----------------------------------------------------------------------------- ICD-10-CM

# Chapter ranges (category level) for files that carry no chapter information.
ICD10CM_CHAPTERS: list[tuple[str, str, str, str]] = [
    ("1", "A00", "B99", "Certain infectious and parasitic diseases"),
    ("2", "C00", "D49", "Neoplasms"),
    (
        "3",
        "D50",
        "D89",
        "Diseases of the blood and blood-forming organs and certain disorders involving the immune mechanism",
    ),
    ("4", "E00", "E89", "Endocrine, nutritional and metabolic diseases"),
    ("5", "F01", "F99", "Mental, Behavioral and Neurodevelopmental disorders"),
    ("6", "G00", "G99", "Diseases of the nervous system"),
    ("7", "H00", "H59", "Diseases of the eye and adnexa"),
    ("8", "H60", "H95", "Diseases of the ear and mastoid process"),
    ("9", "I00", "I99", "Diseases of the circulatory system"),
    ("10", "J00", "J99", "Diseases of the respiratory system"),
    ("11", "K00", "K95", "Diseases of the digestive system"),
    ("12", "L00", "L99", "Diseases of the skin and subcutaneous tissue"),
    ("13", "M00", "M99", "Diseases of the musculoskeletal system and connective tissue"),
    ("14", "N00", "N99", "Diseases of the genitourinary system"),
    ("15", "O00", "O9A", "Pregnancy, childbirth and the puerperium"),
    ("16", "P00", "P96", "Certain conditions originating in the perinatal period"),
    ("17", "Q00", "Q99", "Congenital malformations, deformations and chromosomal abnormalities"),
    (
        "18",
        "R00",
        "R99",
        "Symptoms, signs and abnormal clinical and laboratory findings, not elsewhere classified",
    ),
    ("19", "S00", "T88", "Injury, poisoning and certain other consequences of external causes"),
    ("20", "V00", "Y99", "External causes of morbidity"),
    ("21", "Z00", "Z99", "Factors influencing health status and contact with health services"),
    ("22", "U00", "U85", "Codes for special purposes"),
]


def icd10cm_chapter(code: str) -> tuple[str | None, str | None]:
    cat = code.replace(".", "")[:3].upper()
    for num, lo, hi, desc in ICD10CM_CHAPTERS:
        if lo <= cat <= hi:
            return num, desc
    return None, None


def _dotted(raw: str) -> str:
    raw = raw.strip().upper()
    return raw if len(raw) <= 3 else f"{raw[:3]}.{raw[3:]}"


def parse_icd10cm_order(path: str | Path) -> LoadedSet:
    """CMS `icd10cm_order_<YEAR>.txt`: order(5) code(7) billable(1) short(60) long.

    Includes non-billable headers, so the hierarchy (parent codes) can be rebuilt."""
    path = Path(path)
    year = _year_from_name(path)
    records: list[CodeRecord] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="latin-1").splitlines():
        if len(line) < 16:
            continue
        raw = line[6:13].strip()
        if not raw:
            continue
        code = _dotted(raw)
        billable = line[14:15] == "1"
        long_desc = line[77:].strip() or line[16:76].strip()
        parent = None
        compact = raw
        for n in range(len(compact) - 1, 2, -1):
            cand = _dotted(compact[:n])
            if cand in seen:
                parent = cand
                break
        ch, ch_desc = icd10cm_chapter(code)
        records.append(
            CodeRecord(
                code=code,
                description=long_desc,
                billable=billable,
                parent_code=parent,
                category=compact[:3],
                chapter=ch,
                chapter_description=ch_desc,
                section=None,
                section_description=None,
                attributes={"short_description": line[16:76].strip()},
            )
        )
        seen.add(code)
    start, end = fiscal_year_window(year)
    return LoadedSet("ICD-10-CM", str(year), records, start, end)


def parse_icd10cm_codes(path: str | Path) -> LoadedSet:
    """CMS `icd10cm_codes_<YEAR>.txt`: billable codes only, `CODE<spaces>Description`."""
    path = Path(path)
    year = _year_from_name(path)
    records = []
    for line in path.read_text(encoding="latin-1").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        code = _dotted(parts[0])
        ch, ch_desc = icd10cm_chapter(code)
        records.append(CodeRecord(code, parts[1].strip(), True, None, parts[0][:3], ch, ch_desc, None, None))
    start, end = fiscal_year_window(year)
    return LoadedSet("ICD-10-CM", str(year), records, start, end)


# ----------------------------------------------------------------------------- ICD-10-PCS

PCS_SECTIONS = {
    "0": "Medical and Surgical",
    "1": "Obstetrics",
    "2": "Placement",
    "3": "Administration",
    "4": "Measurement and Monitoring",
    "5": "Extracorporeal or Systemic Assistance and Performance",
    "6": "Extracorporeal or Systemic Therapies",
    "7": "Osteopathic",
    "8": "Other Procedures",
    "9": "Chiropractic",
    "B": "Imaging",
    "C": "Nuclear Medicine",
    "D": "Radiation Therapy",
    "F": "Physical Rehabilitation and Diagnostic Audiology",
    "G": "Mental Health",
    "H": "Substance Abuse Treatment",
    "X": "New Technology",
}


def parse_icd10pcs(path: str | Path) -> LoadedSet:
    """CMS `icd10pcs_codes_<YEAR>.txt` (`CODE Description`) or `icd10pcs_order_<YEAR>.txt`."""
    path = Path(path)
    year = _year_from_name(path)
    records = []
    is_order = "order" in path.name.lower()
    for line in path.read_text(encoding="latin-1").splitlines():
        if is_order:
            if len(line) < 16 or line[14:15] != "1":
                continue  # order file also lists 3-char table headers (flag 0)
            code, desc = line[6:13].strip(), (line[77:].strip() or line[16:76].strip())
        else:
            parts = line.strip().split(None, 1)
            if len(parts) != 2 or len(parts[0]) != 7:
                continue
            code, desc = parts[0], parts[1].strip()
        code = code.upper()
        section = PCS_SECTIONS.get(code[0], "Other")
        root_op = desc.split(" of ")[0].split(" ")[0] if code[0] == "0" else None
        records.append(
            CodeRecord(
                code=code,
                description=desc,
                billable=True,
                parent_code=None,
                category=code[:3],  # PCS table: section + body system + root operation
                chapter=code[0],
                chapter_description=section,
                section=code[:2],
                section_description=None,
                attributes={"section": section, "table": code[:3], "root_operation": root_op},
            )
        )
    start, end = fiscal_year_window(year)
    return LoadedSet("ICD-10-PCS", str(year), records, start, end)


# ----------------------------------------------------------------------------- HCPCS Level II

HCPCS_COVERAGE = {
    "C": "Carrier judgment",
    "D": "Special coverage instructions apply",
    "I": "Not payable by Medicare",
    "M": "Non-covered by Medicare",
    "S": "Non-covered by Medicare statute",
}


def parse_hcpcs_anweb(path: str | Path) -> LoadedSet:
    """CMS HCPCS quarterly `HCPC<YEAR>_<MON>_ANWEB*.txt` (fixed width, 293-char first lines).

    Record id (col 11): 3 = procedure first line, 4 = continuation, 7 = modifier first line,
    8 = modifier continuation. Positions follow the CMS record layout (1-based):
    1-5 HCPC, 12-91 long description, 92-119 short description, 120-127 pricing indicators,
    230 coverage code, 253-256 processing note, 257-259 BETOS, 261-265 type of service,
    269-276 add date, 277-284 action effective date, 285-292 termination date, 293 action code.
    """
    path = Path(path)
    lines = path.read_text(encoding="latin-1").splitlines()
    procs: dict[str, dict] = {}
    modifiers: dict[str, dict] = {}
    order: list[str] = []
    for line in lines:
        if len(line) < 12:
            continue
        rec = line[10]
        code = line[0:5].strip().upper()
        desc = line[11:91].rstrip()
        if rec in ("3", "7"):
            full = line.ljust(293)
            meta = {
                "short_description": full[91:119].strip(),
                "pricing_indicators": [
                    p for p in (full[119:121], full[121:123], full[123:125], full[125:127]) if p.strip()
                ],
                "coverage_code": full[229].strip() or None,
                "processing_note": full[252:256].strip() or None,
                "betos": full[256:259].strip() or None,
                "type_of_service": full[260:265].strip() or None,
                "added": _iso(_yyyymmdd(full[268:276])),
                "action_effective": _iso(_yyyymmdd(full[276:284])),
                "terminated": _iso(_yyyymmdd(full[284:292])),
                "action_code": full[292].strip() or None,
            }
            target = procs if rec == "3" else modifiers
            if code not in target:
                if rec == "3":
                    order.append(code)
                target[code] = {"parts": [desc.strip()], **meta}
        elif rec in ("4", "8"):
            target = procs if rec == "4" else modifiers
            if code in target:
                target[code]["parts"].append(desc.strip())

    m = re.search(r"HCPC(20\d\d)_([A-Z]{3})", path.name.upper())
    year = int(m.group(1)) if m else _year_from_name(path)
    month = _MONTHS.get(m.group(2).lower(), 1) if m else 1
    start, end = _quarter_window(year, month)
    version = f"{year}Q{(month - 1) // 3 + 1}"

    records = []
    for code in order:
        d = procs[code]
        term = d.get("terminated")
        active = term is None or date.fromisoformat(term) > start
        cov = d.get("coverage_code")
        attrs = {k: v for k, v in d.items() if k != "parts" and v not in (None, [], "")}
        if cov:
            attrs["coverage_description"] = HCPCS_COVERAGE.get(cov, cov)
        records.append(
            CodeRecord(
                code=code,
                description=" ".join(p for p in d["parts"] if p).strip(),
                billable=active,
                parent_code=None,
                category=code[0],
                chapter=code[0],
                chapter_description=None,
                section=None,
                section_description=None,
                attributes=attrs,
            )
        )
    mods = {
        k: {"description": " ".join(v["parts"]).strip(), "terminated": v.get("terminated")}
        for k, v in modifiers.items()
    }
    return LoadedSet("HCPCS", version, records, start, end, extra={"modifiers": mods})


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


# ----------------------------------------------------------------------------- CPT


def parse_cpt_csv(path: str | Path, *, license_acknowledged: bool) -> LoadedSet:
    """CPT extract as CSV. Accepts `code` plus any of long/short description columns and keeps
    the remaining columns (category, specialty, type, status...) as attributes."""
    if not license_acknowledged:
        raise PermissionError(
            "CPT is AMA-copyrighted. Load it only with --i-have-a-cpt-license if your organization holds a "
            "CPT license that covers this use."
        )
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    records = []
    years = set()
    seen: dict[str, CodeRecord] = {}
    duplicates = 0
    for row in rows:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        code = (r.get("code") or r.get("cpt") or r.get("cpt code") or "").upper()
        if code in seen:  # keep the first row; remember extra specialties
            duplicates += 1
            spec = r.get("specialty")
            specs = seen[code].attributes.setdefault("other_specialties", [])
            if spec and spec != seen[code].section_description and spec not in specs:
                specs.append(spec)
            continue
        desc = (
            r.get("long_description")
            or r.get("long description")
            or r.get("description")
            or r.get("descriptor")
            or r.get("short_description")
            or r.get("short description")
            or ""
        )
        if not code or not desc:
            continue
        status = r.get("status", "").lower()
        rng = re.match(r"^(\d{4}[0-9FTU])\s*-\s*(\d{4}[0-9FTU])$", code)
        is_range = bool(rng) or r.get("code_scope", "").lower() == "range"
        if r.get("effective_year", "").isdigit():
            years.add(int(r["effective_year"]))
        attrs = {
            k: v
            for k, v in r.items()
            if k not in ("code", "long_description", "description", "short_description") and v
        }
        if is_range and rng:
            # a range row ("12001-12007") is guidance, not a reportable code: the coder picks the member code
            attrs.update({"range": True, "range_start": rng.group(1), "range_end": rng.group(2)})
        rec = CodeRecord(
            code=code,
            description=desc,
            billable=status in ("", "active") and not is_range,
            parent_code=None,
            category=code[:2],
            chapter=r.get("category") or None,
            chapter_description=r.get("category") or None,
            section=None,
            section_description=r.get("specialty") or None,
            attributes=attrs,
        )
        seen[code] = rec
        records.append(rec)
    year = max(years) if years else _year_from_name(path)
    return LoadedSet(
        "CPT", str(year), records, date(year, 1, 1), date(year, 12, 31), extra={"duplicates": duplicates}
    )


# ----------------------------------------------------------------------------- MUE

_DOS_RANGE = re.compile(
    r"dates of service\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\s*-\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})",
    re.IGNORECASE,
)


def _month_date(mon: str, day: str, year: str) -> date | None:
    m = _MONTHS.get(mon[:3].lower())
    return date(int(year), m, int(day)) if m else None


def mue_set_name(path: Path) -> str:
    n = path.name.lower()
    if "outpatient" in n or "oph" in n:
        return "MUE-OPH"
    if "dme" in n:
        return "MUE-DME"
    return "MUE-PRAC"


def parse_mue(path: str | Path) -> LoadedSet:
    """CMS NCCI MUE table (tab separated, preceded by a copyright/validity header).

    Columns: HCPCS/CPT code, MUE value, [MUE Adjudication Indicator], MUE rationale."""
    path = Path(path)
    text = path.read_text(encoding="latin-1")
    start = end = None
    m = _DOS_RANGE.search(text[:3000])
    if m:
        start = _month_date(m.group(1), m.group(2), m.group(3))
        end = _month_date(m.group(4), m.group(5), m.group(6))
    rows: list[EditRow] = []
    header: list[str] | None = None
    for line in text.splitlines():
        cols = [c.strip().strip('"') for c in line.split("\t")]
        if header is None:
            if len(cols) >= 2 and "code" in cols[0].lower() and "mue" in " ".join(cols).lower():
                header = [c.lower() for c in cols]
            continue
        if len(cols) < 2 or not cols[0]:
            continue
        try:
            value = float(cols[1])
        except ValueError:
            continue
        mai_idx = next((i for i, h in enumerate(header) if "adjudication" in h), None)
        rat_idx = next((i for i, h in enumerate(header) if "rationale" in h), None)
        rows.append(
            EditRow(
                code=cols[0].upper(),
                value=value,
                indicator=(cols[mai_idx][:1] if mai_idx is not None and mai_idx < len(cols) else None),
                text=(cols[rat_idx] if rat_idx is not None and rat_idx < len(cols) else None),
                effective_from=start,
                effective_to=end,
            )
        )
    version = f"{start:%Y-%m}" if start else str(_year_from_name(path))
    return LoadedSet(mue_set_name(path), version, rows, start, end)


# ----------------------------------------------------------------------------- HCPCS processing notes

_NOTE_START = re.compile(r"^\s*(\d{4})--(.*)$")


def parse_proc_notes(path: str | Path) -> LoadedSet:
    path = Path(path)
    notes: dict[str, list[str]] = {}
    current = None
    for raw in path.read_text(encoding="latin-1").splitlines():
        line = raw.rstrip().rstrip("*").rstrip()
        m = _NOTE_START.match(line)
        if m:
            current = m.group(1)
            notes[current] = [m.group(2).strip()]
        elif current and line.strip() and not line.strip().startswith("_"):
            notes[current].append(line.strip())
    rows = [
        EditRow(code=k, text=" ".join(v).strip())
        for k, v in notes.items()
        if "DELETED" not in " ".join(v).upper()[:60]
    ]
    m = re.search(r"([A-Z]{3})(20\d\d)", path.name.upper())
    if m and m.group(1).lower() in _MONTHS:
        start, end = _quarter_window(int(m.group(2)), _MONTHS[m.group(1).lower()])
        version = f"{m.group(2)}Q{(_MONTHS[m.group(1).lower()] - 1) // 3 + 1}"
    else:
        start = end = None
        version = str(_year_from_name(path))
    return LoadedSet("HCPCS-NOTES", version, rows, start, end)


# ----------------------------------------------------------------------------- NCCI PTP (not provided yet)


def _table_rows(path: Path) -> list[list[str]]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl  # type: ignore

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        return [
            ["" if v is None else str(v).strip() for v in row]
            for row in wb.active.iter_rows(values_only=True)
        ]
    text = path.read_text(encoding="latin-1")
    delim = "\t" if text.count("\t") > text.count(",") else ","
    return [[c.strip().strip('"') for c in row] for row in csv.reader(text.splitlines(), delimiter=delim)]


def parse_ncci_ptp(path: str | Path) -> LoadedSet:
    """CMS NCCI procedure-to-procedure edits (practitioner `ccipra-*` or hospital `ccioph-*`).

    Columns (header row detected automatically): Column 1, Column 2, *=in existence prior to 1996,
    Effective Date, Deletion Date, Modifier (0 not allowed / 1 allowed / 9 n/a), PTP Edit Rationale."""
    path = Path(path)
    rows = _table_rows(path)
    hdr_idx = next(
        (
            i
            for i, r in enumerate(rows[:30])
            if any("column 1" in c.lower() for c in r) and any("column 2" in c.lower() for c in r)
        ),
        None,
    )
    if hdr_idx is None:
        raise ValueError(f"{path.name}: could not find the 'Column 1 / Column 2' header row")
    header = [c.lower() for c in rows[hdr_idx]]

    def col(*names: str) -> int | None:
        return next((i for i, h in enumerate(header) if any(n in h for n in names)), None)

    c1, c2 = col("column 1"), col("column 2")
    eff, dele, mod, rat = col("effective"), col("deletion"), col("modifier"), col("rationale")
    out: list[EditRow] = []
    for r in rows[hdr_idx + 1 :]:
        if c1 is None or c2 is None or len(r) <= max(c1, c2) or not r[c1] or not r[c2]:
            continue
        deletion = _yyyymmdd(r[dele]) if dele is not None and dele < len(r) else None
        out.append(
            EditRow(
                code=r[c1].upper(),
                code2=r[c2].upper(),
                indicator=(r[mod][:1] if mod is not None and mod < len(r) else None),
                text=(r[rat] if rat is not None and rat < len(r) else None),
                effective_from=_yyyymmdd(r[eff]) if eff is not None and eff < len(r) else None,
                effective_to=deletion,
            )
        )
    m = re.search(r"v(\d{3})r(\d)", path.name.lower())
    version = f"v{m.group(1)}r{m.group(2)}" if m else str(_year_from_name(path))
    return LoadedSet("NCCI-PTP", version, out)


# ----------------------------------------------------------------------------- fee schedule / coverage (optional)


def parse_fee_schedule(path: str | Path) -> LoadedSet:
    """Any CSV with a code column and an amount column (e.g. exported from the CMS PFS Look-up Tool
    or your payer contracts): `code,amount[,modifier]`. Used to estimate dollars at risk."""
    path = Path(path)
    rows = _table_rows(path)
    header = [c.lower() for c in rows[0]]
    ci = next(i for i, h in enumerate(header) if h in ("code", "hcpcs", "cpt", "hcpc", "procedure_code"))
    ai = next(
        i
        for i, h in enumerate(header)
        if any(k in h for k in ("amount", "allowed", "fee", "price", "rate", "payment"))
    )
    out = []
    for r in rows[1:]:
        try:
            out.append(EditRow(code=r[ci].upper(), value=float(r[ai].replace("$", "").replace(",", ""))))
        except (ValueError, IndexError):
            continue
    return LoadedSet("FEE", str(_year_from_name(path)), out)


def parse_coverage(path: str | Path) -> LoadedSet:
    """Medical-necessity crosswalk: CSV with a procedure column and an ICD-10-CM column.

    ICD-10 values may be exact codes (E11.9), prefixes ending in '-' or '*' (E11.-) or ranges (E08-E13).
    Build it from the CMS Medicare Coverage Database (LCD articles: article_x_hcpc_code joined with
    article_x_icd10_covered on article id + group) or from your payer policies."""
    path = Path(path)
    rows = _table_rows(path)
    header = [c.lower() for c in rows[0]]
    pi = next(i for i, h in enumerate(header) if any(k in h for k in ("hcpc", "cpt", "procedure")))
    di = next(i for i, h in enumerate(header) if "icd" in h or "diag" in h)
    gi = next((i for i, h in enumerate(header) if "policy" in h or "article" in h or "lcd" in h), None)
    out = []
    for r in rows[1:]:
        if len(r) <= max(pi, di) or not r[pi] or not r[di]:
            continue
        out.append(
            EditRow(
                code=r[pi].upper(),
                code2=r[di].upper().replace(" ", ""),
                text=(r[gi] if gi is not None and gi < len(r) else None),
            )
        )
    return LoadedSet("COVERAGE", str(_year_from_name(path)), out)


# ----------------------------------------------------------------------------- detection


@dataclass
class DetectedFile:
    path: Path
    kind: str  # icd10cm_tabular | icd10cm_order | icd10cm_codes | icd10pcs | hcpcs | cpt | mue | proc_notes | ptp | fee | coverage | guidelines


def detect_files(data_dir: str | Path) -> list[DetectedFile]:
    """Recognize reference files by their official names (case-insensitive)."""
    out = []
    for p in sorted(Path(data_dir).rglob("*")):
        if not p.is_file():
            continue
        n = p.name.lower()
        kind = None
        if "icd10cm" in n.replace("-", "").replace("_", "") and "tabular" in n and n.endswith(".xml"):
            kind = (
                "icd10cm_tabular"  # CDC icd10cm-tabular-<YEAR>.xml (Excludes1, code first, use additional...)
            )
        elif n.startswith("icd10cm_order") and n.endswith(".txt"):
            kind = "icd10cm_order"
        elif n.startswith("icd10cm_codes") and n.endswith(".txt"):
            kind = "icd10cm_codes"
        elif n.startswith("icd10pcs_") and n.endswith(".txt"):
            kind = "icd10pcs"
        elif n.startswith("hcpc") and "anweb" in n and n.endswith((".txt", ".xlsx", ".csv")):
            kind = "hcpcs"
        elif "cpt" in n and n.endswith(".csv"):
            kind = "cpt"
        elif "mue" in n and n.endswith((".txt", ".csv")):
            kind = "mue"
        elif n.startswith("proc_notes") and n.endswith(".txt"):
            kind = "proc_notes"
        elif (n.startswith(("ccipra", "ccioph", "ptp")) or "ptp" in n) and n.endswith(
            (".txt", ".xlsx", ".csv")
        ):
            kind = "ptp"
        elif n.startswith(("fee", "pfs")) and n.endswith(".csv"):
            kind = "fee"
        elif n.startswith(("coverage", "lcd")) and n.endswith(".csv"):
            kind = "coverage"
        elif n.endswith(".pdf") and ("guideline" in n or "guidline" in n):
            kind = "guidelines"
        if kind:
            out.append(DetectedFile(p, kind))
    return out
