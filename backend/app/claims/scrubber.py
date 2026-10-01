"""Pre-bill claim scrubber: catch the coding errors that turn into denials *before* the claim goes out.

Checks a claim (diagnoses + service lines with units/modifiers/pointers) against the loaded reference
data and reports each problem with the denial it typically produces (CARC/RARC), the rule source and
a suggested fix. Every check is deterministic and data-driven; nothing here calls an LLM.

Reference data used (whatever is loaded; missing sets are reported, not guessed):
  ICD-10-CM (validity, billability, Excludes1, manifestation/etiology, sex/age edits)
  CPT / HCPCS / ICD-10-PCS (validity on date of service, HCPCS coverage/termination/processing notes)
  MUE (unit limits per code per day), NCCI PTP (code pairs), HCPCS modifiers, fee schedule, coverage crosswalk
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.knowledge.codes import normalize_code, parse_code_refs, ref_matches, system_for_code
from app.knowledge.edits import EditsSnapshot, get_edits
from app.knowledge.repository import KBSnapshot, get_snapshot
from app.models import KnowledgeBaseVersion

DENY, REVIEW, INFO = "deny", "review", "info"

# CPT modifiers (the numeric ones are CPT/AMA; HCPCS Level II modifiers come from the HCPCS file)
CPT_MODIFIERS = {
    "22",
    "23",
    "24",
    "25",
    "26",
    "27",
    "32",
    "33",
    "47",
    "50",
    "51",
    "52",
    "53",
    "54",
    "55",
    "56",
    "57",
    "58",
    "59",
    "62",
    "63",
    "66",
    "73",
    "74",
    "76",
    "77",
    "78",
    "79",
    "80",
    "81",
    "82",
    "90",
    "91",
    "92",
    "93",
    "95",
    "96",
    "97",
    "99",
}
# Modifiers that can bypass an NCCI PTP edit with modifier indicator 1 (NCCI Policy Manual, ch. I)
NCCI_BYPASS = {
    "59",
    "XE",
    "XP",
    "XS",
    "XU",
    "25",
    "58",
    "78",
    "79",
    "24",
    "57",
    "27",
    "91",
    "E1",
    "E2",
    "E3",
    "E4",
    "FA",
    "F1",
    "F2",
    "F3",
    "F4",
    "F5",
    "F6",
    "F7",
    "F8",
    "F9",
    "TA",
    "T1",
    "T2",
    "T3",
    "T4",
    "T5",
    "T6",
    "T7",
    "T8",
    "T9",
    "LT",
    "RT",
    "LC",
    "LD",
    "LM",
    "RC",
    "RI",
}
_FEMALE_ONLY = [
    ("O00", "O9A"),
    ("Z33", "Z36"),
    ("Z3A", "Z3A"),
    ("N70", "N98"),
    ("C51", "C58"),
    ("D25", "D28"),
]
_MALE_ONLY = [("N40", "N53"), ("C60", "C63"), ("D29", "D29")]
_NEWBORN_ONLY = [("P00", "P96"), ("Z38", "Z38")]
_MANIFESTATION = re.compile(r"in (other )?diseases classified elsewhere", re.IGNORECASE)
_EM_OFFICE = re.compile(r"^992(0[2-5]|1[1-5])$")
_EM_ANY = re.compile(r"^99(2\d\d|3\d\d|4[0-9]\d)$")
_PREVENTIVE = re.compile(r"^99(38[1-7]|39[1-7])$")


@dataclass
class ClaimLine:
    code: str
    units: float = 1
    modifiers: list[str] = field(default_factory=list)
    dx_pointers: list[int] = field(default_factory=list)  # 1-based into Claim.diagnoses; empty = all
    system: str | None = None
    charge: float | None = None
    description: str | None = None


@dataclass
class Claim:
    diagnoses: list[str]
    lines: list[ClaimLine]
    date_of_service: date = field(default_factory=date.today)
    claim_type: str = "professional"  # professional (CMS-1500/837P) | institutional (UB-04/837I)
    encounter_type: str = "outpatient"  # outpatient | inpatient
    patient_sex: str | None = None
    patient_age: int | None = None
    payer: str = "medicare"

    @classmethod
    def from_dict(cls, d: dict) -> Claim:
        lines = []
        for ln in d.get("lines", []):
            mods = ln.get("modifiers") or []
            if isinstance(mods, str):
                mods = [m for m in re.split(r"[,\s]+", mods) if m]
            ptr = ln.get("dx_pointers") or []
            if isinstance(ptr, str):
                ptr = [int(x) for x in re.findall(r"\d+", ptr)]
            lines.append(
                ClaimLine(
                    code=str(ln["code"]).strip().upper(),
                    units=float(ln.get("units", 1) or 1),
                    modifiers=[m.strip().upper() for m in mods],
                    dx_pointers=[int(x) for x in ptr],
                    system=ln.get("system"),
                    charge=ln.get("charge"),
                )
            )
        dos = d.get("date_of_service")
        return cls(
            diagnoses=[str(x).strip() for x in d.get("diagnoses", []) if str(x).strip()],
            lines=lines,
            date_of_service=date.fromisoformat(dos)
            if isinstance(dos, str) and dos
            else (dos or date.today()),
            claim_type=d.get("claim_type", "professional"),
            encounter_type=d.get("encounter_type", "outpatient"),
            patient_sex=d.get("patient_sex"),
            patient_age=d.get("patient_age"),
            payer=d.get("payer", "medicare"),
        )


@dataclass
class ScrubIssue:
    rule: str
    severity: str
    message: str
    fix: str = ""
    line: int | None = None  # 1-based service line; None = claim/diagnosis level
    code: str | None = None
    carc: str | None = None  # typical Claim Adjustment Reason Code on the remittance
    rarc: str | None = None
    source: str = ""


@dataclass
class ScrubResult:
    status: str  # clean | review | likely_denial
    risk_score: int  # 0-100 estimated probability the claim is denied/adjusted as billed
    issues: list[ScrubIssue]
    dollars_at_risk: float | None
    references: dict[str, str]
    not_checked: list[str]
    line_systems: list[str]

    def as_dict(self) -> dict:
        d = asdict(self)
        d["summary"] = self.summary()
        return d

    def summary(self) -> str:
        n = {s: sum(1 for i in self.issues if i.severity == s) for s in (DENY, REVIEW, INFO)}
        money = f", ${self.dollars_at_risk:,.2f} at risk" if self.dollars_at_risk else ""
        return f"{self.status.upper()} (risk {self.risk_score}%): {n[DENY]} denial risks, {n[REVIEW]} to review, {n[INFO]} notes{money}"


def _in(code: str, ranges: list[tuple[str, str]]) -> bool:
    cat = code.replace(".", "")[:3]
    return any(a <= cat <= b for a, b in ranges)


def _covers(pattern: str, dx: str) -> bool:
    """Coverage crosswalk entry: exact 'E11.9', prefix 'E11.-'/'E11*'/'E11', or range 'E08-E13'."""
    p = pattern.upper().replace(".", "")
    c = dx.upper().replace(".", "")
    if "-" in p.strip("-"):
        lo, hi = p.split("-", 1)
        return lo <= c[: len(lo)] <= hi or lo <= c <= hi + "Z"
    p = p.rstrip("-*")
    return c.startswith(p)


class ClaimScrubber:
    def __init__(self, db: Session):
        self.db = db
        self.snaps: dict[str, KBSnapshot | None] = {
            s: get_snapshot(db, s) for s in ("ICD-10-CM", "CPT", "HCPCS", "ICD-10-PCS")
        }
        self.edits: EditsSnapshot = get_edits(db)
        self.windows = {
            v.code_system: (v.version, v.effective_from, v.effective_to)
            for v in db.query(KnowledgeBaseVersion).filter(KnowledgeBaseVersion.active.is_(True))
        }

    # ------------------------------------------------------------------ entry point
    def scrub(self, claim: Claim) -> ScrubResult:
        issues: list[ScrubIssue] = []
        not_checked: list[str] = []
        dos = claim.date_of_service
        dx = [normalize_code(d) for d in claim.diagnoses]
        systems = []
        for ln in claim.lines:
            ln.system = ln.system or system_for_code(ln.code) or "CPT"
            systems.append(ln.system)

        used = {"ICD-10-CM"} | set(systems)
        self._freshness(dos, used, claim, issues)
        self._diagnoses(claim, dx, issues)
        for i, ln in enumerate(claim.lines, start=1):
            self._line(claim, i, ln, dx, issues, not_checked)
        self._pairs(claim, issues, not_checked)
        self._em_rules(claim, issues)
        self._duplicates(claim, issues)

        risk = 1.0
        for iss in issues:
            risk *= 1 - {DENY: 0.85, REVIEW: 0.2, INFO: 0.0}[iss.severity]
        risk_score = round((1 - risk) * 100)
        status = (
            "likely_denial"
            if any(i.severity == DENY for i in issues)
            else "review"
            if any(i.severity == REVIEW for i in issues)
            else "clean"
        )
        money = self._dollars(claim, issues)
        refs = {k: f"{v[0]} ({v[1]} to {v[2]})" if v[1] else v[0] for k, v in self.windows.items()}
        return ScrubResult(status, risk_score, issues, money, refs, sorted(set(not_checked)), systems)

    # ------------------------------------------------------------------ reference freshness
    def _freshness(self, dos: date, used: set[str], claim: Claim, issues: list[ScrubIssue]) -> None:
        sets = set(used)
        sets |= {s for s in self.edits.sets if s.startswith("MUE") or s in ("NCCI-PTP",)}
        for s in sorted(sets):
            w = self.windows.get(s)
            if not w or not w[1]:
                continue
            _v, start, end = w
            if (end and dos > end) or (start and dos < start):
                issues.append(
                    ScrubIssue(
                        "reference_not_effective",
                        REVIEW,
                        f"{s} {w[0]} covers {start} to {end}, but the date of service is {dos}",
                        f"Load the {s} release effective for {dos} before billing",
                        source=f"{s} {w[0]}",
                    )
                )

    # ------------------------------------------------------------------ diagnoses
    def _diagnoses(self, claim: Claim, dx: list[str], issues: list[ScrubIssue]) -> None:
        snap = self.snaps["ICD-10-CM"]
        src = f"ICD-10-CM {snap.version}" if snap else "ICD-10-CM"
        if not dx:
            issues.append(
                ScrubIssue(
                    "dx_missing",
                    DENY,
                    "Claim has no diagnosis codes",
                    "Add the ICD-10-CM code(s) supporting each service",
                    carc="16",
                    rarc="M76",
                )
            )
            return
        if snap is None:
            return
        seen = set()
        for pos, code in enumerate(dx, start=1):
            info = snap.get(code)
            label = f"Dx {pos} {code}"
            if code in seen:
                issues.append(
                    ScrubIssue(
                        "dx_duplicate",
                        INFO,
                        f"{label} is listed more than once",
                        "Remove the duplicate",
                        code=code,
                        source=src,
                    )
                )
            seen.add(code)
            if info is None:
                issues.append(
                    ScrubIssue(
                        "dx_invalid",
                        DENY,
                        f"{label} does not exist in {src}",
                        "Correct the code (check for typos or a deleted/new code)",
                        code=code,
                        carc="146",
                        source=src,
                    )
                )
                continue
            if not info.billable:
                kids = [k.code for k in snap.billable_descendants(code, limit=5)]
                reason = (
                    "is not a valid code in the CMS code file"
                    if info.attributes.get("not_in_cms_code_file")
                    else "is a category header, not a billable code"
                )
                issues.append(
                    ScrubIssue(
                        "dx_not_billable",
                        DENY,
                        f"{label} {reason}",
                        ("Code to the highest specificity: " + ", ".join(kids))
                        if kids
                        else "Select a complete, valid code",
                        code=code,
                        carc="146",
                        source=src + " (Guideline I.B.2)",
                    )
                )
            sex = (claim.patient_sex or "").upper()[:1]
            if sex == "M" and _in(code, _FEMALE_ONLY):
                issues.append(
                    ScrubIssue(
                        "dx_sex_edit",
                        DENY,
                        f"{label} is female-specific; patient is male",
                        "Verify patient sex or the diagnosis",
                        code=code,
                        carc="7",
                        source="CMS Medicare Code Editor (sex edit)",
                    )
                )
            if sex == "F" and _in(code, _MALE_ONLY):
                issues.append(
                    ScrubIssue(
                        "dx_sex_edit",
                        DENY,
                        f"{label} is male-specific; patient is female",
                        "Verify patient sex or the diagnosis",
                        code=code,
                        carc="7",
                        source="CMS Medicare Code Editor (sex edit)",
                    )
                )
            age = claim.patient_age
            if age is not None:
                if code.startswith("O") and not 9 <= age <= 64:
                    issues.append(
                        ScrubIssue(
                            "dx_age_edit",
                            DENY,
                            f"{label} is a maternity code; patient age {age} is outside 9-64",
                            "Verify age or diagnosis",
                            code=code,
                            carc="6",
                            source="CMS Medicare Code Editor (age edit)",
                        )
                    )
                if _in(code, _NEWBORN_ONLY) and age > 0:
                    issues.append(
                        ScrubIssue(
                            "dx_age_edit",
                            REVIEW,
                            f"{label} is a perinatal/newborn code; patient age {age}",
                            "Perinatal codes may be used beyond the newborn period only if the condition originated in the perinatal period (Guideline I.C.16.a.4)",
                            code=code,
                            carc="6",
                            source="CMS Medicare Code Editor (age edit)",
                        )
                    )
        first = dx[0]
        finfo = snap.get(first)
        if first[:1] in ("V", "W", "X", "Y"):
            issues.append(
                ScrubIssue(
                    "dx_first_external_cause",
                    DENY,
                    f"External cause code {first} cannot be the first-listed diagnosis",
                    "List the injury/condition first; external cause codes are secondary",
                    code=first,
                    carc="16",
                    rarc="MA63",
                    source="ICD-10-CM Guideline I.C.20",
                )
            )
        if finfo and _MANIFESTATION.search(finfo.description):
            issues.append(
                ScrubIssue(
                    "dx_first_manifestation",
                    DENY,
                    f"Manifestation code {first} cannot be first-listed",
                    "Sequence the underlying etiology first ('code first' note)",
                    code=first,
                    carc="16",
                    rarc="MA63",
                    source="ICD-10-CM Guideline I.A.13",
                )
            )
        valid = [c for c in dx if snap.get(c)]
        reported: set[frozenset] = set()
        for c in valid:
            for owner, note in snap.inherited(c, "excludes1"):
                refs = parse_code_refs(note)
                clash = [o for o in valid if o != c and any(ref_matches(o, r) for r in refs)]
                for o in clash:
                    if frozenset((c, o)) in reported:
                        continue
                    reported.add(frozenset((c, o)))
                    issues.append(
                        ScrubIssue(
                            "dx_excludes1",
                            REVIEW,
                            f"{c} and {o} are mutually exclusive (Excludes1 at {owner}: {note})",
                            "Report only the code that matches the documentation (unless the Excludes1 exception for unrelated conditions applies)",
                            code=c,
                            carc="16",
                            source=f"{src} tabular Excludes1",
                        )
                    )
        by_cat: dict[str, list[str]] = {}
        for c in valid:
            by_cat.setdefault(c[:3], []).append(c)
        for cat, codes in by_cat.items():
            if len(codes) > 1:
                unspec = [c for c in codes if "unspecified" in (snap.get(c).description.lower())]
                if unspec and len(unspec) < len(codes):
                    issues.append(
                        ScrubIssue(
                            "dx_unspecified_with_specific",
                            REVIEW,
                            f"{', '.join(unspec)} (unspecified) reported with a more specific code in {cat}",
                            "Drop the unspecified code",
                            code=unspec[0],
                            source=src,
                        )
                    )

    # ------------------------------------------------------------------ service lines
    def _line(
        self,
        claim: Claim,
        i: int,
        ln: ClaimLine,
        dx: list[str],
        issues: list[ScrubIssue],
        not_checked: list[str],
    ) -> None:
        sys_ = ln.system or "CPT"
        code = normalize_code(ln.code, sys_)
        snap = self.snaps.get(sys_)
        label = f"Line {i} {code}"
        if ln.units <= 0:
            issues.append(
                ScrubIssue(
                    "units_invalid",
                    DENY,
                    f"{label} has {ln.units:g} units",
                    "Units must be at least 1",
                    line=i,
                    code=code,
                    carc="16",
                )
            )
        if sys_ == "ICD-10-PCS" and (
            claim.claim_type == "professional" or claim.encounter_type != "inpatient"
        ):
            issues.append(
                ScrubIssue(
                    "pcs_on_outpatient",
                    DENY,
                    f"{label} is an ICD-10-PCS code; PCS is reported only on inpatient facility claims",
                    "Use CPT/HCPCS for professional and outpatient services",
                    line=i,
                    code=code,
                    carc="16",
                    source="HIPAA code set rules",
                )
            )
        if (
            sys_ in ("CPT", "HCPCS")
            and claim.claim_type == "institutional"
            and claim.encounter_type == "inpatient"
        ):
            issues.append(
                ScrubIssue(
                    "hcpcs_on_inpatient",
                    REVIEW,
                    f"{label}: inpatient facility claims report procedures with ICD-10-PCS",
                    "Report the procedure with ICD-10-PCS on the inpatient claim",
                    line=i,
                    code=code,
                    source="HIPAA code set rules",
                )
            )

        if snap is None:
            not_checked.append(
                f"{sys_} code validity (no {sys_} reference loaded"
                + (" - AMA license required" if sys_ == "CPT" else "")
                + ")"
            )
        else:
            info = snap.get(code)
            src = f"{sys_} {snap.version}"
            if info is None:
                sev = DENY if sys_ != "CPT" or len(snap.codes) > 5000 else REVIEW
                msg = f"{label} not found in {src}" + (
                    "" if sev == DENY else " (your CPT extract has only a subset of codes)"
                )
                issues.append(
                    ScrubIssue(
                        "code_invalid",
                        sev,
                        msg,
                        "Verify the code",
                        line=i,
                        code=code,
                        carc="181" if sev == DENY else None,
                        source=src,
                    )
                )
            else:
                ln.description = info.description
                a = info.attributes or {}
                term = a.get("terminated")
                added = a.get("added")
                if term and date.fromisoformat(term) < claim.date_of_service:
                    issues.append(
                        ScrubIssue(
                            "code_terminated",
                            DENY,
                            f"{label} was terminated on {term}",
                            "Use the replacement code in effect on the date of service",
                            line=i,
                            code=code,
                            carc="181",
                            source=src,
                        )
                    )
                elif not info.billable:
                    issues.append(
                        ScrubIssue(
                            "code_not_billable",
                            DENY,
                            f"{label} is not reportable in {src}",
                            "Use a valid code",
                            line=i,
                            code=code,
                            carc="181",
                            source=src,
                        )
                    )
                if added and date.fromisoformat(added) > claim.date_of_service:
                    issues.append(
                        ScrubIssue(
                            "code_not_yet_effective",
                            DENY,
                            f"{label} is effective from {added}",
                            "Use the code valid on the date of service",
                            line=i,
                            code=code,
                            carc="181",
                            source=src,
                        )
                    )
                cov = a.get("coverage_code")
                if sys_ == "HCPCS" and cov and claim.payer.lower() == "medicare":
                    if cov in ("M", "S"):
                        issues.append(
                            ScrubIssue(
                                "hcpcs_noncovered",
                                DENY,
                                f"{label}: {a.get('coverage_description')} (coverage code {cov})",
                                "Obtain an ABN (modifier GA) before service, or bill the patient/secondary payer",
                                line=i,
                                code=code,
                                carc="96",
                                source=src + " coverage code",
                            )
                        )
                    elif cov == "I":
                        issues.append(
                            ScrubIssue(
                                "hcpcs_not_payable",
                                DENY,
                                f"{label}: not payable by Medicare (coverage code I) - Medicare uses another code for this service",
                                "Find the Medicare-payable code (see HCPCS cross-reference)",
                                line=i,
                                code=code,
                                carc="96",
                                source=src + " coverage code",
                            )
                        )
                    elif cov == "D":
                        note = self.edits.notes.get(a.get("processing_note") or "")
                        issues.append(
                            ScrubIssue(
                                "hcpcs_special_coverage",
                                INFO,
                                f"{label}: special coverage instructions apply"
                                + (f" - {note}" if note else ""),
                                "Check the NCD/LCD before billing",
                                line=i,
                                code=code,
                                source=src,
                            )
                        )
                pn = a.get("processing_note")
                if pn and pn in self.edits.notes and cov != "D":
                    issues.append(
                        ScrubIssue(
                            "hcpcs_processing_note",
                            INFO,
                            f"{label} processing note {pn}: {self.edits.notes[pn]}",
                            line=i,
                            code=code,
                            source="HCPCS processing notes",
                        )
                    )
                if "unlisted" in info.description.lower():
                    issues.append(
                        ScrubIssue(
                            "unlisted_code",
                            REVIEW,
                            f"{label} is an unlisted code; payers require a description and documentation",
                            "Attach the operative/procedure report",
                            line=i,
                            code=code,
                            carc="16",
                            rarc="M127",
                            source=src,
                        )
                    )

        # modifiers
        known_mods = CPT_MODIFIERS | set(self.edits.modifiers)
        for m in ln.modifiers:
            if self.edits.modifiers and m not in known_mods:
                issues.append(
                    ScrubIssue(
                        "modifier_invalid",
                        DENY,
                        f"{label}: modifier {m} is not a valid CPT/HCPCS modifier",
                        "Correct the modifier",
                        line=i,
                        code=code,
                        carc="4",
                        source="HCPCS modifiers + CPT modifier list",
                    )
                )
        if "LT" in ln.modifiers and "RT" in ln.modifiers:
            issues.append(
                ScrubIssue(
                    "modifier_conflict",
                    DENY,
                    f"{label}: LT and RT on the same line",
                    "Use modifier 50 (bilateral) or two lines with LT and RT",
                    line=i,
                    code=code,
                    carc="4",
                )
            )
        if "25" in ln.modifiers and not _EM_ANY.match(code):
            issues.append(
                ScrubIssue(
                    "modifier_25_non_em",
                    DENY,
                    f"{label}: modifier 25 is only valid on E/M services",
                    "Remove modifier 25",
                    line=i,
                    code=code,
                    carc="4",
                    source="CPT modifier 25 definition",
                )
            )

        # MUE
        mue_set = self._mue_set(claim, sys_, code)
        table = self.edits.mue.get(mue_set) if mue_set else None
        if table is None:
            if sys_ in ("CPT", "HCPCS"):
                not_checked.append("MUE unit limits (" + (mue_set or "no MUE set") + " not loaded)")
        elif code in table:
            limit, mai, rationale = table[code]
            total = sum(x.units for x in claim.lines if normalize_code(x.code, x.system or "CPT") == code)
            if ln.units > limit:
                issues.append(
                    ScrubIssue(
                        "mue_exceeded",
                        DENY,
                        f"{label}: {ln.units:g} units exceed the MUE of {limit:g} per day ({rationale or 'CMS'})",
                        f"Bill at most {limit:g} unit(s) on this line; if more were medically necessary, split by anatomic modifiers where allowed and keep documentation for appeal",
                        line=i,
                        code=code,
                        carc="151",
                        source=f"{mue_set} {self.edits.sets[mue_set].version}",
                    )
                )
            elif total > limit and ln is next(
                x for x in claim.lines if normalize_code(x.code, x.system or "CPT") == code
            ):
                issues.append(
                    ScrubIssue(
                        "mue_exceeded_day",
                        REVIEW,
                        f"{code}: {total:g} units across lines exceed the MUE of {limit:g} per date of service",
                        "Date-of-service MUEs add units from all lines; verify medical necessity and modifiers",
                        line=i,
                        code=code,
                        carc="151",
                        source=f"{mue_set} {self.edits.sets[mue_set].version}",
                    )
                )

        # diagnosis pointers & medical necessity
        ptrs = ln.dx_pointers or list(range(1, len(dx) + 1))
        bad = [p for p in ptrs if p < 1 or p > len(dx)]
        if bad:
            issues.append(
                ScrubIssue(
                    "dx_pointer_invalid",
                    DENY,
                    f"{label} points to diagnosis {bad} but the claim has {len(dx)}",
                    "Fix the diagnosis pointer(s)",
                    line=i,
                    code=code,
                    carc="16",
                    rarc="M76",
                )
            )
        pointed = [dx[p - 1] for p in ptrs if 1 <= p <= len(dx)]
        if self.edits.coverage:
            rules = self.edits.coverage.get(code)
            if rules and pointed and not any(_covers(r, d) for r in rules for d in pointed):
                issues.append(
                    ScrubIssue(
                        "medical_necessity",
                        DENY,
                        f"{label}: none of the linked diagnoses ({', '.join(pointed)}) are on the covered list for this service",
                        "Link the diagnosis that supports medical necessity, or obtain an ABN",
                        line=i,
                        code=code,
                        carc="50",
                        source="Coverage crosswalk (LCD/NCD)",
                    )
                )
        elif sys_ in ("CPT", "HCPCS"):
            not_checked.append("medical necessity (no LCD/NCD coverage crosswalk loaded)")

    def _mue_set(self, claim: Claim, system: str, code: str) -> str | None:
        if system not in ("CPT", "HCPCS"):
            return None
        if system == "HCPCS" and code[:1] in ("E", "K", "L", "A") and "MUE-DME" in self.edits.mue:
            return "MUE-DME"
        if claim.claim_type == "institutional":
            return "MUE-OPH" if "MUE-OPH" in self.edits.mue else None
        return "MUE-PRAC"

    # ------------------------------------------------------------------ NCCI procedure-to-procedure
    def _pairs(self, claim: Claim, issues: list[ScrubIssue], not_checked: list[str]) -> None:
        codes = [
            (i, normalize_code(x.code, x.system or "CPT"), x) for i, x in enumerate(claim.lines, start=1)
        ]
        if not self.edits.ptp:
            if sum(1 for _i, _c, x in codes if (x.system or "CPT") in ("CPT", "HCPCS")) > 1:
                not_checked.append("NCCI procedure-to-procedure bundling (NCCI PTP file not loaded)")
            return
        dos = claim.date_of_service
        for i, c1, _l1 in codes:
            for j, c2, l2 in codes:
                if i == j:
                    continue
                edit = self.edits.ptp.get((c1, c2))
                if not edit:
                    continue
                ind, start, end, rationale = edit
                if (start and dos < start) or (end and dos > end) or ind == "9":
                    continue
                src = f"NCCI PTP {self.edits.sets['NCCI-PTP'].version}"
                if ind == "0":
                    issues.append(
                        ScrubIssue(
                            "ncci_ptp",
                            DENY,
                            f"Line {j} {c2} is bundled into line {i} {c1} (NCCI, modifier not allowed: {rationale or 'column 1/column 2 edit'})",
                            f"Remove {c2}; it is not separately payable with {c1}",
                            line=j,
                            code=c2,
                            carc="236",
                            source=src,
                        )
                    )
                elif not set(l2.modifiers) & NCCI_BYPASS:
                    issues.append(
                        ScrubIssue(
                            "ncci_ptp",
                            DENY,
                            f"Line {j} {c2} is bundled into line {i} {c1} unless a distinct service is documented ({rationale or 'column 1/column 2 edit'})",
                            f"If separately identifiable (different site/session/encounter), append 59 or XE/XS/XP/XU to {c2}; otherwise remove it",
                            line=j,
                            code=c2,
                            carc="236",
                            source=src,
                        )
                    )
                else:
                    issues.append(
                        ScrubIssue(
                            "ncci_bypass_modifier",
                            INFO,
                            f"Line {j} {c2} bypasses the NCCI edit with {c1} via modifier(s) {', '.join(sorted(set(l2.modifiers) & NCCI_BYPASS))}",
                            "Audit risk: documentation must support a distinct procedural service",
                            line=j,
                            code=c2,
                            source=src,
                        )
                    )

    # ------------------------------------------------------------------ E/M policy rules (work without the PTP file)
    def _em_rules(self, claim: Claim, issues: list[ScrubIssue]) -> None:
        lines = [
            (i, normalize_code(x.code, x.system or "CPT"), x) for i, x in enumerate(claim.lines, start=1)
        ]
        ems = [(i, c, x) for i, c, x in lines if _EM_ANY.match(c)]
        procs = [
            (i, c, x)
            for i, c, x in lines
            if (x.system or "CPT") == "CPT" and c.isdigit() and "10004" <= c <= "69990"
        ]
        if procs and not self.edits.ptp:
            for i, c, x in ems:
                if _PREVENTIVE.match(c):
                    continue
                if "25" not in x.modifiers and "57" not in x.modifiers:
                    issues.append(
                        ScrubIssue(
                            "em_with_procedure_no_25",
                            DENY,
                            f"Line {i} {c} (E/M) is billed with procedure {', '.join(p for _j, p, _x in procs)} without modifier 25",
                            "Append 25 only if a significant, separately identifiable E/M is documented (57 if decision for major surgery); otherwise remove the E/M",
                            line=i,
                            code=c,
                            carc="97",
                            source="NCCI Policy Manual ch. I / CPT modifier 25",
                        )
                    )
        prev = [(i, c) for i, c, _x in ems if _PREVENTIVE.match(c)]
        if prev:
            for i, c, x in ems:
                if _EM_OFFICE.match(c) and "25" not in x.modifiers:
                    issues.append(
                        ScrubIssue(
                            "preventive_plus_problem_em",
                            DENY,
                            f"Line {i} {c} billed with preventive visit {prev[0][1]} without modifier 25",
                            "Append 25 to the problem-oriented E/M if separately documented",
                            line=i,
                            code=c,
                            carc="97",
                            source="CPT preventive medicine guidelines",
                        )
                    )
        # vaccine/toxoid products (90476-90759) are billed together with an administration code
        products = [(i, c) for i, c, x in lines if c.isdigit() and "90476" <= c <= "90759"]
        admins = [
            c
            for _i, c, _x in lines
            if c.isdigit() and ("90460" <= c <= "90474" or c in ("G0008", "G0009", "G0010"))
        ]
        if products and not admins:
            issues.append(
                ScrubIssue(
                    "vaccine_without_admin",
                    REVIEW,
                    f"Vaccine product {', '.join(c for _i, c in products)} billed without an immunization administration code",
                    "Add 90471 (or 90460 with counseling for patients under 19; G0008/G0009/G0010 for Medicare flu/pneumo/hep B) - otherwise the administration is not paid",
                    line=products[0][0],
                    code=products[0][1],
                    source="CPT immunization administration guidelines",
                )
            )
        if admins and not products:
            issues.append(
                ScrubIssue(
                    "admin_without_vaccine",
                    REVIEW,
                    "Immunization administration billed without a vaccine product code",
                    "Add the vaccine product code (or confirm it was supplied free, e.g. VFC)",
                    source="CPT immunization administration guidelines",
                )
            )
        if len([1 for _i, c, _x in ems if _EM_OFFICE.match(c)]) > 1:
            issues.append(
                ScrubIssue(
                    "multiple_office_em",
                    REVIEW,
                    "More than one office E/M on the same date of service",
                    "Combine into one E/M level unless different specialties/providers",
                    carc="18",
                )
            )

    def _duplicates(self, claim: Claim, issues: list[ScrubIssue]) -> None:
        seen: dict[tuple, int] = {}
        for i, x in enumerate(claim.lines, start=1):
            key = (normalize_code(x.code, x.system or "CPT"), tuple(sorted(x.modifiers)))
            if key in seen:
                issues.append(
                    ScrubIssue(
                        "duplicate_line",
                        REVIEW,
                        f"Line {i} duplicates line {seen[key]} ({key[0]} {' '.join(key[1])})",
                        "Combine units on one line, or add 76/77/91/59 if it was truly repeated",
                        line=i,
                        code=key[0],
                        carc="18",
                    )
                )
            else:
                seen[key] = i

    def _dollars(self, claim: Claim, issues: list[ScrubIssue]) -> float | None:
        denied = {i.line for i in issues if i.severity == DENY and i.line}
        claim_level = any(i.severity == DENY and i.line is None for i in issues)
        total, known = 0.0, False
        for n, x in enumerate(claim.lines, start=1):
            if claim_level or n in denied:
                amt = (
                    x.charge
                    if x.charge is not None
                    else self.edits.fee.get(normalize_code(x.code, x.system or "CPT"))
                )
                if amt is not None:
                    total += float(amt) * (x.units if x.charge is None else 1)
                    known = True
        return round(total, 2) if known else None


_PER_UNIT = re.compile(r"(?:,|\bper)\s*(\d+(?:\.\d+)?)\s*(mg|mcg|units?|ml|g|gm)\b", re.I)
_DOSE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|mcg|units?|ml|g|gm)\b", re.I)


def infer_units(description: str, evidence: str) -> float:
    """HCPCS drug codes are billed per dosage unit ('Injection, dexamethasone sodium phosphate, 1 mg'):
    8 mg documented -> 8 units. Returns 1 when the dose cannot be read."""
    import math

    per = _PER_UNIT.search(description or "")
    if not per:
        return 1
    unit = per.group(2).lower().rstrip("s")
    for m in _DOSE.finditer(evidence or ""):
        if m.group(2).lower().rstrip("s") == unit:
            return float(max(1, math.ceil(float(m.group(1)) / float(per.group(1)))))
    return 1


def claim_from_suggestions(
    suggestions: list[dict],
    *,
    date_of_service: date | None = None,
    encounter_type: str = "outpatient",
    patient_sex: str | None = None,
    patient_age: int | None = None,
) -> Claim:
    """Draft claim from a coded document: diagnoses in sequence order, procedures as lines."""
    dx = [s["code"] for s in suggestions if s.get("code_system", "ICD-10-CM") == "ICD-10-CM"]
    lines = [
        ClaimLine(
            code=s["code"],
            system=s["code_system"],
            units=float(
                s.get("units") or infer_units(s.get("description", ""), " ".join(s.get("evidence") or []))
            ),
            description=s.get("description"),
        )
        for s in suggestions
        if s.get("code_system") in ("CPT", "HCPCS", "ICD-10-PCS")
    ]
    return Claim(
        diagnoses=dx,
        lines=lines,
        date_of_service=date_of_service or date.today(),
        claim_type="institutional" if encounter_type == "inpatient" else "professional",
        encounter_type=encounter_type,
        patient_sex=patient_sex,
        patient_age=patient_age,
    )
