"""Coding engine: concept grouping -> retrieval (RAG) -> model proposals.

Modes (used by evaluation baselines):
  direct      - Baseline 1: LLM reads the note, no retrieval, no NLP
  rag         - Baseline 2: LLM + retrieval on raw sentences (no NLP entities/assertions)
  full        - NLP entities + assertions + entity-level retrieval + LLM (validation added downstream)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from app.coding.guideline_rules import RuleChange, apply_guideline_rules
from app.coding.models import CodingModel, CodingRequest, ConceptGroup, ModelOutput
from app.coding.procedures import build_procedure_groups, retrieve_procedures
from app.extraction.context import sentence_spans
from app.extraction.types import Entity
from app.ingestion.sections import Section, section_at
from app.rag.lexical import tokenize
from app.rag.retriever import HybridRetriever, RetrievedCode

# Sections whose statements are the provider's diagnostic conclusions ("Plan" holds orders, not diagnoses)
ASSESSMENT_SECTIONS = {
    "assessment",
    "discharge_diagnoses",
    "postoperative_diagnosis",
    "preoperative_diagnosis",
}
EXCLUDED_ASSERTION_SECTIONS = {"review_of_systems", "allergies"}
CODABLE_CATEGORIES = {"condition", "symptom", "finding"}

# medication normalization -> long-term drug therapy query (ICD-10-CM Z79.-)
DERIVED_MEDICATION_QUERIES: dict[str, tuple[str, bool]] = {
    # normalized: (query, requires_diabetes)
    "insulin": ("long term (current) use of insulin", True),
    "oral hypoglycemic": ("long term (current) use of oral hypoglycemic drugs", True),
    "injectable non-insulin antidiabetic": (
        "long term (current) use of injectable non-insulin antidiabetic drugs",
        True,
    ),
    "anticoagulant": ("long term (current) use of anticoagulants", False),
}


@dataclass
class EngineResult:
    output: ModelOutput
    groups: list[ConceptGroup]
    retrieval_version: str
    kb_version: str
    mode: str
    retrieval_ms: int = 0
    total_ms: int = 0
    candidates_by_code: dict[str, RetrievedCode] = field(default_factory=dict)
    rule_changes: list[RuleChange] = field(default_factory=list)


def build_groups(text: str, entities: list[Entity]) -> list[ConceptGroup]:
    by_key: dict[str, list[Entity]] = {}
    for e in entities:
        if e.category in CODABLE_CATEGORIES:
            by_key.setdefault(e.concept_key, []).append(e)

    groups: list[ConceptGroup] = []
    for key, mentions in by_key.items():
        considered = [m for m in mentions if m.section not in EXCLUDED_ASSERTION_SECTIONS] or mentions
        affirmed = [m for m in considered if m.affirmed]
        current = [m for m in affirmed if not m.historical or m.section in ASSESSMENT_SECTIONS]

        def rank(m: Entity) -> tuple:
            return (m.affirmed, m.section in ASSESSMENT_SECTIONS, not m.historical, m.end - m.start)

        rep = max(affirmed or considered, key=rank)
        groups.append(
            ConceptGroup(
                key=key,
                mentions=mentions,
                representative=rep,
                query=_query_for(rep),
                affirmed=bool(affirmed),
                negated=not affirmed and any(m.negated for m in considered),
                uncertain=not affirmed and any(m.uncertain and not m.negated for m in considered),
                family=all(m.family for m in mentions),
                historical_only=bool(affirmed) and not current,
                in_diagnostic_section=any(m.section in ASSESSMENT_SECTIONS and m.affirmed for m in mentions),
            )
        )

    groups = _merge_subsumed(groups)
    groups.extend(_derived_family_history(groups))
    groups.extend(_derived_bmi(text, groups))
    has_diabetes = any(g.affirmed and "diabetes" in g.key for g in groups)
    seen_derived: set[str] = set()
    for e in entities:
        if e.category != "medication" or e.negated or e.hypothetical or e.family:
            continue
        if e.section in ("plan", "assessment"):
            continue  # newly started/planned drugs are not established long-term therapy
        spec = DERIVED_MEDICATION_QUERIES.get((e.normalized or "").lower())
        if not spec or spec[0] in seen_derived or (spec[1] and not has_diabetes):
            continue
        seen_derived.add(spec[0])
        groups.append(
            ConceptGroup(
                key=f"derived:{spec[0]}",
                mentions=[e],
                representative=e,
                query=spec[0],
                affirmed=True,
                negated=False,
                uncertain=False,
                family=False,
                historical_only=False,
                in_diagnostic_section=False,
                derived=True,
            )
        )
    groups.sort(key=lambda g: (not g.in_diagnostic_section, g.derived, g.representative.start))
    return groups


_BMI_RE = re.compile(r"\bBMI(?:\s+(?:of|is|was))?\s*:?\s*(\d{2}(?:\.\d)?)", re.IGNORECASE)
_WEIGHT_DX = re.compile(r"obes|overweight|underweight|malnutrition|cachexia", re.IGNORECASE)


def _bmi_query(value: float) -> str | None:
    """Adult BMI -> Z68 description (ICD-10-CM Z68.1-Z68.45)."""
    if value < 20:
        return "body mass index [BMI] 19.9 or less, adult"
    if value < 40:
        lo = int(value)
        return f"body mass index [BMI] {lo}.0-{lo}.9, adult"
    for hi, desc in ((45, "40.0-44.9"), (50, "45.0-49.9"), (60, "50.0-59.9"), (70, "60.0-69.9")):
        if value < hi:
            return f"body mass index [BMI] {desc}, adult"
    return "body mass index [BMI] 70 or greater, adult"


def _derived_bmi(text: str, groups: list[ConceptGroup]) -> list[ConceptGroup]:
    """Guideline I.B.14: BMI is reported only with an associated provider-documented weight diagnosis."""
    dx = next((g for g in groups if g.affirmed and _WEIGHT_DX.search(g.representative.text)), None)
    m = _BMI_RE.search(text)
    if not dx or not m:
        return []
    q = _bmi_query(float(m.group(1)))
    if not q:
        return []
    ent = Entity(
        text=m.group(0), category="finding", start=m.start(), end=m.end(), section=None, source="derived"
    )
    return [
        ConceptGroup(
            key=f"derived:{q}",
            mentions=[ent],
            representative=ent,
            query=q,
            affirmed=True,
            negated=False,
            uncertain=False,
            family=False,
            historical_only=False,
            in_diagnostic_section=False,
            derived=True,
        )
    ]


def _derived_family_history(groups: list[ConceptGroup]) -> list[ConceptGroup]:
    """Family history the provider lists in the assessment -> 'family history of ...' (Z80-Z84)."""
    out = []
    for g in groups:
        fam = [m for m in g.mentions if m.family and m.section in ASSESSMENT_SECTIONS and not m.negated]
        if not fam:
            continue
        m = fam[0]
        concept = m.normalized or m.text
        q = f"family history of {concept}"
        out.append(
            ConceptGroup(
                key=f"derived:{q}",
                mentions=[m],
                representative=m,
                query=q,
                affirmed=True,
                negated=False,
                uncertain=False,
                family=False,
                historical_only=False,
                in_diagnostic_section=True,
                derived=True,
            )
        )
    return out


_STEM = {"hypertensive": "hypertension", "diabetic": "diabetes", "asthmatic": "asthma", "renal": "kidney"}


def _concept_tokens(text: str) -> set[str]:
    return {_STEM.get(t, t) for t in tokenize(text, expand=True)}


def _merge_subsumed(groups: list[ConceptGroup]) -> list[ConceptGroup]:
    """Fold vague mentions into the specific diagnosis that contains them.

    "diabetes" (chief complaint) + "Type 2 diabetes mellitus with hyperglycemia" (assessment) are one
    concept; "hypertension" + "hypertensive chronic kidney disease" is the ICD-10-CM combination.
    """
    specific = [g for g in groups if g.affirmed and g.in_diagnostic_section]
    keep: list[ConceptGroup] = []
    for g in groups:
        tok = _concept_tokens(g.representative.text)
        target = None
        separately_listed = g.in_diagnostic_section and g.representative.source == "rules-list"
        if g.affirmed and tok and not separately_listed:
            for s in specific:
                if s is g:
                    continue
                st = _concept_tokens(s.representative.text)
                if tok < st or (tok == st and not g.in_diagnostic_section):
                    target = s
                    break
        if target is None:
            keep.append(g)
        else:
            target.mentions.extend(g.mentions)
    return keep


def _query_for(e: Entity) -> str:
    q = e.text
    low = q.lower()
    if e.normalized and e.normalized.lower() not in low and len(e.text) <= 6:
        q = f"{e.normalized} ({e.text})"
    elif e.normalized and e.normalized.lower() not in low:
        q = f"{q} {e.normalized}"
    for extra in (e.laterality, e.severity):
        if extra and extra.lower() not in q.lower():
            q = f"{q} {extra}"
    return q


def build_sentence_groups(text: str, sections: list[Section]) -> list[ConceptGroup]:
    """Baseline-2 queries: raw sentences from assessment sections (or the whole note)."""
    spans = sentence_spans(text)
    preferred = [(s, e) for s, e in spans if section_at(sections, s) in ASSESSMENT_SECTIONS] or spans
    groups = []
    for s, e in preferred[:40]:
        chunk = text[s:e].strip()
        if len(chunk) < 4:
            continue
        ent = Entity(
            text=chunk[:200],
            category="condition",
            start=s,
            end=s + min(len(chunk), 200),
            section=section_at(sections, s),
            source="sentence",
        )
        groups.append(
            ConceptGroup(
                key=f"s{s}",
                mentions=[ent],
                representative=ent,
                query=chunk[:300],
                affirmed=True,
                negated=False,
                uncertain=False,
                family=False,
                historical_only=False,
                in_diagnostic_section=ent.section in ASSESSMENT_SECTIONS,
            )
        )
    return groups


_HEAD_SPLIT = re.compile(r"\s+(?:due to|secondary to|associated with|complicated by|from)\s+", re.IGNORECASE)


def _compound_head(text: str) -> str | None:
    parts = _HEAD_SPLIT.split(text, maxsplit=1)
    return parts[0].strip() if len(parts) == 2 and len(parts[0].strip()) >= 3 else None


class CodingEngine:
    def __init__(
        self,
        model: CodingModel,
        retriever: HybridRetriever | None,
        procedure_retrievers: dict[str, HybridRetriever] | None = None,
        guideline_lookup=None,  # noqa: ANN001 - callable(query, code|None) -> list[str]
    ):
        self.model = model
        self.retriever = retriever
        self.procedure_retrievers = procedure_retrievers or {}
        self.guideline_lookup = guideline_lookup

    def run(
        self,
        text: str,
        sections: list[Section],
        entities: list[Entity],
        *,
        encounter_type: str,
        patient_sex: str | None = None,
        patient_age: int | None = None,
        mode: str = "full",
        systems: list[str] | None = None,
    ) -> EngineResult:
        started = time.perf_counter()
        kb_version = self.retriever.snapshot.version if self.retriever else "none"
        retrieval_version = self.retriever.version if self.retriever else "none"

        if mode == "direct":
            out = self.model.propose_direct(text, encounter_type)
            return EngineResult(
                out, [], "none", kb_version, mode, 0, int((time.perf_counter() - started) * 1000)
            )

        groups = build_sentence_groups(text, sections) if mode == "rag" else build_groups(text, entities)
        t0 = time.perf_counter()
        if self.retriever is None:
            raise RuntimeError("Retriever required for RAG modes")
        to_query = [g for g in groups if mode == "rag" or (g.affirmed or g.uncertain)]
        results = self.retriever.retrieve_many([g.query for g in to_query])
        for g, cands in zip(to_query, results, strict=True):
            g.candidates = cands
        # Combination phrases ("sepsis due to UTI") also retrieve for their head concept, so a
        # separately-coded condition isn't lost when no single combination code exists.
        heads = [(g, h) for g in to_query if mode != "rag" and (h := _compound_head(g.representative.text))]
        if heads:
            for (g, _h), cands in zip(
                heads, self.retriever.retrieve_many([h for _g, h in heads]), strict=True
            ):
                known = {c.code for c in g.candidates}
                for c in cands:
                    if c.code not in known:
                        c.score = round(c.score * 0.9, 4)
                        g.candidates.append(c)
                g.candidates.sort(key=lambda c: -c.score)
                for i, c in enumerate(g.candidates, start=1):
                    c.rank = i
        if mode == "full" and self.procedure_retrievers:
            proc_groups = build_procedure_groups(text, sections, entities)
            retrieve_procedures(proc_groups, self.procedure_retrievers)
            groups.extend(proc_groups)
        retrieval_ms = int((time.perf_counter() - t0) * 1000)

        guideline_texts: list[str] = []
        if self.guideline_lookup and mode == "full":
            seen_refs: set[str] = set()
            for g in groups:
                if not (g.affirmed or g.uncertain):
                    continue
                top = g.candidates[0].code if g.candidates else None
                for ref, excerpt in self.guideline_lookup(g.query, top, g.kind):
                    if ref not in seen_refs and len(guideline_texts) < 6:
                        seen_refs.add(ref)
                        guideline_texts.append(excerpt)

        systems_used = list(dict.fromkeys((systems or ["ICD-10-CM"]) + list(self.procedure_retrievers)))
        req = CodingRequest(
            text=text,
            groups=groups,
            encounter_type=encounter_type,
            patient_sex=patient_sex,
            patient_age=patient_age,
            systems=systems_used,
            kb_label=f"ICD-10-CM {kb_version}"
            + "".join(f", {k} {r.snapshot.version}" for k, r in self.procedure_retrievers.items()),
            show_assertions=(mode != "rag"),
            guidelines=guideline_texts,
        )
        out = self.model.propose(req)
        # Official-guideline coder rules (combination codes, history vs acute, 7th characters, Z79...)
        # run on every model's output; each change is cited and visible to the coder.
        rule_changes: list[RuleChange] = []
        if mode == "full":
            rule_changes = apply_guideline_rules(
                out.codes,
                out.not_coded,
                text,
                sections,
                encounter_type,
                self.retriever.snapshot,
                patient_age=patient_age,
            )
        by_code: dict[str, RetrievedCode] = {}
        for g in groups:
            for rc in g.candidates:
                if rc.code not in by_code or rc.score > by_code[rc.code].score:
                    by_code[rc.code] = rc
        return EngineResult(
            out,
            groups,
            retrieval_version,
            kb_version,
            mode,
            retrieval_ms,
            int((time.perf_counter() - started) * 1000),
            by_code,
            rule_changes,
        )
