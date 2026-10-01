"""Clinical information extraction: rules + (optional) LLM, reconciled by deterministic ConText.

The LLM finds mentions the lexicon doesn't know; ConText independently re-derives
assertions for every span. When the LLM and ConText disagree about negation or
uncertainty the entity is marked `assertion_conflict` and the conservative value
(negated/uncertain) wins -> the suggestion is forced into mandatory review.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from app.extraction.context import ConTextAnalyzer
from app.extraction.rule_extractor import RuleBasedExtractor, load_lexicon
from app.extraction.types import Entity
from app.ingestion.sections import Section, section_at
from app.llm.prompts import EXTRACTION_PROMPT_VERSION

log = logging.getLogger(__name__)


@dataclass
class ExtractionRun:
    entities: list[Entity]
    provider: str
    model: str
    prompt_version: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    raw_response: str | None = None
    warnings: list[str] = field(default_factory=list)


def locate(text: str, needle: str, used: set[tuple[int, int]] | None = None) -> tuple[int, int] | None:
    """Find a verbatim (or whitespace/case-normalized) occurrence of `needle` in `text`."""
    if not needle.strip():
        return None
    used = used or set()
    start = 0
    while True:
        i = text.find(needle, start)
        if i == -1:
            break
        if (i, i + len(needle)) not in used:
            return i, i + len(needle)
        start = i + 1
    pattern = r"\s+".join(re.escape(p) for p in needle.split())
    for m in re.finditer(pattern, text, re.IGNORECASE):
        if (m.start(), m.end()) not in used:
            return m.start(), m.end()
    return None


class ClinicalExtractor:
    def __init__(self, llm_model=None, context: ConTextAnalyzer | None = None):  # noqa: ANN001
        self.rules = RuleBasedExtractor()
        self.context = context or ConTextAnalyzer()
        self.llm_model = llm_model  # CodingModel with .extract_entities, or None

    def extract(self, text: str, sections: list[Section]) -> ExtractionRun:
        started = time.perf_counter()
        rule_entities = self.rules.extract(text, sections)
        self.context.analyze(text, rule_entities)
        run = ExtractionRun(entities=rule_entities, provider="rules", model="rules+context-v1")

        if self.llm_model is not None and getattr(self.llm_model, "supports_extraction", False):
            try:
                llm_run = self.llm_model.extract_entities(text)
            except Exception as exc:  # noqa: BLE001 - degrade to rules, never fail the document
                log.warning("llm_extraction_failed", extra={"error": str(exc)[:300]})
                run.warnings.append(f"LLM extraction failed, rules only: {str(exc)[:200]}")
            else:
                llm_entities = self._llm_to_entities(text, sections, llm_run.parsed.entities, run)
                run.entities = self._merge(rule_entities, llm_entities)
                run.provider, run.model = "merged", f"rules+context-v1|{llm_run.model}"
                run.prompt_version = EXTRACTION_PROMPT_VERSION
                run.input_tokens, run.output_tokens = llm_run.input_tokens, llm_run.output_tokens
                run.cost_usd, run.raw_response = llm_run.cost_usd, llm_run.raw_text
        run.entities.sort(key=lambda e: (e.start, -e.end))
        run.latency_ms = int((time.perf_counter() - started) * 1000)
        return run

    def _llm_to_entities(self, text: str, sections: list[Section], items, run: ExtractionRun) -> list[Entity]:  # noqa: ANN001
        used: set[tuple[int, int]] = set()
        out: list[Entity] = []
        lex = load_lexicon()
        for it in items:
            span = locate(text, it.text, used)
            if span is None:
                run.warnings.append("LLM entity not found verbatim in note (dropped)")
                continue
            used.add(span)
            ent = Entity(
                text=text[span[0] : span[1]],
                category=it.category,
                start=span[0],
                end=span[1],
                normalized=(lex.get(it.text.lower(), (None, None))[1] or it.text.lower()),
                section=section_at(sections, span[0]),
                laterality=it.laterality,
                severity=it.severity,
                temporal=it.temporal,
                source="llm",
            )
            # deterministic assertion on the same span
            self.context.analyze(text, [ent])
            llm_flags = (it.negated, it.uncertain)
            ctx_flags = (ent.negated, ent.uncertain)
            if llm_flags != ctx_flags:
                ent.assertion_conflict = True
            ent.negated = ent.negated or it.negated
            ent.uncertain = ent.uncertain or it.uncertain
            ent.historical = ent.historical or it.historical
            ent.family = ent.family or it.family
            out.append(ent)
        return out

    @staticmethod
    def _merge(rule_entities: list[Entity], llm_entities: list[Entity]) -> list[Entity]:
        merged = list(llm_entities)
        for r in rule_entities:
            overlapping = [e for e in merged if e.overlaps(r)]
            if not overlapping:
                merged.append(r)
                continue
            for e in overlapping:
                # keep the LLM span, but union assertions (conservative) and borrow lexicon normalization
                if (e.negated, e.uncertain) != (r.negated, r.uncertain) and e.category == r.category:
                    e.assertion_conflict = True
                e.negated = e.negated or r.negated
                e.uncertain = e.uncertain or r.uncertain
                e.family = e.family or r.family
                e.hypothetical = e.hypothetical or r.hypothetical
                if r.normalized and r.source == "rules" and (e.end - e.start) <= (r.end - r.start) + 3:
                    e.normalized = r.normalized
                e.source = "merged"
        return merged
