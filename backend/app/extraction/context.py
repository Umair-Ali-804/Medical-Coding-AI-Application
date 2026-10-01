"""ConText / NegEx assertion detection (Chapman et al., Harkema et al.).

For every target span we determine, within its sentence:
  * negated       - "denies chest pain", "no evidence of pneumonia", "ruled out"
  * uncertain     - "possible pneumonia", "rule out MI", "consistent with" (outpatient: not coded)
  * historical    - "history of", "s/p", "previous"
  * family        - "mother has diabetes", "family history of", or Family History section
  * hypothetical  - "return if fever develops"

Triggers have a direction (forward / backward / bidirectional) and their scope
ends at a termination term ("but", "however", "which", ...), at another
trigger's scope boundary, or at the sentence end. Pseudo-triggers ("no
increase", "not only", "gram negative") are masked out first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.extraction.types import Entity

FWD, BWD, BI = "forward", "backward", "bidirectional"


@dataclass(frozen=True)
class Rule:
    phrase: str
    category: str  # negated | uncertain | historical | family | hypothetical
    direction: str
    max_scope: int = 12  # tokens


RULES: list[Rule] = [
    # --- negation (forward) ---
    *(
        Rule(p, "negated", FWD)
        for p in (
            "no",
            "not",
            "denies",
            "denied",
            "denying",
            "without",
            "negative for",
            "no evidence of",
            "no signs of",
            "no sign of",
            "no history of",
            "no hx of",
            "free of",
            "absence of",
            "absent",
            "never had",
            "never",
            "rules out",
            "ruled out for",
            "no complaints of",
            "not have",
            "did not have",
            "does not have",
            "no new",
            "resolved",
            "negative",
            "nor",
            "fails to reveal",
            "no findings of",
            "no suspicion of",
            "declines",
            "without evidence of",
            "no further",
            "not demonstrate",
            "not exhibit",
            "not show",
            "no acute",
            "neither",
        )
    ),
    # --- negation (backward) ---
    *(
        Rule(p, "negated", BWD, 6)
        for p in (
            "is ruled out",
            "was ruled out",
            "has been ruled out",
            "have been ruled out",
            "ruled out",
            "was negative",
            "is negative",
            "were negative",
            "unlikely",
            "is absent",
            "not present",
            "not seen",
            "not identified",
            "has resolved",
            "have resolved",
            "resolved",
            "free",
        )
    ),
    # --- uncertainty (per ICD-10-CM outpatient guideline IV.H) ---
    *(
        Rule(p, "uncertain", FWD)
        for p in (
            "possible",
            "possibly",
            "probable",
            "probably",
            "likely",
            "suspected",
            "suspect",
            "suspicious for",
            "rule out",
            "r/o",
            "questionable",
            "question of",
            "concern for",
            "concerning for",
            "cannot exclude",
            "cannot rule out",
            "can not rule out",
            "may have",
            "might have",
            "may be",
            "consistent with",
            "compatible with",
            "suggestive of",
            "working diagnosis",
            "presumed",
            "differential diagnosis includes",
            "differential includes",
            "differential",
            "evaluate for",
            "evaluation for",
            "to rule out",
            "?",
        )
    ),
    *(
        Rule(p, "uncertain", BWD, 6)
        for p in (
            "is suspected",
            "was suspected",
            "is possible",
            "is likely",
            "is probable",
            "not excluded",
            "not ruled out",
            "or not",
        )
    ),
    *(Rule(p, "uncertain", BI, 4) for p in ("versus", "vs", "vs.")),
    # --- historical ---
    *(
        Rule(p, "historical", FWD)
        for p in (
            "history of",
            "hx of",
            "h/o",
            "past history of",
            "previous",
            "previously",
            "prior",
            "status post",
            "s/p",
            "remote history of",
            "in remission",
        )
    ),
    # --- family / other experiencer ---
    *(
        Rule(p, "family", FWD)
        for p in (
            "family history of",
            "family hx of",
            "fhx of",
            "fh of",
            "mother with",
            "father with",
            "brother with",
            "sister with",
            "mother has",
            "father has",
            "mother had",
            "father had",
            "brother has",
            "sister has",
            "grandmother with",
            "grandfather with",
            "mother's",
            "father's",
            "sibling with",
            "parents with",
            "daughter with",
            "son with",
        )
    ),
    *(
        Rule(p, "family", BWD, 8)
        for p in (
            "in his mother",
            "in her mother",
            "in his father",
            "in her father",
            "in the family",
            "in mother",
            "in father",
            "in sibling",
            "runs in the family",
        )
    ),
    # --- hypothetical ---
    *(
        Rule(p, "hypothetical", FWD, 10)
        for p in (
            "if",
            "return if",
            "return for",
            "should he develop",
            "should she develop",
            "in case of",
            "call if",
            "watch for",
            "monitor for",
            "precautions for",
            "return precautions",
            "risk of",
            "at risk for",
            "prevent",
            "prevention of",
            "prophylaxis",
            "screening for",
            "screen for",
        )
    ),
    *(
        Rule(p, "hypothetical", BWD, 3)
        for p in (
            "prevention",
            "prophylaxis",
            "risk reduction",
            "screening",
            "precautions",
        )
    ),
]

PSEUDO = (
    "no increase",
    "no change",
    "no significant change",
    "not only",
    "not necessarily",
    "gram negative",
    "no further workup",
    "not certain if",
    "not certain whether",
    "without difficulty",
    "not cause",
    "not drain",
    "no suspicious change",
    "no interval change",
    "not ruled out",
    "cannot be ruled out",
    "without any further",
    "history and physical",
    "history of present illness",
    "history of the present illness",
)

TERMINATION = (
    "but",
    "however",
    "although",
    "though",
    "which",
    "aside from",
    "apart from",
    "except",
    "cause of",
    "causes of",
    "source of",
    "reason for",
    "secondary to",
    "due to",
    "presents with",
    "presenting with",
    "complains of",
    "c/o",
    "reports",
    "has",
    "with",
    "and has",
    "patient has",
    "positive for",
    "notable for",
    "significant for",
    "now",
    "continue",
    "continued",
    "started",
    "start",
    "plan",
    "recommend",
)
_NON_PREFIX = re.compile(r"(?<![A-Za-z])non[-\s]?$", re.IGNORECASE)
_CLAUSE_BREAK_RE = re.compile(r"\s[-\u2013]\s|:")

_TOKEN_RE = re.compile(r"[A-Za-z0-9/'?]+(?:[.-][A-Za-z0-9]+)*|[;:,.]")
# sentence boundaries: . ! ? ; newline(s), and bullet/numbered list starts
# A newline ends a sentence unless the next line continues in lowercase (hard-wrapped PDF/EHR text:
# "no prior diagnosis\nof hypertension" is ONE sentence, so the negation reaches "hypertension").
_SENT_RE = re.compile(r"(?<=[.!;])\s+|\n+(?![ \t]*[a-z])|(?<=\?)\s+")

LATERALITY_RE = re.compile(r"\b(bilateral|bilaterally|both|left|right|l\.|r\.)\b", re.IGNORECASE)
SEVERITY_RE = re.compile(
    r"\b(mild|moderate|severe|uncontrolled|poorly controlled|well controlled|controlled|"
    r"intractable|stage\s*(?:[1-5][ab]?|i{1,3}v?|v)|grade\s*[1-4]|acute on chronic|"
    r"with exacerbation|exacerbation|in remission|morbid)\b",
    re.IGNORECASE,
)
TEMPORAL_RE = re.compile(
    r"\b(acute|chronic|subacute|recurrent|intermittent|persistent|new onset|new-onset|"
    r"initial encounter|subsequent encounter|sequela)\b",
    re.IGNORECASE,
)


def _compile(phrases: tuple[str, ...] | list[str]) -> re.Pattern[str]:
    alts = sorted({re.escape(p) for p in phrases}, key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(alts) + r")(?![A-Za-z0-9])", re.IGNORECASE)


_PSEUDO_RE = _compile(PSEUDO)
_TERM_RE = _compile(TERMINATION)
_RULE_RES = {r: _compile([r.phrase]) for r in RULES}


_ABBREV_RE = re.compile(
    r"(?<![A-Za-z])(vs|dr|mr|mrs|ms|e\.g|i\.e|approx|no|pt|hx|dx|st|jr|sr|fig|etc|wt|ht|temp)\.",
    re.IGNORECASE,
)


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Split into sentence-like spans; abbreviations ("vs.", "Dr.") do not end sentences."""
    protected = _ABBREV_RE.sub(lambda m: m.group(0)[:-1] + "\x01", text)
    spans: list[tuple[int, int]] = []
    pos = 0
    for m in _SENT_RE.finditer(protected):
        if m.start() > pos:
            spans.append((pos, m.start()))
        pos = m.end()
    if pos < len(text):
        spans.append((pos, len(text)))
    return spans


