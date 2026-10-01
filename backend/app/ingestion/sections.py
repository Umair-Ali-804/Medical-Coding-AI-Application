"""Clinical note section detection.

Recognizes common headers (SOAP, H&P, discharge summary, op note) and maps
them to canonical names. Section context matters for coding: diagnoses in
"Assessment/Plan" or "Discharge Diagnoses" carry more weight than those in
"Family History" or "Review of Systems".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# canonical name -> header variants (lowercase, without trailing colon)
SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "chief_complaint": (
        "chief complaint",
        "cc",
        "reason for visit",
        "reason for consultation",
        "presenting complaint",
    ),
    "hpi": (
        "history of present illness",
        "hpi",
        "history of the present illness",
        "present illness",
        "subjective",
        "interval history",
    ),
    "past_medical_history": (
        "past medical history",
        "pmh",
        "medical history",
        "past history",
        "history",
        "problem list",
        "active problems",
        "past medical/surgical history",
    ),
    "past_surgical_history": ("past surgical history", "psh", "surgical history"),
    "medications": (
        "medications",
        "current medications",
        "home medications",
        "meds",
        "medication list",
        "outpatient medications",
        "discharge medications",
    ),
    "allergies": ("allergies", "drug allergies", "nkda"),
    "family_history": ("family history", "fh", "fhx"),
    "social_history": ("social history", "sh", "social hx"),
    "review_of_systems": ("review of systems", "ros"),
    "vitals": ("vital signs", "vitals"),
    "physical_exam": ("physical exam", "physical examination", "exam", "objective", "examination"),
    "labs": (
        "labs",
        "laboratory",
        "laboratory data",
        "lab results",
        "results",
        "diagnostic data",
        "studies",
        "imaging",
        "radiology",
    ),
    "assessment": (
        "assessment",
        "impression",
        "diagnosis",
        "diagnoses",
        "clinical impression",
        "assessment and plan",
        "assessment/plan",
        "a/p",
        "a&p",
        "final diagnosis",
        "final diagnoses",
    ),
    "plan": ("plan", "recommendations", "disposition", "follow up", "follow-up"),
    "discharge_diagnoses": (
        "discharge diagnosis",
        "discharge diagnoses",
        "principal diagnosis",
        "secondary diagnoses",
    ),
    # working diagnosis at admission; the discharge diagnoses are authoritative for inpatient coding
    "admission_diagnosis": (
        "admitting diagnosis",
        "admission diagnosis",
        "admitting diagnoses",
        "admission diagnoses",
    ),
    "hospital_course": ("hospital course", "brief hospital course", "course"),
    "procedures": (
        "procedure",
        "procedures",
        "procedure performed",
        "procedures performed",
        "operation",
        "operative procedure",
        "operations",
    ),
    "preoperative_diagnosis": ("preoperative diagnosis", "pre-operative diagnosis", "preop diagnosis"),
    "postoperative_diagnosis": ("postoperative diagnosis", "post-operative diagnosis", "postop diagnosis"),
    "findings": ("findings", "operative findings"),
}

# Sections whose statements are most authoritative for diagnosis coding.
DIAGNOSTIC_SECTIONS = {
    "assessment",
    "discharge_diagnoses",
    "postoperative_diagnosis",
    "chief_complaint",
    "hpi",
    "plan",
}
# Sections whose findings should generally NOT be coded as current patient conditions.
NON_CODABLE_SECTIONS = {"family_history", "allergies", "review_of_systems"}

_ALIAS_TO_CANON = {alias: canon for canon, aliases in SECTION_ALIASES.items() for alias in aliases}
_ALIASES_SORTED = sorted(_ALIAS_TO_CANON, key=len, reverse=True)
_HEADER_RE = re.compile(
    r"^[ \t]*(?P<header>(" + "|".join(re.escape(a) for a in _ALIASES_SORTED) + r"))[ \t]*(:|-|\n|$)",
    re.IGNORECASE | re.MULTILINE,
)
# Generic ALL-CAPS header ("HOSPITAL COURSE:")
_CAPS_HEADER_RE = re.compile(r"^[ \t]*(?P<header>[A-Z][A-Z/&-]{2,}(?: [A-Z/&-]+){1,5}):", re.MULTILINE)


@dataclass
class Section:
    name: str
    header: str | None
    start: int  # start of section body (after header)
    end: int
    order: int


def detect_sections(text: str) -> list[Section]:
    hits: list[tuple[int, int, str, str]] = []  # (header_start, body_start, canon, header)
    for m in _HEADER_RE.finditer(text):
        header = m.group("header")
        canon = _ALIAS_TO_CANON[header.lower()]
        body_start = m.end()
        hits.append((m.start("header"), body_start, canon, header))
    known = {h[0] for h in hits}
    for m in _CAPS_HEADER_RE.finditer(text):
        if m.start("header") in known:
            continue
        header = m.group("header").strip()
        canon = _ALIAS_TO_CANON.get(header.lower(), re.sub(r"[^a-z]+", "_", header.lower()).strip("_"))
        hits.append((m.start("header"), m.end(), canon, header))
    hits.sort()

    sections: list[Section] = []
    if not hits or hits[0][0] > 0:
        first_end = hits[0][0] if hits else len(text)
        if text[:first_end].strip():
            sections.append(Section("preamble", None, 0, first_end, 0))
    for i, (_hstart, body_start, canon, header) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        sections.append(Section(canon, header, body_start, end, len(sections)))
    return sections


def section_at(sections: list[Section], offset: int) -> str | None:
    for s in sections:
        if s.start <= offset < s.end:
            return s.name
    # Offsets inside a header line belong to the section that header opens
    for s in sections:
        if offset < s.start:
            return s.name
    return sections[-1].name if sections else None
