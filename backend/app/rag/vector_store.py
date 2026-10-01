"""Vector store abstraction over Qdrant (server or embedded) with an in-memory fallback."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np

from app.core.config import get_settings

log = logging.getLogger(__name__)
_NS = uuid.UUID("7b0f7d1e-4c4b-4a53-9b0c-0d6f0b8c2f11")


@dataclass
class VectorHit:
    code: str
    score: float
    payload: dict


class VectorStore(Protocol):
    def ensure_collection(self, name: str, dim: int) -> None: ...
    def upsert(
        self, name: str, ids: Sequence[str], vectors: Sequence[Sequence[float]], payloads: Sequence[dict]
    ) -> None: ...
    def search(
        self, name: str, vector: Sequence[float], limit: int, filters: dict | None = None
    ) -> list[VectorHit]: ...
    def count(self, name: str) -> int: ...
    def healthy(self) -> bool: ...


def collection_name(system: str, version: str, embedder_name: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in f"{system}_{version}_{embedder_name}").lower()
    return f"{get_settings().qdrant_collection_prefix}_{safe}"


class QdrantStore:
    def __init__(self, url: str | None, api_key: str | None, path: str | None):
        from qdrant_client import QdrantClient

        if path:
            self.client = QdrantClient(path=path)
        elif url == ":memory:":
            self.client = QdrantClient(location=":memory:")
        else:
            self.client = QdrantClient(url=url, api_key=api_key, timeout=30)

    def ensure_collection(self, name: str, dim: int) -> None:
        from qdrant_client.http import models as qm

        if not self.client.collection_exists(name):
            self.client.create_collection(
                name,
                vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
                on_disk_payload=True,
            )
            for field in ("billable", "chapter", "category"):
                try:
                    schema = (
                        qm.PayloadSchemaType.BOOL if field == "billable" else qm.PayloadSchemaType.KEYWORD
                    )
                    self.client.create_payload_index(name, field_name=field, field_schema=schema)
                except Exception as exc:  # noqa: BLE001 - embedded mode doesn't support payload indexes
                    log.debug("payload_index_skipped", extra={"field": field, "error": str(exc)[:100]})

    def upsert(
        self, name: str, ids: Sequence[str], vectors: Sequence[Sequence[float]], payloads: Sequence[dict]
    ) -> None:
        from qdrant_client.http import models as qm

        points = [
            qm.PointStruct(id=str(uuid.uuid5(_NS, i)), vector=list(v), payload=p)
            for i, v, p in zip(ids, vectors, payloads, strict=True)
        ]
        self.client.upsert(name, points=points, wait=True)

    def search(
        self, name: str, vector: Sequence[float], limit: int, filters: dict | None = None
    ) -> list[VectorHit]:
        from qdrant_client.http import models as qm

        flt = None
        if filters:
            flt = qm.Filter(
                must=[qm.FieldCondition(key=k, match=qm.MatchValue(value=v)) for k, v in filters.items()]
            )
        res = self.client.query_points(
            name, query=list(vector), limit=limit, query_filter=flt, with_payload=True
        )
        return [
            VectorHit(code=p.payload["code"], score=float(p.score), payload=p.payload) for p in res.points
        ]

    def count(self, name: str) -> int:
        if not self.client.collection_exists(name):
            return 0
        return int(self.client.count(name, exact=True).count)

    def healthy(self) -> bool:
        try:
            self.client.get_collections()
            return True
        except Exception:  # noqa: BLE001
            return False


class MemoryStore:
    """Brute-force cosine search. For tests and tiny KBs."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[list[str], np.ndarray | None, list[dict], list[list[float]]]] = {}

    def ensure_collection(self, name: str, dim: int) -> None:
        self._data.setdefault(name, ([], None, [], []))

    def upsert(
        self, name: str, ids: Sequence[str], vectors: Sequence[Sequence[float]], payloads: Sequence[dict]
    ) -> None:
        keys, _, pls, raw = self._data.setdefault(name, ([], None, [], []))
        index = {k: n for n, k in enumerate(keys)}
        for i, v, p in zip(ids, vectors, payloads, strict=True):
            if i in index:
                raw[index[i]] = list(v)
                pls[index[i]] = p
            else:
                keys.append(i)
                raw.append(list(v))
                pls.append(p)
        mat = np.asarray(raw, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1
        self._data[name] = (keys, mat / norms, pls, raw)

    def search(
        self, name: str, vector: Sequence[float], limit: int, filters: dict | None = None
    ) -> list[VectorHit]:
        keys, mat, pls, _ = self._data.get(name, ([], None, [], []))
        if mat is None or not keys:
            return []
        q = np.asarray(vector, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1)
        scores = mat @ q
        order = np.argsort(-scores)
        hits = []
        for idx in order:
            p = pls[idx]
            if filters and any(p.get(k) != v for k, v in filters.items()):
                continue
            hits.append(VectorHit(code=p["code"], score=float(scores[idx]), payload=p))
            if len(hits) >= limit:
                break
        return hits

    def count(self, name: str) -> int:
        return len(self._data.get(name, ([],))[0])

    def healthy(self) -> bool:
        return True


class DiskStore:
    """Brute-force cosine search over float16 matrices persisted as .npy files.

    Zero-infrastructure option for notebooks (Colab + Google Drive) and single-node pilots: embeddings
    are computed once and reused across sessions/processes. ~150k codes x 384 dims = ~115 MB on disk and
    a search is one matrix-vector product (milliseconds). Use Qdrant for multi-node production."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._pending: dict[str, tuple[dict[str, int], list[list[float]], list[dict]]] = {}
        self._loaded: dict[str, tuple[np.ndarray, list[dict]]] = {}

    def _files(self, name: str) -> tuple[Path, Path]:
        return self.root / f"{name}.npy", self.root / f"{name}.json"

    def ensure_collection(self, name: str, dim: int) -> None:
        self._pending.setdefault(name, ({}, [], []))

    def upsert(
        self, name: str, ids: Sequence[str], vectors: Sequence[Sequence[float]], payloads: Sequence[dict]
    ) -> None:
        index, vecs, pls = self._pending.setdefault(name, ({}, [], []))
        for i, v, p in zip(ids, vectors, payloads, strict=True):
            if i in index:
                vecs[index[i]], pls[index[i]] = list(v), p
            else:
                index[i] = len(vecs)
                vecs.append(list(v))
                pls.append(p)

    def flush(self, name: str) -> None:
        if name not in self._pending:
            return
        _index, vecs, pls = self._pending.pop(name)
        mat = np.asarray(vecs, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1
        mat = (mat / norms).astype(np.float16)
        npy, meta = self._files(name)
        np.save(npy, mat)
        meta.write_text(json.dumps({"count": len(pls), "payloads": pls}))
        self._loaded[name] = (mat, pls)

    def _load(self, name: str) -> tuple[np.ndarray, list[dict]] | None:
        if name in self._loaded:
            return self._loaded[name]
        npy, meta = self._files(name)
        if not (npy.exists() and meta.exists()):
            return None
        data = (np.load(npy), json.loads(meta.read_text())["payloads"])
        self._loaded[name] = data
        return data

    def search(
        self, name: str, vector: Sequence[float], limit: int, filters: dict | None = None
    ) -> list[VectorHit]:
        data = self._load(name)
        if data is None:
            return []
        mat, pls = data
        q = np.asarray(vector, dtype=np.float32)
        q = (q / (np.linalg.norm(q) or 1)).astype(np.float16)
        scores = (mat @ q).astype(np.float32)
        k = min(len(pls), max(limit * 4, limit))
        top = np.argpartition(-scores, k - 1)[:k] if k < len(pls) else np.arange(len(pls))
        top = top[np.argsort(-scores[top])]
        hits = []
        for idx in top:
            p = pls[int(idx)]
            if filters and any(p.get(k2) != v for k2, v in filters.items()):
                continue
            hits.append(VectorHit(code=p["code"], score=float(scores[idx]), payload=p))
            if len(hits) >= limit:
                break
        return hits

    def count(self, name: str) -> int:
        if name in self._loaded:
            return len(self._loaded[name][1])
        _npy, meta = self._files(name)
        if not meta.exists():
            return 0
        with meta.open() as f:
            head = f.read(64)
        m = re.match(r'\{"count": (\d+)', head)
        return int(m.group(1)) if m else len(json.loads(meta.read_text())["payloads"])

    def healthy(self) -> bool:
        return self.root.exists()


@lru_cache
def get_vector_store() -> VectorStore:
    s = get_settings()
    if s.vector_store == "memory":
        return MemoryStore()
    if s.vector_store == "disk":
        return DiskStore(s.vector_dir)
    key = s.qdrant_api_key.get_secret_value() if s.qdrant_api_key else None
    return QdrantStore(s.qdrant_url, key, s.qdrant_path)