def sentence_bounds(text: str, offset: int, spans: list[tuple[int, int]] | None = None) -> tuple[int, int]:
    for start, end in spans if spans is not None else sentence_spans(text):
        if start <= offset < end:
            return start, end
    return offset, len(text)


def _mask(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: "\x00" * len(m.group(0)), text)


def _tokens_between(text: str) -> int:
    return len(_TOKEN_RE.findall(text))


class ConTextAnalyzer:
    """Deterministic assertion classifier applied to every entity span."""

    def __init__(self, family_section_names: set[str] | None = None):
        self.family_sections = family_section_names or {"family_history"}

    def analyze(self, text: str, entities: list[Entity]) -> list[Entity]:
        spans = sentence_spans(text)
        for ent in entities:
            self._analyze_one(text, ent, spans)
        return entities

    def _analyze_one(self, text: str, ent: Entity, spans: list[tuple[int, int]]) -> None:
        s_start, s_end = sentence_bounds(text, ent.start, spans)
        s_end = max(s_end, ent.end)
        sentence = text[s_start:s_end]
        rel_start, rel_end = ent.start - s_start, ent.end - s_start
        masked = _mask(sentence, _PSEUDO_RE)
        # don't let the entity's own words act as triggers ("negative" in "gram-negative sepsis")
        masked = masked[:rel_start] + "\x00" * (rel_end - rel_start) + masked[rel_end:]

        found: dict[str, list[str]] = {}
        for rule, rx in _RULE_RES.items():
            for m in rx.finditer(masked):
                if rule.direction in (FWD, BI) and m.end() <= rel_start:
                    between = masked[m.end() : rel_start]
                    if self._in_scope(between, rule.max_scope):
                        found.setdefault(rule.category, []).append(rule.phrase)
                if rule.direction in (BWD, BI) and m.start() >= rel_end:
                    between = masked[rel_end : m.start()]
                    if self._in_scope(between, rule.max_scope):
                        found.setdefault(rule.category, []).append(rule.phrase)

        # "non-smoker", "non smoker", "nondiabetic": the prefix negates the concept itself
        if _NON_PREFIX.search(text[max(0, ent.start - 5) : ent.start]):
            found.setdefault("negated", []).append("non-")
        ent.negated = ent.negated or "negated" in found
        ent.uncertain = ent.uncertain or "uncertain" in found
        ent.historical = ent.historical or "historical" in found
        ent.family = ent.family or "family" in found or (ent.section in self.family_sections)
        ent.hypothetical = ent.hypothetical or "hypothetical" in found
        ent.triggers = sorted({p for phrases in found.values() for p in phrases})

        window = text[max(s_start, ent.start - 40) : min(s_end, ent.end + 40)]
        own = text[ent.start : ent.end]
        lat = LATERALITY_RE.search(own) or LATERALITY_RE.search(window)
        if lat and not ent.laterality:
            val = lat.group(1).lower().rstrip(".")
            ent.laterality = {"l": "left", "r": "right", "both": "bilateral", "bilaterally": "bilateral"}.get(
                val, val
            )
        sev = SEVERITY_RE.search(own) or SEVERITY_RE.search(window)
        if sev and not ent.severity:
            ent.severity = re.sub(r"\s+", " ", sev.group(1).lower())
        tmp = TEMPORAL_RE.search(own) or TEMPORAL_RE.search(window)
        if tmp and not ent.temporal:
            ent.temporal = tmp.group(1).lower()

    @staticmethod
    def _in_scope(between: str, max_scope: int) -> bool:
        if _TERM_RE.search(between) or _CLAUSE_BREAK_RE.search(between):
            return False
        return _tokens_between(between) <= max_scope
