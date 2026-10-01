"""One place that turns reference files into loaded, versioned knowledge.

Used by the CLI (`python -m app.cli kb load-all --data-dir ...`), the Colab notebook and tests.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.knowledge import cms_loaders as L
from app.knowledge import edits, guidelines, repository
from app.knowledge.cms_loaders import EditRow, LoadedSet
from app.knowledge.icd10cm import default_tabular_path, file_checksum, parse_tabular
from app.models import KnowledgeBaseVersion

log = logging.getLogger(__name__)
Printer = Callable[[str], None]


@dataclass
class LoadReport:
    lines: list[str] = field(default_factory=list)
    loaded: dict[str, str] = field(default_factory=dict)  # kind -> version
    warnings: list[str] = field(default_factory=list)

    def add(self, msg: str, out: Printer | None = None) -> None:
        self.lines.append(msg)
        if out:
            out(msg)


def load_code_set(db: Session, ls: LoadedSet, path: Path, note: str = "") -> KnowledgeBaseVersion:
    return repository.load_records(
        db,
        ls.kind,
        ls.version,
        ls.records,
        source=f"{ls.kind} {path.name}{note}",
        checksum=file_checksum(path),
        effective_from=ls.effective_from,
        effective_to=ls.effective_to,
    )


def load_edit(db: Session, ls: LoadedSet, path: Path) -> KnowledgeBaseVersion:
    return edits.load_edit_set(
        db,
        ls.kind,
        ls.version,
        ls.records,
        source=f"{ls.kind} {path.name}",
        checksum=file_checksum(path),
        effective_from=ls.effective_from,
        effective_to=ls.effective_to,
    )


def load_icd10cm(
    db: Session,
    order_file: Path | None,
    codes_file: Path | None,
    report: LoadReport,
    out: Printer | None,
    tabular_file: Path | None = None,
) -> None:
    """ICD-10-CM: official tabular XML (instructional notes) + CMS order file (authoritative validity).

    The tabular's 7th-character expansion can produce combinations CMS does not publish as valid
    (e.g. S06.1X7D); the order file decides which codes are billable."""
    # A CDC tabular XML placed with the reference files wins over the one bundled with simple-icd-10-cm
    # (the bundled one lags behind: e.g. FY2026 while the FY2027 code file is loaded).
    tab = tabular_file or default_tabular_path()
    official = order_file or codes_file
    auth = (
        (L.parse_icd10cm_order(order_file) if order_file else L.parse_icd10cm_codes(codes_file))
        if official
        else None
    )
    if tab:
        version, records = parse_tabular(tab)
        if auth and auth.version and version != auth.version:
            report.warnings.append(
                f"ICD-10-CM tabular XML is {version} but the CMS code file is {auth.version}: validity follows "
                f"{auth.version}, instructional notes (Excludes1, code first, use additional code) are from "
                f"{version}. Add icd10cm-tabular-{auth.version}.xml (CDC) to the reference folder."
            )
            version = auth.version
        eff = (
            (auth.effective_from, auth.effective_to)
            if auth
            else L.fiscal_year_window(int(version) if version.isdigit() else 2026)
        )
        note = ""
        if auth:
            valid = {r.code for r in auth.records if r.billable}
            known = {r.code for r in records}
            demoted = 0
            for r in records:
                if r.billable and r.code not in valid:
                    r.billable = False
                    r.attributes = {**r.attributes, "not_in_cms_code_file": True}
                    demoted += 1
            missing = [r for r in auth.records if r.code not in known]
            records.extend(missing)
            note = f" + {official.name} (validity)"
            report.add(
                f"  ICD-10-CM cross-check vs {official.name}: {demoted} tabular codes are not valid per CMS "
                f"(marked non-billable), {len(missing)} codes only in the CMS file (added)",
                out,
            )
        ls = LoadedSet("ICD-10-CM", version, records, *eff)
        kbv = load_code_set(db, ls, Path(tab), note)
    elif auth:
        kbv = load_code_set(db, auth, official)
        report.warnings.append(
            "ICD-10-CM loaded from the CMS code file only (no Excludes1 / Use-additional-code notes). "
            "`pip install simple-icd-10-cm` or pass the CDC tabular XML for full validation."
        )
    else:
        report.warnings.append("No ICD-10-CM source found")
        return
    report.loaded["ICD-10-CM"] = kbv.version
    report.add(
        f"ICD-10-CM {kbv.version}: {kbv.code_count} codes, valid {kbv.effective_from} to {kbv.effective_to}",
        out,
    )


def load_directory(
    db: Session,
    data_dir: str | Path,
    *,
    cpt_license: bool = False,
    out: Printer | None = print,
) -> LoadReport:
    found = L.detect_files(data_dir)
    if not found:
        report = LoadReport()
        report.add(f"WARNING: No reference files recognized in {data_dir}", out)
        report.warnings.append(f"No reference files recognized in {data_dir}")
        return report
    return load_files(db, found, cpt_license=cpt_license, out=out, label=str(data_dir))


def load_file(
    db: Session,
    path: str | Path,
    kind: str | None = None,
    *,
    cpt_license: bool = False,
    out: Printer | None = print,
) -> LoadReport:
    p = Path(path)
    if kind is None:
        hits = [f for f in L.detect_files(p.parent) if f.path == p]
        if not hits:
            raise ValueError(f"Cannot tell what {p.name} is; pass --kind")
        kind = hits[0].kind
    return load_files(db, [L.DetectedFile(p, kind)], cpt_license=cpt_license, out=out, label=p.name)


def load_files(
    db: Session,
    found: list[L.DetectedFile],
    *,
    cpt_license: bool = False,
    out: Printer | None = print,
    label: str = "",
) -> LoadReport:
    report = LoadReport()
    by_kind: dict[str, list[Path]] = {}
    for f in found:
        by_kind.setdefault(f.kind, []).append(f.path)
    report.add(
        f"Recognized {len(found)} file(s) in {label}: "
        + ", ".join(f"{k}={len(v)}" for k, v in by_kind.items()),
        out,
    )

    # newest release wins when several fiscal years are present (file names carry the year)
    def newest(kind: str) -> Path | None:
        files = sorted(by_kind.get(kind, []), key=lambda p: p.name)
        return files[-1] if files else None

    order, codes, tabular = newest("icd10cm_order"), newest("icd10cm_codes"), newest("icd10cm_tabular")
    if order or codes:
        load_icd10cm(db, order, codes, report, out, tabular)
        db.commit()

    for p in sorted(by_kind.get("icd10pcs", []), key=lambda p: p.name)[-1:]:
        ls = L.parse_icd10pcs(p)
        kbv = load_code_set(db, ls, p)
        report.loaded["ICD-10-PCS"] = kbv.version
        report.add(
            f"ICD-10-PCS {kbv.version}: {kbv.code_count} codes, valid {kbv.effective_from} to {kbv.effective_to}",
            out,
        )
        db.commit()

    for p in sorted(by_kind.get("hcpcs", []))[-1:]:
        ls = L.parse_hcpcs_anweb(p) if p.suffix.lower() == ".txt" else _hcpcs_table(p)
        kbv = load_code_set(db, ls, p)
        report.loaded["HCPCS"] = kbv.version
        active = sum(r.billable for r in ls.records)
        report.add(
            f"HCPCS {kbv.version}: {kbv.code_count} codes ({active} active), valid {kbv.effective_from} to {kbv.effective_to}",
            out,
        )
        mods = ls.extra.get("modifiers") or {}
        if mods:
            rows = [
                EditRow(code=k, text=v["description"], effective_to=_d(v.get("terminated")))
                for k, v in mods.items()
            ]
            ms = LoadedSet("MODIFIERS", ls.version, rows, ls.effective_from, ls.effective_to)
            load_edit(db, ms, p)
            report.add(f"HCPCS modifiers {ls.version}: {len(rows)}", out)
        db.commit()

    for p in by_kind.get("cpt", [])[:1]:
        if not cpt_license:
            report.warnings.append(
                f"{p.name} skipped: CPT is AMA-licensed. Re-run with --i-have-a-cpt-license if your organization holds a license."
            )
            continue
        ls = L.parse_cpt_csv(p, license_acknowledged=True)
        kbv = load_code_set(db, ls, p, " (licensed; acknowledged)")
        report.loaded["CPT"] = kbv.version
        dup = ls.extra.get("duplicates")
        report.add(
            f"CPT {kbv.version}: {kbv.code_count} codes (your licensed extract)"
            + (f"; {dup} duplicate rows merged" if dup else ""),
            out,
        )
        db.commit()

    for p in by_kind.get("mue", []):
        ls = L.parse_mue(p)
        kbv = load_edit(db, ls, p)
        report.loaded[ls.kind] = kbv.version
        report.add(
            f"{ls.kind} {kbv.version}: {kbv.code_count} unit limits, valid {kbv.effective_from} to {kbv.effective_to}",
            out,
        )
        db.commit()

    for p in sorted(by_kind.get("proc_notes", []), key=lambda p: p.name)[-1:]:
        ls = L.parse_proc_notes(p)
        kbv = load_edit(db, ls, p)
        report.loaded[ls.kind] = kbv.version
        report.add(f"HCPCS processing notes {kbv.version}: {kbv.code_count}", out)
        db.commit()

    ptp_rows: list[EditRow] = []
    ptp_version = None
    for p in by_kind.get("ptp", []):
        ls = L.parse_ncci_ptp(p)
        ptp_rows.extend(ls.records)
        ptp_version = ls.version
    if ptp_rows:
        ls = LoadedSet("NCCI-PTP", ptp_version or "current", ptp_rows)
        kbv = load_edit(db, ls, by_kind["ptp"][0])
        report.loaded["NCCI-PTP"] = kbv.version
        report.add(f"NCCI PTP {kbv.version}: {kbv.code_count} code pairs", out)
        db.commit()

    for kind, parser in (("fee", L.parse_fee_schedule), ("coverage", L.parse_coverage)):
        for p in by_kind.get(kind, [])[:1]:
            ls = parser(p)
            kbv = load_edit(db, ls, p)
            report.loaded[ls.kind] = kbv.version
            report.add(f"{ls.kind} {kbv.version}: {kbv.code_count} rows", out)
            db.commit()

    for p in by_kind.get("guidelines", []):
        system, version, n = guidelines.load_pdf(db, p)
        report.loaded[f"GUIDELINES {system}"] = version
        report.add(f"{system} Official Guidelines FY{version}: {n} passages from {p.name}", out)
        db.commit()

    report.warnings.extend(freshness_warnings(db, date.today()))
    for w in report.warnings:
        report.add(f"WARNING: {w}", out)
    return report


def _hcpcs_table(p: Path) -> LoadedSet:
    from app.knowledge.other_systems import parse_hcpcs

    m = re.search(r"(20\d\d)", p.name)
    year = int(m.group(1)) if m else date.today().year
    return LoadedSet("HCPCS", str(year), parse_hcpcs(p), date(year, 1, 1), date(year, 12, 31))


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def active_versions(db: Session) -> list[KnowledgeBaseVersion]:
    return list(
        db.execute(
            select(KnowledgeBaseVersion)
            .where(KnowledgeBaseVersion.active.is_(True))
            .order_by(KnowledgeBaseVersion.code_system)
        ).scalars()
    )


def freshness_warnings(db: Session, on: date) -> list[str]:
    """Reference sets that are not valid for date of service `on` (expired or not yet effective)."""
    out = []
    for v in active_versions(db):
        if v.effective_to and on > v.effective_to:
            out.append(
                f"{v.code_system} {v.version} expired on {v.effective_to} (date checked: {on}). "
                f"Load the current release before coding/billing dates of service after {v.effective_to}."
            )
        elif v.effective_from and on < v.effective_from:
            out.append(f"{v.code_system} {v.version} is not effective until {v.effective_from}")
    return out
