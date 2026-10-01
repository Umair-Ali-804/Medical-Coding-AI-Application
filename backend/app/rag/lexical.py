"""In-process BM25 index over code descriptions + inclusion terms.

Dense retrieval misses exact clinical wording ("stage 3a", "without complications",
"initial encounter"); BM25 catches it. Built lazily from the KB snapshot.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from threading import Lock

from app.knowledge.repository import KBSnapshot

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "of",
    "the",
    "and",
    "or",
    "in",
    "to",
    "a",
    "an",
    "for",
    "on",
    "by",
    "due",
    "other",
    "unspecified",
    "specified",
    "as",
    "at",
    "elsewhere",
    "classified",
    "nos",
    "type",
}
# Clinical synonyms/abbreviations expanded at query time.
_SYNONYMS = {
    "htn": "hypertension",
    "dm": "diabetes mellitus",
    "t2dm": "type 2 diabetes mellitus",
    "t1dm": "type 1 diabetes mellitus",
    "ckd": "chronic kidney disease",
    "copd": "chronic obstructive pulmonary disease",
    "chf": "heart failure",
    "cad": "atherosclerotic heart disease coronary artery",
    "afib": "atrial fibrillation",
    "uti": "urinary tract infection",
    "gerd": "gastro esophageal reflux disease",
    "mi": "myocardial infarction",
    "osa": "obstructive sleep apnea",
    "uri": "upper respiratory infection",
    "dvt": "deep vein thrombosis",
    "pe": "pulmonary embolism",
    "bph": "benign prostatic hyperplasia",
    "aki": "acute kidney failure",
    "esrd": "end stage renal disease",
    "mdd": "major depressive disorder",
    "gad": "generalized anxiety disorder",
    "sob": "shortness of breath",
    "hld": "hyperlipidemia",
    "oa": "osteoarthritis",
    "ra": "rheumatoid arthritis",
    "reflux": "reflux gastro esophageal",
    "smoker": "nicotine dependence cigarettes",
    "coronary": "atherosclerotic coronary",
    "cholelithiasis": "calculus gallbladder",
    "gallstones": "calculus gallbladder",
    "gallstone": "calculus gallbladder",
    "choledocholithiasis": "calculus bile duct",
    "nephrolithiasis": "calculus kidney",
    "urolithiasis": "calculus urinary",
    "ureterolithiasis": "calculus ureter",
    "iii": "3",
    "ii": "2",
    "iv": "4",
}
# Plural/variant normalization
_NORM = {
    "kidneys": "kidney",
    "ulcers": "ulcer",
    "stones": "calculus",
    "stone": "calculus",
    "hypertensive": "hypertensive",
    "diabetic": "diabetes",
    "asthmatic": "asthma",
    "gastroesophageal": "gastro esophageal",
    "cigarette": "cigarettes",
}


def tokenize(text: str, expand: bool = False) -> list[str]:
    out: list[str] = []
    for tok in _TOKEN.findall(text.lower()):
        tok = _NORM.get(tok, tok)
        if expand and tok in _SYNONYMS:
            out.extend(_TOKEN.findall(_SYNONYMS[tok]))
        for t in tok.split():
            if t not in _STOP:
                out.append(t)
    return out


class BM25Index:
    def __init__(self, snapshot: KBSnapshot, billable_only: bool = True, k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.codes: list[str] = []
        self.doc_len: list[int] = []
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for info in snapshot.codes.values():
            if billable_only and not (info.billable or info.attributes.get("range")):
                continue
            toks = tokenize(info.search_text())
            idx = len(self.codes)
            self.codes.append(info.code)
            self.doc_len.append(len(toks))
            for term, tf in Counter(toks).items():
                self.postings[term].append((idx, tf))
        self.n = len(self.codes)
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 1.0
        self.idf = {
            t: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()
        }

    def search(self, query: str, limit: int = 25) -> list[tuple[str, float]]:
        scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query, expand=True)):
            plist = self.postings.get(term)
            if not plist:
                continue
            idf = self.idf[term]
            for idx, tf in plist:
                dl = self.doc_len[idx]
                scores[idx] += (
                    idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
                )
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
        return [(self.codes[i], s) for i, s in top]


_indexes: dict[tuple[str, str], BM25Index] = {}
_lock = Lock()


def get_bm25(snapshot: KBSnapshot) -> BM25Index:
    key = (snapshot.system, snapshot.version)
    idx = _indexes.get(key)
    if idx is None:
        with _lock:
            idx = _indexes.get(key)
            if idx is None:
                idx = BM25Index(snapshot)
                _indexes[key] = idx
    return idx


def clear_bm25_cache() -> None:
    with _lock:
        _indexes.clear()
