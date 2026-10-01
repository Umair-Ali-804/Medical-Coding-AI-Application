"""Embedding providers.

* fastembed : local ONNX models (default BAAI/bge-small-en-v1.5), no PHI leaves the host
* openai    : any OpenAI-compatible /embeddings endpoint (OpenAI, Azure, vLLM, TEI...)
* hash      : deterministic hashed bag-of-words+char-trigrams; offline tests & CI only
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

import httpx
import numpy as np

from app.core.config import get_settings


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class HashEmbedder:
    """Feature-hashing embedder. Semantically weak but deterministic and dependency-free."""

    _tok = re.compile(r"[a-z0-9]+")

    def __init__(self, dim: int = 384):
        self.dim = dim
        self.name = f"hash-{dim}"

    def _vec(self, text: str) -> list[float]:
        v = np.zeros(self.dim, dtype=np.float32)
        words = self._tok.findall(text.lower())
        feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
        for w in words:
            padded = f"#{w}#"
            feats.extend(padded[i : i + 3] for i in range(len(padded) - 2))
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        n = float(np.linalg.norm(v))
        return (v / n).tolist() if n else v.tolist()

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class FastEmbedEmbedder:
    def __init__(self, model: str, dim: int, cache_dir: str | None = None):
        from fastembed import TextEmbedding  # type: ignore

        self._model = TextEmbedding(model_name=model, cache_dir=cache_dir)
        self.name = model
        self.dim = dim
        self._is_bge = "bge" in model.lower()

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(list(texts), batch_size=256)]

    def embed_query(self, text: str) -> list[float]:
        if self._is_bge:
            return next(iter(self._model.query_embed(text))).tolist()
        return self.embed_documents([text])[0]


class SentenceTransformerEmbedder:
    """Same BGE model through sentence-transformers/PyTorch: uses a GPU when present (Colab T4 embeds
    ~150k codes in a couple of minutes). Vectors are L2-normalized; BGE queries get the retrieval prefix."""

    _BGE_QUERY = "Represent this sentence for searching relevant passages: "

    def __init__(self, model: str, dim: int, device: str | None = None):
        from sentence_transformers import SentenceTransformer  # type: ignore

        if device is None:
            try:
                import torch  # type: ignore

                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:  # pragma: no cover
                device = "cpu"
        self._model = SentenceTransformer(model, device=device)
        self.name = f"{model}-st"
        self.dim = dim
        self._is_bge = "bge" in model.lower()

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._model.encode(list(texts), batch_size=256, normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        q = (self._BGE_QUERY + text) if self._is_bge else text
        return self._model.encode([q], normalize_embeddings=True)[0].tolist()


class OpenAICompatibleEmbedder:
    def __init__(self, model: str, dim: int, base_url: str, api_key: str | None, timeout: float = 60.0):
        self.name = model
        self.dim = dim
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 128):
            resp = self._client.post(
                "/embeddings", json={"model": self.name, "input": list(texts[i : i + 128])}
            )
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embedding_provider == "hash":
        return HashEmbedder(s.embedding_dim)
    if s.embedding_provider == "openai":
        if not s.embedding_api_base:
            raise RuntimeError("EMBEDDING_API_BASE is required for EMBEDDING_PROVIDER=openai")
        key = s.embedding_api_key.get_secret_value() if s.embedding_api_key else None
        return OpenAICompatibleEmbedder(s.embedding_model, s.embedding_dim, s.embedding_api_base, key)
    if s.embedding_provider == "sentence_transformers":
        return SentenceTransformerEmbedder(s.embedding_model, s.embedding_dim, s.embedding_device)
    return FastEmbedEmbedder(s.embedding_model, s.embedding_dim, s.embedding_cache_dir)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
