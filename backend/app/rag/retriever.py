"""Hybrid retrieval: dense (Qdrant) + lexical (BM25), fused with Reciprocal Rank Fusion.

Returns hydrated KB records (description + instructional notes) so the LLM
reasons over official reference text rather than memorized code lists.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.knowledge.repository import CodeInfo, KBSnapshot, get_snapshot
from app.models import KnowledgeBaseVersion
from app.rag.embeddings import get_embedder
from app.rag.lexical import get_bm25, tokenize
from app.rag.vector_store import collection_name, get_vector_store

log = logging.getLogger(__name__)


@dataclass
class RetrievedCode:
    info: CodeInfo
    score: float  # 0..1 blended relevance
    rank: int  # 1-based
    rrf: float = 0.0
    dense_score: float | None = None
    lexical_score: float | None = None
    coverage: float = 0.0  # fraction of query terms found in the code text
    sources: list[str] = field(default_factory=list)

    @property
    def code(self) -> str:
        return self.info.code


class HybridRetriever:
    def __init__(self, db: Session, system: str = "ICD-10-CM", snapshot: KBSnapshot | None = None):
        self.settings = get_settings()
        self.system = system
        self.snapshot = snapshot or get_snapshot(db, system)
        if self.snapshot is None:
            raise RuntimeError(
                f"No active knowledge base for {system}. Run `python -m app.cli kb load-icd10cm`."
            )
        self.bm25 = get_bm25(self.snapshot)
        self.embedder = get_embedder()
        self.store = get_vector_store()
        self.collection = collection_name(system, self.snapshot.version, self.embedder.name)
        self.dense_available = self._dense_ready()
        self.version = self.settings.retrieval_version + ("" if self.dense_available else ":lexical-only")

    def _dense_ready(self) -> bool:
        try:
            return self.store.count(self.collection) > 0
        except Exception as exc:  # noqa: BLE001
            log.warning("vector_store_unavailable", extra={"error": str(exc)[:200]})
            return False

    def retrieve(self, query: str, k: int | None = None) -> list[RetrievedCode]:
        return self.retrieve_many([query], k)[0]

    def retrieve_many(self, queries: list[str], k: int | None = None) -> list[list[RetrievedCode]]:
        k = k or self.settings.retrieval_top_k
        dense_vecs: list[list[float] | None] = [None] * len(queries)
        if self.dense_available and queries:
            try:
                dense_vecs = [self.embedder.embed_query(q) for q in queries]
            except Exception as exc:  # noqa: BLE001
                log.warning("embedding_failed", extra={"error": str(exc)[:200]})
        return [self._retrieve_one(q, v, k) for q, v in zip(queries, dense_vecs, strict=True)]

    def _retrieve_one(self, query: str, vec: list[float] | None, k: int) -> list[RetrievedCode]:
        s = self.settings
        rrf: dict[str, float] = defaultdict(float)
        dense: dict[str, float] = {}
        lexical: dict[str, float] = {}
        sources: dict[str, list[str]] = defaultdict(list)

        if vec is not None:
            try:
                for rank, hit in enumerate(
                    self.store.search(self.collection, vec, s.retrieval_dense_k), start=1
                ):
                    rrf[hit.code] += 1.0 / (s.retrieval_rrf_k + rank)
                    dense[hit.code] = hit.score
                    sources[hit.code].append("dense")
            except Exception as exc:  # noqa: BLE001
                log.warning("dense_search_failed", extra={"error": str(exc)[:200]})

        for rank, (code, score) in enumerate(self.bm25.search(query, s.retrieval_lexical_k), start=1):
            rrf[code] += 1.0 / (s.retrieval_rrf_k + rank)
            lexical[code] = score
            sources[code].append("lexical")

        q_terms = set(tokenize(query, expand=True))
        max_rrf = 2.0 / (s.retrieval_rrf_k + 1)
        results: list[RetrievedCode] = []
        for code, fused in rrf.items():
            info = self.snapshot.codes.get(code)
            if info is None:
                continue
            doc_terms = set(tokenize(info.search_text()))
            desc_terms = set(tokenize(info.description))
            coverage = len(q_terms & doc_terms) / len(q_terms) if q_terms else 0.0
            # penalize descriptions carrying many concepts the query never mentioned
            precision = len(q_terms & desc_terms) / len(desc_terms) if desc_terms else 0.0
            score = 0.45 * min(1.0, fused / max_rrf * 1.6) + 0.35 * coverage + 0.20 * precision
            score += (
                _specificity_prior(info.description, query, info.code)
                if self.system == "ICD-10-CM"
                else _procedure_prior(info.description, query)
            )
            results.append(
                RetrievedCode(
                    info=info,
                    score=round(max(0.0, min(score, 1.0)), 4),
                    rank=0,
                    rrf=fused,
                    dense_score=dense.get(code),
                    lexical_score=lexical.get(code),
                    coverage=round(coverage, 4),
                    sources=sources[code],
                )
            )
        results.sort(key=lambda r: (-r.score, r.info.code))
        for i, r in enumerate(results[:k], start=1):
            r.rank = i
        return results[:k]


_OTHER_RE = re.compile(r"^other\b|\bother specified\b|,\s*other\b|\bother [a-z]+ of\b", re.IGNORECASE)
_NOS_RE = re.compile(
    r"\bunspecified\b|\bwithout complications\b|\bNOS\b|\bnot otherwise specified\b", re.IGNORECASE
)


_COMPLICATION_WORDS = (
    r"(obstruction|hemorrhage|bleeding|perforation|gangrene|abscess|coma|intractab\w*|status migrainosus)"
)
_WITH_COMPLICATION = re.compile(rf"(?<!out)\bwith {_COMPLICATION_WORDS}", re.IGNORECASE)
_WITHOUT_COMPLICATION = re.compile(rf"\bwithout {_COMPLICATION_WORDS}", re.IGNORECASE)
_PREGNANCY_Q = re.compile(
    r"pregnan|gestation|obstetric|postpartum|puerper|deliver|labor|trimester|antepartum|maternal",
    re.IGNORECASE,
)
_NEWBORN_Q = re.compile(r"newborn|neonat|perinatal|infant of|birth", re.IGNORECASE)
_LATE_EPISODE = re.compile(r"\b(sequela|subsequent encounter)\b", re.IGNORECASE)


def _specificity_prior(description: str, query: str, code: str = "") -> float:
    """Coding-convention priors applied on top of relevance:
    * unspecified/NOS beats other/NEC when documentation does not name a subtype
    * pregnancy (O) and perinatal (P) chapters need pregnancy/newborn context
    * 7th character: 'sequela'/'subsequent encounter' need explicit documentation"""
    prior = 0.0
    if code[:1] == "O" and not _PREGNANCY_Q.search(query):
        prior -= 0.25
    if code[:1] == "P" and not _NEWBORN_Q.search(query):
        prior -= 0.25
    if _LATE_EPISODE.search(description) and not _LATE_EPISODE.search(query):
        prior -= 0.06
    # ICD index convention: the main term leads the title ("Sepsis, unspecified organism")
    head = re.split(r"[,(]| with | due to ", query.lower(), maxsplit=1)[0].strip()
    if head and len(head) >= 4 and description.lower().startswith(head):
        prior += 0.05
    # "with obstruction/hemorrhage/perforation..." needs documentation; default to the "without" sibling
    q = query.lower()
    for m in _WITH_COMPLICATION.finditer(description):
        if m.group(1).lower()[:6] not in q:
            prior -= 0.08
            break
    for m in _WITHOUT_COMPLICATION.finditer(description):
        if m.group(1).lower()[:6] not in q:
            prior += 0.03
            break
    if re.search(r"\b(other|specified)\b", query, re.IGNORECASE):
        return prior
    if _OTHER_RE.search(description):
        prior -= 0.12
    elif _NOS_RE.search(description):
        prior += 0.04
    return prior


def _procedure_prior(description: str, query: str) -> float:
    """CPT/HCPCS/PCS: unlisted codes are a last resort; add-on codes need their primary procedure."""
    d = description.lower()
    prior = 0.0
    if "unlisted" in d and "unlisted" not in query.lower():
        prior -= 0.15
    if "list separately in addition" in d or d.startswith("each additional"):
        prior -= 0.08
    return prior


def index_knowledge_base(db: Session, system: str = "ICD-10-CM", batch_size: int = 512, progress=None) -> int:  # noqa: ANN001
    """Embed every billable code of the active KB version into the vector store."""
    snapshot = get_snapshot(db, system)
    if snapshot is None:
        raise RuntimeError(f"No active knowledge base for {system}")
    embedder = get_embedder()
    store = get_vector_store()
    name = collection_name(system, snapshot.version, embedder.name)
    store.ensure_collection(name, embedder.dim)
    infos = [i for i in snapshot.codes.values() if i.billable or i.attributes.get("range")]
    done = 0
    for start in range(0, len(infos), batch_size):
        batch = infos[start : start + batch_size]
        texts = [f"{i.code} {i.search_text()}" for i in batch]
        vecs = embedder.embed_documents(texts)
        store.upsert(
            name,
            ids=[f"{system}:{snapshot.version}:{i.code}" for i in batch],
            vectors=vecs,
            payloads=[
                {
                    "code": i.code,
                    "description": i.description,
                    "billable": i.billable,
                    "chapter": i.chapter or "",
                    "category": i.category or "",
                }
                for i in batch
            ],
        )
        done += len(batch)
        if progress:
            progress(done, len(infos))
    flush = getattr(store, "flush", None)
    if flush:
        flush(name)
    kbv = db.query(KnowledgeBaseVersion).filter_by(code_system=system, version=snapshot.version).one_or_none()
    if kbv:
        kbv.indexed = True
    return done
