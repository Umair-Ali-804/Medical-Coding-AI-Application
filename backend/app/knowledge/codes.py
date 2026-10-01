"""Code formatting helpers shared by the KB, validation and evaluation."""

from __future__ import annotations

import re

_ICD_RE = re.compile(r"^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$")
_CPT_RE = re.compile(r"^[0-9]{4}[0-9FTU]$")
_HCPCS_RE = re.compile(r"^[A-V][0-9]{4}$")
# ICD-10-PCS: 7 characters, digits and letters except I and O
_PCS_RE = re.compile(r"^[0-9A-HJ-NP-Z]{7}$")

DIAGNOSIS_SYSTEMS = ("ICD-10-CM",)
PROCEDURE_SYSTEMS = ("CPT", "HCPCS", "ICD-10-PCS")
ALL_SYSTEMS = DIAGNOSIS_SYSTEMS + PROCEDURE_SYSTEMS


def system_for_code(code: str) -> str | None:
    """Best-effort code-system detection for claim lines entered without a system."""
    c = re.sub(r"\s+", "", (code or "").upper())
    if _CPT_RE.match(c):
        return "CPT"
    if _HCPCS_RE.match(c):
        return "HCPCS"
    if _PCS_RE.match(c):
        return "ICD-10-PCS"
    if _ICD_RE.match(normalize_code(c)):
        return "ICD-10-CM"
    return None


def normalize_code(code: str, system: str = "ICD-10-CM") -> str:
    c = re.sub(r"\s+", "", (code or "").upper()).rstrip(".")
    if system == "ICD-10-CM":
        c = c.replace(".", "")
        if len(c) > 3:
            c = c[:3] + "." + c[3:]
    return c


def looks_valid(code: str, system: str) -> bool:
    if system == "ICD-10-CM":
        return bool(_ICD_RE.match(code))
    if system == "CPT":
        return bool(_CPT_RE.match(code))
    if system == "HCPCS":
        return bool(_HCPCS_RE.match(code))
    if system == "ICD-10-PCS":
        return bool(_PCS_RE.match(code))
    return False


def category_of(code: str) -> str:
    return code.replace(".", "")[:3]


def code_in_range(code: str, start: str, end: str) -> bool:
    """Category-level range test, e.g. code_in_range('E11.9', 'E08', 'E13')."""
    cat = category_of(code)
    return category_of(start) <= cat <= category_of(end)


# "(E10.-)", "(O24.4-)", "(E11.A)", "(E08-E13)", "(I10-I16)", "(N18.1-N18.6)", "(Z79.4)"
_CODE = r"[A-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{0,4})?"
_REF_RE = re.compile(rf"\b({_CODE})(?:\s*-\s*({_CODE})|(-))?")


def parse_code_refs(note: str) -> list[tuple[str, str | None, bool]]:
    """Extract code references from an instructional note.

    Returns (start, end, is_prefix): end is set for ranges ("E08-E13", "N18.1-N18.6");
    is_prefix is True for "E10.-" / "O24.4-" style references (all subcodes).
    """
    refs: list[tuple[str, str | None, bool]] = []
    for paren in re.findall(r"\(([^)]*)\)", note):
        for m in _REF_RE.finditer(paren):
            start, end, dash = m.group(1).rstrip("."), m.group(2), m.group(3)
            if end:
                refs.append((start, end.rstrip("."), False))
            else:
                refs.append((start, None, bool(dash) or m.group(1).endswith(".") or "." not in start))
    return refs


def ref_matches(code: str, ref: tuple[str, str | None, bool]) -> bool:
    start, end, is_prefix = ref
    if end:
        if "." in start or "." in end:  # subcategory range, e.g. N18.1-N18.6
            c = code.replace(".", "")
            s, e = start.replace(".", ""), end.replace(".", "")
            return s <= c[: len(s)] <= e or s <= c <= e + "Z"
        return code_in_range(code, start, end)
    c = code.replace(".", "")
    s = start.replace(".", "")
    return c.startswith(s) if is_prefix else c == s
