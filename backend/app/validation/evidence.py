"""Locate LLM evidence quotes in the source document (anti-hallucination check)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.extraction.context import sentence_spans

_WS = re.compile(r"\s+")


@dataclass
class EvidenceMatch:
    quote: str
    start: int | None
    end: int | None
    score: float  # 1.0 verbatim, ~0.97 case/whitespace-normalized, else fuzzy ratio


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip().lower()


def match_quote(text: str, quote: str, spans: list[tuple[int, int]] | None = None) -> EvidenceMatch:
    q = quote.strip().strip('"').strip("'").strip()
    if not q:
        return EvidenceMatch(quote, None, None, 0.0)
    i = text.find(q)
    if i != -1:
        return EvidenceMatch(q, i, i + len(q), 1.0)
    pattern = r"\s+".join(re.escape(p) for p in q.split())
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return EvidenceMatch(q, m.start(), m.end(), 0.97)
    # fuzzy: best window within any sentence
    nq = _norm(q)
    best = (0.0, None, None)
    for s, e in spans if spans is not None else sentence_spans(text):
        sent = text[s:e]
        ns = _norm(sent)
        if not ns:
            continue
        if len(ns) <= len(nq) * 1.5:
            r = SequenceMatcher(None, nq, ns).ratio()
            if r > best[0]:
                best = (r, s, e)
            continue
        words = [(w.start(), w.end()) for w in re.finditer(r"\S+", sent)]
        n_words = max(1, len(q.split()))
        for wi in range(0, max(1, len(words) - n_words + 1)):
            ws, we = words[wi][0], words[min(len(words) - 1, wi + n_words - 1)][1]
            r = SequenceMatcher(None, nq, _norm(sent[ws:we])).ratio()
            if r > best[0]:
                best = (r, s + ws, s + we)
    score = round(best[0] * 0.95, 3)  # fuzzy never scores as high as verbatim
    if score < 0.6:
        return EvidenceMatch(q, None, None, score)
    return EvidenceMatch(q, best[1], best[2], score)


def match_all(text: str, quotes: list[str]) -> list[EvidenceMatch]:
    spans = sentence_spans(text)
    return [match_quote(text, q, spans) for q in quotes if q and q.strip()]
