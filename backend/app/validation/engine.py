"""Deterministic validation of AI code proposals. The LLM is never trusted directly.

Severity:
  reject - the code cannot be reported as-is (nonexistent, wrong version, non-billable header,
           unlicensed system, duplicate). Shown to the coder as system-rejected.
  flag   - reportable but needs human judgement (unsupported evidence, negation contradiction,
           Excludes1 conflict, specificity/laterality, demographic edit, manifestation sequencing).
  info   - coding hints (use-additional-code, outside retrieved references).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.extraction.types import Entity
from app.knowledge.codes import PROCEDURE_SYSTEMS, looks_valid, normalize_code, parse_code_refs, ref_matches
from app.knowledge.repository import CodeInfo, KBSnapshot
from app.models.enums import ErrorCategory, ValidationStatus
from app.rag.lexical import tokenize
from app.validation.evidence import EvidenceMatch, match_all

REJECT, FLAG, INFO = "reject", "flag", "info"
EVIDENCE_MIN = 0.85

_UNSPEC_LAT = re.compile(
    r"unspecified (side|eye|ear|knee|hip|shoulder|elbow|wrist|hand|foot|ankle|leg|arm|limb|lower limb|upper limb|"
    r"lung|breast|kidney|ureter|ovary|fallopian tube|thigh|femur|humerus|radius|ulna|tibia|fibula|finger|toe|"
    r"forearm|upper arm|lower leg|site)|unspecified laterality",
    re.IGNORECASE,
)
_FEMALE_ONLY = [
    ("O00", "O9A"),
    ("Z33", "Z36"),
    ("Z3A", "Z3A"),
    ("N70", "N98"),
    ("C51", "C58"),
    ("D25", "D28"),
]
_MALE_ONLY = [("N40", "N53"), ("C60", "C63"), ("D29", "D29")]
_MANIFESTATION = re.compile(r"in (other )?diseases classified elsewhere", re.IGNORECASE)
_DIABETES_CATS = {"E08", "E09", "E10", "E11", "E13"}


@dataclass
class Issue:
    rule: str
    severity: str
    message: str
    category: str | None = None  # ErrorCategory for error analysis
    related_codes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "category": self.category,
            "related_codes": self.related_codes,
        }


@dataclass
class Proposal:
    code: str
    system: str
    evidence: list[str]
    entity_text: str = ""
    rationale: str = ""
    llm_confidence: float | None = None
    from_candidates: bool = True
    llm_description: str = ""
    sequence: int = 0


@dataclass
class ValidatedProposal:
    proposal: Proposal
    code: str
    info: CodeInfo | None
    status: str
    issues: list[Issue]
    evidence: list[EvidenceMatch]
    linked_entity: Entity | None
    entity_similarity: float

    @property
    def best_evidence_score(self) -> float:
        return max((e.score for e in self.evidence), default=0.0)

    def issue_dicts(self) -> list[dict]:
        return [i.as_dict() for i in self.issues]


@dataclass
class ValidationContext:
    text: str
    entities: list[Entity]
    snapshots: dict[str, KBSnapshot | None]
    encounter_type: str = "outpatient"
    patient_sex: str | None = None
    patient_age: int | None = None
    other_versions: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # (system, code)->versions


def _in_ranges(code: str, ranges: list[tuple[str, str]]) -> bool:
    cat = code.replace(".", "")[:3]
    return any(a <= cat <= b for a, b in ranges)


def _similarity(a: str, b: str) -> float:
    ta, tb = set(tokenize(a, expand=True)), set(tokenize(b, expand=True))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta)


class ValidationEngine:
    def validate(self, proposals: list[Proposal], ctx: ValidationContext) -> list[ValidatedProposal]:
        results: list[ValidatedProposal] = []
        seen: set[tuple[str, str]] = set()
        for seq, p in enumerate(proposals, start=1):
            p.sequence = seq
            code = normalize_code(p.code, p.system)
            snap = ctx.snapshots.get(p.system)
            info = snap.get(code) if snap else None
            issues: list[Issue] = []

            self._existence(p, code, info, snap, ctx, issues)
            if (p.system, code) in seen:
                issues.append(
                    Issue(
                        "duplicate",
                        REJECT,
                        f"{code} already proposed for this document",
                        ErrorCategory.EXTRA_CODE,
                    )
                )
            seen.add((p.system, code))

            evidence = match_all(ctx.text, p.evidence)
            self._evidence(evidence, issues)
            linked, sim = self._link_entity(p, evidence, ctx.entities, info)
            self._assertions(code, linked, ctx, issues)
            if info:
                self._laterality(info, linked, evidence, ctx.text, issues)
                if p.system == "ICD-10-CM":
                    self._demographics(code, info, ctx, issues)
                elif p.system == "ICD-10-PCS" and ctx.encounter_type != "inpatient":
                    issues.append(
                        Issue(
                            "pcs_outpatient",
                            FLAG,
                            "ICD-10-PCS is reported on inpatient facility claims only",
                            ErrorCategory.WRONG_CODE,
                        )
                    )
            if not p.from_candidates:
                issues.append(
                    Issue(
                        "outside_retrieval",
                        INFO,
                        "Proposed from model knowledge, not from retrieved references",
                        ErrorCategory.RETRIEVAL_FAILURE,
                    )
                )
            results.append(
                ValidatedProposal(p, code, info, ValidationStatus.PASSED, issues, evidence, linked, sim)
            )

        self._set_rules(results, ctx)
        for r in results:
            sev = {i.severity for i in r.issues}
            r.status = (
                ValidationStatus.REJECTED
                if REJECT in sev
                else ValidationStatus.FLAGGED
                if FLAG in sev
                else ValidationStatus.PASSED
            )
        return results

    # ------------------------------------------------------------------ single-code rules
    def _existence(
        self,
        p: Proposal,
        code: str,
        info: CodeInfo | None,
        snap: KBSnapshot | None,
        ctx: ValidationContext,
        issues: list[Issue],
    ) -> None:
        if snap is None:
            issues.append(
                Issue(
                    "code_system_unavailable",
                    REJECT,
                    f"{p.system} reference data is not loaded/licensed on this platform",
                    ErrorCategory.VALIDATION_FAILURE,
                )
            )
            return
        if not looks_valid(code, p.system):
            issues.append(
                Issue(
                    "invalid_format",
                    REJECT,
                    f"'{p.code}' is not a valid {p.system} code format",
                    ErrorCategory.WRONG_CODE,
                )
            )
            return
        if info is None and p.system == "CPT":
            rng = next(
                (
                    c
                    for c in snap.codes.values()
                    if c.attributes.get("range")
                    and c.attributes["range_start"] <= code <= c.attributes["range_end"]
                ),
                None,
            )
            if rng is not None:
                issues.append(
                    Issue(
                        "cpt_partial_reference",
                        FLAG,
                        f"{code} falls in CPT range {rng.code} ({rng.description}) of your CPT extract; "
                        "verify the exact code against the full CPT code set",
                        ErrorCategory.VALIDATION_FAILURE,
                    )
                )
                return
        if info is None:
            other = ctx.other_versions.get((p.system, code), [])
            msg = (
                f"{code} is not valid in {p.system} {snap.version} (exists in: {', '.join(other)})"
                if other
                else f"{code} does not exist in {p.system} {snap.version}"
            )
            issues.append(Issue("code_exists", REJECT, msg, ErrorCategory.WRONG_CODE))
            return
        if not info.billable:
            kids = snap.billable_descendants(code, limit=6)
            hint = ("; more specific codes: " + ", ".join(k.code for k in kids)) if kids else ""
            issues.append(
                Issue(
                    "billable",
                    REJECT,
                    f"{code} is a non-billable header and needs more characters{hint}",
                    ErrorCategory.WRONG_SPECIFICITY,
                    [k.code for k in kids],
                )
            )

    def _evidence(self, evidence: list[EvidenceMatch], issues: list[Issue]) -> None:
        if not evidence:
            issues.append(
                Issue(
                    "evidence_missing", FLAG, "No supporting evidence quoted", ErrorCategory.REASONING_FAILURE
                )
            )
            return
        best = max(e.score for e in evidence)
        if best < EVIDENCE_MIN:
            issues.append(
                Issue(
                    "evidence_not_found",
                    FLAG,
                    f"Evidence quote not found in the document (best match {best:.0%}); possible hallucination",
                    ErrorCategory.REASONING_FAILURE,
                )
            )

    def _link_entity(
        self, p: Proposal, evidence: list[EvidenceMatch], entities: list[Entity], info: CodeInfo | None
    ) -> tuple[Entity | None, float]:
        cats = (
            ("procedure",)
            if p.system in PROCEDURE_SYSTEMS
            else ("condition", "symptom", "finding", "medication")
        )
        codable = [e for e in entities if e.category in cats]
        best: tuple[float, Entity | None] = (0.0, None)
        for e in codable:
            score = 0.0
            if p.entity_text:
                if p.entity_text.strip().lower() == e.text.lower():
                    score = 1.0
                else:
                    score = max(_similarity(p.entity_text, e.text), _similarity(e.text, p.entity_text)) * 0.9
            for ev in evidence:
                if ev.start is not None and ev.start <= e.start and e.end <= (ev.end or 0):
                    score = max(score, 0.5 + 0.5 * score)
            if score > best[0]:
                best = (score, e)
        linked = best[1] if best[0] >= 0.34 else None
        sim = 0.0
        if info:
            probe = " ".join(
                filter(
                    None, [p.entity_text, linked.text if linked else "", linked.normalized if linked else ""]
                )
            )
            sim = _similarity(probe, info.search_text()) if probe else 0.0
        return linked, round(min(1.0, sim), 3)

    def _assertions(
        self, code: str, linked: Entity | None, ctx: ValidationContext, issues: list[Issue]
    ) -> None:
        if linked is None:
            issues.append(
                Issue(
                    "entity_link",
                    INFO,
                    "No extracted clinical entity matches this code",
                    ErrorCategory.REASONING_FAILURE,
                )
            )
            return
        same = [e for e in ctx.entities if e.concept_key == linked.concept_key] or [linked]
        affirmed = [e for e in same if e.affirmed and e.section not in ("review_of_systems",)]
        if linked.assertion_conflict:
            issues.append(
                Issue(
                    "assertion_conflict",
                    FLAG,
                    "Rule-based NLP and LLM disagree on negation/uncertainty for this finding",
                    ErrorCategory.NEGATION_ERROR,
                )
            )
        if affirmed:
            return
        is_family_code = code[:3] in {"Z80", "Z81", "Z82", "Z83", "Z84"}
        if any(e.negated for e in same):
            issues.append(
                Issue(
                    "negation_contradiction",
                    FLAG,
                    f"Documentation negates '{linked.text}'",
                    ErrorCategory.NEGATION_ERROR,
                )
            )
        elif any(e.family for e in same) and not is_family_code:
            issues.append(
                Issue(
                    "family_history",
                    FLAG,
                    f"'{linked.text}' is documented for a family member, not the patient",
                    ErrorCategory.NEGATION_ERROR,
                )
            )
        elif any(e.uncertain for e in same) and ctx.encounter_type != "inpatient":
            issues.append(
                Issue(
                    "uncertain_diagnosis",
                    FLAG,
                    f"'{linked.text}' is documented as uncertain; outpatient guidelines code symptoms instead",
                    ErrorCategory.NEGATION_ERROR,
                )
            )
        elif any(e.hypothetical for e in same):
            issues.append(
                Issue(
                    "hypothetical",
                    FLAG,
                    f"'{linked.text}' is mentioned hypothetically",
                    ErrorCategory.NEGATION_ERROR,
                )
            )

    def _laterality(
        self,
        info: CodeInfo,
        linked: Entity | None,
        evidence: list[EvidenceMatch],
        text: str,
        issues: list[Issue],
    ) -> None:
        documented = linked.laterality if linked else None
        if not documented:
            for ev in evidence:
                if ev.start is not None:
                    m = re.search(r"\b(left|right|bilateral)\b", text[ev.start : ev.end], re.IGNORECASE)
                    if m:
                        documented = m.group(1).lower()
                        break
        if not documented:
            return
        desc = info.description.lower()
        if _UNSPEC_LAT.search(desc):
            issues.append(
                Issue(
                    "laterality_unspecified",
                    FLAG,
                    f"Documentation specifies '{documented}' but code is unspecified laterality",
                    ErrorCategory.WRONG_SPECIFICITY,
                )
            )
        elif documented in ("left", "right"):
            other = "right" if documented == "left" else "left"
            if re.search(rf"\b{other}\b", desc) and not re.search(rf"\b{documented}\b", desc):
                issues.append(
                    Issue(
                        "laterality_mismatch",
                        FLAG,
                        f"Documentation says '{documented}' but code describes '{other}'",
                        ErrorCategory.WRONG_CODE,
                    )
                )

    def _demographics(self, code: str, info: CodeInfo, ctx: ValidationContext, issues: list[Issue]) -> None:
        sex = (ctx.patient_sex or "").upper()
        if sex == "M" and _in_ranges(code, _FEMALE_ONLY):
            issues.append(
                Issue(
                    "sex_edit",
                    FLAG,
                    f"{code} is female-specific but patient is male",
                    ErrorCategory.WRONG_CODE,
                )
            )
        if sex == "F" and _in_ranges(code, _MALE_ONLY):
            issues.append(
                Issue(
                    "sex_edit",
                    FLAG,
                    f"{code} is male-specific but patient is female",
                    ErrorCategory.WRONG_CODE,
                )
            )
        if ctx.patient_age is not None and code.startswith("O") and not (9 <= ctx.patient_age <= 64):
            issues.append(
                Issue("age_edit", FLAG, f"Maternity code {code} outside age 9-64", ErrorCategory.WRONG_CODE)
            )

    # ------------------------------------------------------------------ set-level rules
    def _set_rules(self, results: list[ValidatedProposal], ctx: ValidationContext) -> None:
        valid = [r for r in results if r.info is not None]
        codes = {r.code for r in valid}
        first_dx_seq = next((r.proposal.sequence for r in results if r.proposal.system == "ICD-10-CM"), 1)

        for r in valid:
            snap = ctx.snapshots.get(r.proposal.system)
            if snap is None:
                continue
            # Excludes1: never report together
            for owner, note in snap.inherited(r.code, "excludes1"):
                for ref in parse_code_refs(note):
                    clash = [o.code for o in valid if o is not r and ref_matches(o.code, ref)]
                    if clash:
                        r.issues.append(
                            Issue(
                                "excludes1",
                                FLAG,
                                f"Excludes1 ({owner}): '{note}' conflicts with {', '.join(clash)}",
                                ErrorCategory.WRONG_CODE,
                                clash,
                            )
                        )
            # use additional code: informational hint when referenced code is absent. Notes owned by the
            # same code are often alternatives ("insulin (Z79.4)" / "oral hypoglycemic drugs (Z79.84)"):
            # once any of them is satisfied, the instruction is met.
            by_owner: dict[str, list[str]] = {}
            for owner, note in snap.inherited(r.code, "use_additional_code"):
                by_owner.setdefault(owner, []).append(note)
            hints = []
            for notes in by_owner.values():
                refs = [ref for n in notes for ref in parse_code_refs(n)]
                if refs and not any(ref_matches(c, ref) for c in codes for ref in refs):
                    hints.extend(n for n in notes if parse_code_refs(n))
            if hints:
                r.issues.append(
                    Issue(
                        "use_additional_code",
                        INFO,
                        "Consider additional code: " + "; ".join(hints[:4]),
                        ErrorCategory.MISSING_CODE,
                    )
                )
            # manifestation codes cannot be first-listed and need their etiology
            if r.info and _MANIFESTATION.search(r.info.description):
                if r.proposal.sequence == first_dx_seq:
                    r.issues.append(
                        Issue(
                            "manifestation_sequencing",
                            FLAG,
                            "Manifestation code cannot be first-listed",
                            ErrorCategory.VALIDATION_FAILURE,
                        )
                    )
                cf_refs = [
                    ref for _o, n in snap.inherited(r.code, "code_first") for ref in parse_code_refs(n)
                ]
                if cf_refs and not any(ref_matches(c, ref) for c in codes for ref in cf_refs):
                    r.issues.append(
                        Issue(
                            "code_first_missing",
                            FLAG,
                            "Underlying (etiology) condition must be coded first",
                            ErrorCategory.MISSING_CODE,
                        )
                    )

        # unspecified code reported alongside a more specific code of the same category
        by_cat: dict[str, list[ValidatedProposal]] = {}
        for r in valid:
            if r.proposal.system == "ICD-10-CM":
                by_cat.setdefault(r.code[:3], []).append(r)
        for cat, group in by_cat.items():
            if len(group) < 2:
                continue
            for r in group:
                desc = r.info.description.lower() if r.info else ""
                is_unspec = "unspecified" in desc or (
                    cat in _DIABETES_CATS and "without complications" in desc
                )
                others = [o.code for o in group if o is not r]
                if is_unspec and others:
                    r.issues.append(
                        Issue(
                            "unspecified_with_specific",
                            FLAG,
                            f"{r.code} ({r.info.description}) conflicts with more specific {', '.join(others)}",
                            ErrorCategory.WRONG_SPECIFICITY,
                            others,
                        )
                    )
