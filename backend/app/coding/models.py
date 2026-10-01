"""Coding models: the component that turns (note + entities + candidates) into code proposals.

* OpenRouterCodingModel - production: LLM over retrieved official references
* HeuristicCodingModel  - deterministic retrieval-only baseline; also the offline/dev fallback
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from app.coding.guideline_rules import Doc, documentation_gap
from app.core.config import Settings, get_settings
from app.extraction.context import sentence_bounds
from app.llm.client import LLMResult, OpenRouterClient
from app.llm.prompts import (
    CODING_PROMPT_VERSION,
    CODING_SYSTEM,
    DIRECT_PROMPT_VERSION,
    DIRECT_SYSTEM,
    EXTRACTION_SYSTEM,
    UNCERTAINTY_INPATIENT,
    UNCERTAINTY_OUTPATIENT,
    build_coding_user_message,
)
from app.llm.schemas import CodingOutput, ExtractionOutput, LLMCode, NotCoded

MAX_NOTE_CHARS = 30000


@dataclass
class ConceptGroup:
    """All mentions of one clinical concept in a note, with a representative mention."""

    key: str
    mentions: list  # list[Entity]
    representative: object  # Entity
    query: str
    affirmed: bool
    negated: bool
    uncertain: bool
    family: bool
    historical_only: bool
    in_diagnostic_section: bool
    derived: bool = False  # synthesized (e.g. long-term drug therapy from medication list)
    candidates: list = field(default_factory=list)  # list[RetrievedCode]
    kind: str = "diagnosis"  # diagnosis | procedure


@dataclass
class CodingRequest:
    text: str
    groups: list[ConceptGroup]
    encounter_type: str
    patient_sex: str | None
    patient_age: int | None
    systems: list[str]
    kb_label: str
    use_candidates: bool = True
    show_assertions: bool = True
    guidelines: list[str] = field(default_factory=list)  # official guideline excerpts for grounding


@dataclass
class ModelOutput:
    codes: list[LLMCode]
    not_coded: list[NotCoded]
    provider: str
    model: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    raw_text: str | None = None


class CodingModel(Protocol):
    provider: str
    model: str
    supports_extraction: bool
    supports_direct: bool

    def extract_entities(self, text: str) -> LLMResult: ...
    def propose(self, req: CodingRequest) -> ModelOutput: ...
    def propose_direct(self, text: str, encounter_type: str) -> ModelOutput: ...


def _assertion_label(g: ConceptGroup) -> str:
    flags = []
    if g.negated:
        flags.append("NEGATED")
    if g.uncertain:
        flags.append("UNCERTAIN")
    if g.family:
        flags.append("FAMILY-MEMBER")
    if g.historical_only:
        flags.append("HISTORICAL")
    if not flags:
        flags.append("affirmed")
    return ",".join(flags)


def _format_candidates(g: ConceptGroup, limit: int = 8) -> str:
    lines = []
    for rc in g.candidates[:limit]:
        info = rc.info
        extra = []
        if info.inclusion_terms:
            extra.append("Incl: " + "; ".join(info.inclusion_terms[:3]))
        if info.excludes1:
            extra.append("Excl1: " + "; ".join(info.excludes1[:3]))
        if info.use_additional_code:
            extra.append("UseAdd: " + "; ".join(info.use_additional_code[:3]))
        if info.code_first:
            extra.append("CodeFirst: " + "; ".join(info.code_first[:2]))
        tail = (" | " + " | ".join(extra)) if extra else ""
        sys_tag = f" [{info.system}]" if info.system != "ICD-10-CM" else ""
        lines.append(f"    - {info.code}{sys_tag}: {info.description}{tail}")
    return "\n".join(lines) if lines else "    (no candidates retrieved)"


class OpenRouterCodingModel:
    provider = "openrouter"
    supports_extraction = True
    supports_direct = True

    def __init__(
        self,
        settings: Settings | None = None,
        model: str | None = None,
        client: OpenRouterClient | None = None,
    ):
        self.s = settings or get_settings()
        self.model = model or self.s.llm_model
        self.client = client or OpenRouterClient(self.s)
        self.supports_extraction = self.s.llm_extraction_enabled

    def extract_entities(self, text: str) -> LLMResult:
        return self.client.complete_structured(
            [
                {"role": "system", "content": EXTRACTION_SYSTEM},
                {"role": "user", "content": f"CLINICAL NOTE:\n<<<\n{text[:MAX_NOTE_CHARS]}\n>>>"},
            ],
            ExtractionOutput,
            schema_name="clinical_entities",
            model=self.model,
        )

    def propose(self, req: CodingRequest) -> ModelOutput:
        uncertainty = UNCERTAINTY_INPATIENT if req.encounter_type == "inpatient" else UNCERTAINTY_OUTPATIENT
        system = CODING_SYSTEM.format(
            systems="/".join(req.systems),
            encounter=req.encounter_type,
            kb_label=req.kb_label,
            uncertainty_rule=uncertainty,
        )
        ent_lines, cand_lines = [], []
        for i, g in enumerate(req.groups, start=1):
            rep = g.representative
            assertion = _assertion_label(g) if req.show_assertions else "-"
            ent_lines.append(
                f"E{i} | {rep.text} | {rep.category} | {rep.section or '-'} | {assertion}"
                + (" | derived from medication list" if g.derived else "")
                + (" | PROCEDURE performed this encounter" if g.kind == "procedure" else "")
            )
            if req.use_candidates and g.candidates:
                cand_lines.append(f"E{i} ({rep.text}):\n{_format_candidates(g)}")
        demo = f"encounter={req.encounter_type}; sex={req.patient_sex or 'unknown'}; age={req.patient_age if req.patient_age is not None else 'unknown'}"
        user = build_coding_user_message(
            req.text[:MAX_NOTE_CHARS],
            "\n".join(ent_lines) or "(none)",
            "\n\n".join(cand_lines) or "(none)",
            demo,
            "\n\n".join(req.guidelines) if req.guidelines else "",
        )
        res = self.client.complete_structured(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            CodingOutput,
            schema_name="coding_result",
            model=self.model,
        )
        out: CodingOutput = res.parsed  # type: ignore[assignment]
        return ModelOutput(
            out.codes,
            out.not_coded,
            self.provider,
            res.model,
            CODING_PROMPT_VERSION,
            res.input_tokens,
            res.output_tokens,
            res.cost_usd,
            res.latency_ms,
            res.raw_text,
        )

    def propose_direct(self, text: str, encounter_type: str) -> ModelOutput:
        res = self.client.complete_structured(
            [
                {"role": "system", "content": DIRECT_SYSTEM},
                {
                    "role": "user",
                    "content": f"ENCOUNTER: {encounter_type}\nCLINICAL NOTE:\n<<<\n{text[:MAX_NOTE_CHARS]}\n>>>",
                },
            ],
            CodingOutput,
            schema_name="coding_result",
            model=self.model,
        )
        out: CodingOutput = res.parsed  # type: ignore[assignment]
        return ModelOutput(
            out.codes,
            out.not_coded,
            self.provider,
            res.model,
            DIRECT_PROMPT_VERSION,
            res.input_tokens,
            res.output_tokens,
            res.cost_usd,
            res.latency_ms,
            res.raw_text,
        )


class HeuristicCodingModel:
    """Deterministic retrieval-only coder.

    Picks the best retrieved candidate for each affirmed concept, applying a few
    guideline rules (no negated/family/uncertain-outpatient concepts; symptoms only
    when no definitive diagnosis explains the visit). Useful as a baseline and to
    run the platform end-to-end without an LLM key. Not intended for production coding.
    """

    provider = "heuristic"
    model = "retrieval-top1-v1"
    supports_extraction = False
    supports_direct = False
    min_score = 0.40
    min_procedure_score = 0.35

    def extract_entities(self, text: str) -> LLMResult:  # pragma: no cover - not supported
        raise NotImplementedError

    def propose_direct(self, text: str, encounter_type: str) -> ModelOutput:  # pragma: no cover
        raise NotImplementedError("Direct (no-retrieval) coding needs an LLM")

    def propose(self, req: CodingRequest) -> ModelOutput:
        codes: list[LLMCode] = []
        not_coded: list[NotCoded] = []
        has_dx = any(
            g.affirmed
            and not g.derived
            and g.representative.category == "condition"
            and g.in_diagnostic_section
            for g in req.groups
        )
        seen: set[str] = set()
        doc = Doc(req.text, [], req.encounter_type)
        for g in req.groups:
            rep = g.representative
            if g.kind == "procedure":
                self._procedure(req, g, codes, not_coded, seen)
                continue
            reason = None
            if g.negated:
                reason = "negated"
            elif g.family:
                reason = "family history"
            elif g.uncertain and req.encounter_type != "inpatient":
                reason = "uncertain diagnosis (outpatient)"
            elif g.historical_only:
                reason = "historical"
            elif not g.affirmed and not (g.uncertain and req.encounter_type == "inpatient"):
                # Guidelines II.H / III.C: inpatient uncertain diagnoses at discharge are coded as established
                reason = "not affirmed"
            elif (
                rep.category in ("symptom", "finding")
                and has_dx
                and not g.in_diagnostic_section
                and not g.derived
            ):
                reason = "symptom explained by documented diagnosis"
            elif rep.category not in ("condition", "symptom", "finding") and not g.derived:
                reason = "not a diagnosis"
            elif not g.candidates:
                reason = "no candidate retrieved"
            elif g.candidates[0].score < self.min_score:
                reason = "low retrieval score"
            if reason:
                not_coded.append(NotCoded(entity=rep.text, reason=reason))
                continue
            best = _first_supported(g.candidates, req, doc)
            if best is None:
                not_coded.append(
                    NotCoded(entity=rep.text, reason="no retrieved code is supported by the documentation")
                )
                continue
            if best.code in seen:
                continue
            seen.add(best.code)
            s, e = sentence_bounds(req.text, rep.start)
            sentence = req.text[s:e].strip()
            evidence = sentence if len(sentence) <= 220 else rep.text
            codes.append(
                LLMCode(
                    code=best.code,
                    code_system="ICD-10-CM",
                    description=best.info.description,
                    entity=rep.text,
                    evidence=[evidence],
                    rationale=f"Top retrieved reference for '{rep.text}' ({', '.join(best.sources)})",
                    confidence=round(min(0.95, best.score), 3),
                    from_candidates=True,
                )
            )
        # first-listed: a diagnosis from a diagnostic section
        codes.sort(
            key=lambda c: (
                0
                if any(g.representative.text == c.entity and g.in_diagnostic_section for g in req.groups)
                else 1
            )
        )
        return ModelOutput(codes, not_coded, self.provider, self.model, "heuristic-v1")

    def _procedure(
        self,
        req: CodingRequest,
        g: ConceptGroup,
        codes: list[LLMCode],
        not_coded: list[NotCoded],
        seen: set[str],
    ) -> None:
        rep = g.representative
        if not g.candidates:
            not_coded.append(NotCoded(entity=rep.text, reason="procedure: no candidate retrieved"))
            return
        best = g.candidates[0]
        if best.score < self.min_procedure_score:
            not_coded.append(
                NotCoded(entity=rep.text, reason="procedure: low retrieval score (code manually)")
            )
            return
        if best.info.attributes.get("range"):
            not_coded.append(
                NotCoded(
                    entity=rep.text,
                    reason=f"procedure maps to {best.info.system} range {best.code} ({best.info.description}); "
                    "choose the specific code (size/site/number) in the full code set",
                )
            )
            return
        if best.code in seen:
            return
        seen.add(best.code)
        s, e = sentence_bounds(req.text, rep.start)
        sentence = req.text[s:e].strip()
        codes.append(
            LLMCode(
                code=best.code,
                code_system=best.info.system,
                description=best.info.description,
                entity=rep.text,
                evidence=[sentence if len(sentence) <= 220 else rep.text],
                rationale=f"Top retrieved {best.info.system} reference for performed procedure '{rep.text}'",
                confidence=round(min(0.7, best.score), 3),
                from_candidates=True,
            )
        )


def _first_supported(candidates: list, req: CodingRequest, doc: Doc, top: int = 6):  # noqa: ANN202
    """Best near-top retrieved candidate (score within 0.15 of the best) whose qualifiers (site, organism,
    complication, type, cause...) are documented. Never returns a candidate with a documentation gap."""
    if not candidates:
        return None
    lead = candidates[0].score
    for rc in candidates[:top]:
        if rc.score < HeuristicCodingModel.min_score or rc.score < lead - 0.15:
            break
        if rc.info.system != "ICD-10-CM" or documentation_gap(rc.code, rc.info.description, doc) is None:
            return rc
    return None


def build_model(settings: Settings | None = None, model: str | None = None) -> CodingModel:
    s = settings or get_settings()
    if s.llm_provider == "openrouter":
        return OpenRouterCodingModel(s, model=model)
    return HeuristicCodingModel()


_WS = re.compile(r"\s+")
