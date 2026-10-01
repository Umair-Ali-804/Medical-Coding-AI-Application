"""Deterministic clinical entity recognition.

Two complementary recognizers:
  1. Lexicon matcher  - curated terms/abbreviations -> category + normalized concept
  2. Problem-list parser - numbered / bulleted items under diagnostic sections
     ("Assessment", "Discharge Diagnoses", ...) become condition candidates even
     when the lexicon doesn't know them; these are the richest retrieval queries.

Assertions (negation etc.) are added afterwards by ConTextAnalyzer.
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources

from app.extraction.types import Entity
from app.ingestion.sections import DIAGNOSTIC_SECTIONS, Section, section_at


@lru_cache
def load_lexicon() -> dict[str, tuple[str, str]]:
    lex: dict[str, tuple[str, str]] = {}
    raw = resources.files("app.extraction").joinpath("lexicon/clinical_terms.tsv").read_text("utf-8")
    for line in raw.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        term, category, normalized = (p.strip() for p in parts[:3])
        lex[term.lower()] = (category, normalized)
    return lex


@lru_cache
def _lexicon_regexes() -> tuple[re.Pattern[str], re.Pattern[str]]:
    lex = load_lexicon()
    long_terms = [t for t in lex if len(t) > 3]
    short_terms = [t for t in lex if len(t) <= 3]

    def build(terms: list[str], flags: int) -> re.Pattern[str]:
        alts = sorted((re.escape(t).replace(r"\ ", r"\s+") for t in terms), key=len, reverse=True)
        return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(alts) + r")(?![A-Za-z0-9])", flags)

    # Short abbreviations (HTN, CAD, UTI...) must be upper-case in the note to avoid
    # matching ordinary words ("pad", "gad", "sob").
    return build(long_terms, re.IGNORECASE), build([t.upper() for t in short_terms], 0)


_LIST_ITEM_RE = re.compile(r"^[ \t]*(?:\d{1,2}[.)]|[-*•]|#\d*\.?)[ \t]+(?P<item>[^\n]{3,200})$", re.MULTILINE)
# where a problem-list item stops being a diagnosis and becomes plan text
_ITEM_CUT_RE = re.compile(
    r"\s(?:-|--|–)\s|:\s|;|\.\s|\.$|,\s(?:continue|start|stop|increase|decrease|recheck|follow|monitor|plan|will|refer|check|on)\b",
    re.IGNORECASE,
)
_LEADING_TRIGGER = re.compile(
    r"^(?:no evidence of|no signs of|negative for|no|denies|possible|probable|likely|suspected|rule out|r/o|"
    r"history of|h/o|hx of|status post|s/p|questionable|presumed|family history of|concern for|"
    r"consistent with|evaluate for)\s+",
    re.IGNORECASE,
)
_COMPOUND_JOIN = re.compile(
    r"^\s*(?:,\s*)?(?:with|without|w/o?|due to|secondary to|associated with|complicated by|in|of|from)\b",
    re.IGNORECASE,
)
_PLAN_VERBS = re.compile(
    r"^(continue|start|stop|increase|decrease|recheck|follow|monitor|refer|check|return|schedule|order|obtain|counsel|discussed|advised|f/u)\b",
    re.IGNORECASE,
)


_INJURY_HEAD = re.compile(
    r"\b(?:fracture|fx|laceration|sprain|strain of|contusion|dislocation|subluxation|abrasion|puncture wound|"
    r"avulsion|crush injury|animal bite|dog bite|burn of)\b",
    re.IGNORECASE,
)
# procedure / plan / historical phrasing that is not the injury diagnosis itself
_INJURY_NOISE = re.compile(
    r"\b(?:repair|reduction|splint|suture|history of|s/p|status post|rule out|risk of|"
    r"no (?:evidence of )?(?:fracture|dislocation)|without (?:evidence of )?fracture|"
    r"negative for fracture)\b",
    re.IGNORECASE,
)
_DIAGNOSTIC_SECTIONS = {
    "assessment",
    "discharge_diagnoses",
    "postoperative_diagnosis",
    "preoperative_diagnosis",
}


class RuleBasedExtractor:
    name = "rules"

    def extract(self, text: str, sections: list[Section]) -> list[Entity]:
        entities = self._lexicon_entities(text, sections)
        entities = self._injury_phrases(text, sections, entities)
        entities = self._merge_problem_list(text, sections, entities)
        entities.sort(key=lambda e: (e.start, -e.end))
        return entities

    def _lexicon_entities(self, text: str, sections: list[Section]) -> list[Entity]:
        lex = load_lexicon()
        long_re, short_re = _lexicon_regexes()
        found: list[Entity] = []
        for rx in (long_re, short_re):
            for m in rx.finditer(text):
                key = re.sub(r"\s+", " ", m.group(1).lower())
                if key not in lex:
                    continue
                category, normalized = lex[key]
                found.append(
                    Entity(
                        text=m.group(1),
                        category=category,
                        start=m.start(1),
                        end=m.end(1),
                        normalized=normalized,
                        section=section_at(sections, m.start(1)),
                        source="rules",
                    )
                )
        # longest match wins on overlaps
        found.sort(key=lambda e: (-(e.end - e.start), e.start))
        kept: list[Entity] = []
        for e in found:
            if not any(e.overlaps(k) for k in kept):
                kept.append(e)
        return kept

    def _injury_phrases(self, text: str, sections: list[Section], entities: list[Entity]) -> list[Entity]:
        """Injuries are coded from the whole documented phrase (type, site, side, open/closed, displacement,
        foreign body, episode of care): "Closed displaced Colles fracture, right radius, initial encounter".
        A lone "fracture" loses everything ICD-10-CM chapter 19 needs, so the phrase replaces it.
        Diagnostic-section phrases are preferred; other sections are used only when the note has none."""
        found: list[Entity] = []
        for m in _INJURY_HEAD.finditer(text):
            start = max(text.rfind(ch, 0, m.start()) for ch in ".\n;:") + 1
            start = max(start, m.start() - 90)
            ends = [i for i in (text.find(ch, m.end()) for ch in ".\n;") if i != -1]
            end = min(ends) if ends else len(text)
            end = min(end, m.end() + 160)
            raw = text[start:end]
            lead = len(raw) - len(raw.lstrip(" \t-*0123456789)."))
            start += lead
            phrase = text[start:end].rstrip(" ,")
            end = start + len(phrase)
            if len(phrase) < 6 or _INJURY_NOISE.search(phrase):
                continue
            found.append(
                Entity(
                    text=phrase,
                    category="condition",
                    start=start,
                    end=end,
                    normalized=re.sub(r"\s+", " ", phrase.lower()),
                    section=section_at(sections, start),
                    source="rules-injury",
                )
            )
        if not found:
            return entities
        diagnostic = [e for e in found if e.section in _DIAGNOSTIC_SECTIONS]
        found = diagnostic or found
        kept = [
            e
            for e in entities
            if not any(e.overlaps(f) for f in found) or e.category not in ("condition", "finding", "symptom")
        ]
        kept.extend(found)
        return kept

    def _merge_problem_list(self, text: str, sections: list[Section], entities: list[Entity]) -> list[Entity]:
        out = list(entities)
        for sec in sections:
            if sec.name not in DIAGNOSTIC_SECTIONS or sec.name in ("hpi", "chief_complaint"):
                continue
            body = text[sec.start : sec.end]
            for m in _LIST_ITEM_RE.finditer(body):
                item = m.group("item")
                cut = _ITEM_CUT_RE.search(item)
                phrase = item[: cut.start()] if cut else item
                phrase = phrase.strip(" .,;")
                if len(phrase) < 3 or _PLAN_VERBS.match(phrase):
                    continue
                start = sec.start + m.start("item")
                lead = _LEADING_TRIGGER.match(phrase)
                if lead:  # keep the trigger outside the span so ConText can see it
                    start += lead.end()
                    phrase = phrase[lead.end() :]
                end = start + len(phrase)
                inner = [e for e in out if e.start >= start and e.end <= end]
                if any(e.category == "medication" or e.category == "procedure" for e in inner) and not any(
                    e.category in ("condition", "symptom", "finding") for e in inner
                ):
                    continue
                clinical = sorted(
                    (e for e in inner if e.category in ("condition", "symptom", "finding")),
                    key=lambda e: e.start,
                )
                absorbed = clinical
                if len(clinical) > 1:
                    joins = [text[a.end : b.start] for a, b in zip(clinical, clinical[1:], strict=False)]
                    if not all(_COMPOUND_JOIN.match(j) for j in joins):
                        continue  # "Hypertension and hyperlipidemia": keep the separate lexicon hits
                    # Combination diagnosis ("X with Y", "X due to Y"): the phrase replaces the head
                    # concept and absorbs linked symptoms/findings; other conditions stay separate.
                    absorbed = [clinical[0]] + [
                        e for e in clinical[1:] if e.category in ("symptom", "finding")
                    ]
                main = clinical[0] if clinical else None
                if inner and len(inner) == 1 and inner[0].start == start and inner[0].end == end:
                    continue  # the lexicon already captured exactly this
                span = Entity("", "", start, end)
                if any(e.overlaps(span) and e not in inner for e in out):
                    continue  # partially overlaps something else; keep lexicon hits as they are
                for e in absorbed:
                    out.remove(e)
                out.append(
                    Entity(
                        text=text[start:end],
                        category=main.category if main else "condition",
                        start=start,
                        end=end,
                        normalized=main.normalized if main else text[start:end].lower(),
                        section=sec.name,
                        source="rules-list",
                    )
                )
        return out
