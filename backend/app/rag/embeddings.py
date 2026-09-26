"""Embedding abstraction.

Providers are swappable via `settings.embedding_provider`; nothing in the RAG
pipeline is coupled to a specific vendor.

    EmbeddingProvider
        ├── LocalEmbeddings   (fastembed ONNX — free, offline after first download)
        ├── RemoteEmbeddings  (OpenAI-compatible /embeddings API)
        └── MockEmbeddings    (demo mode)
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Sequence

import numpy as np

from app.core.config import settings
from app.services.cache import cache_key, get_cache

_UNIT_VECTOR_DIM = 384


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        ...

    @property
    def dimension(self) -> int:
        return _UNIT_VECTOR_DIM


class CachedEmbeddingProvider(EmbeddingProvider):
    """Memoizes embeddings through the configured CacheBackend (Redis or in-memory)
    so identical texts are never re-embedded. Transparent no-op when caching is off."""

    def __init__(self, inner: EmbeddingProvider, ttl: int = 86400) -> None:
        self._inner = inner
        self._ttl = ttl

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    @staticmethod
    def _key(purpose: str, text: str) -> str:
        return cache_key("embed", purpose, text)

    def embed_query(self, text: str) -> list[float]:
        cache = get_cache()
        if cache is None:
            return self._inner.embed_query(text)
        key = self._key("q", text)
        hit = cache.get(key)
        if hit is not None:
            return [_f(v) for v in hit]
        vec = self._inner.embed_query(text)
        if vec:
            cache.set(key, vec, self._ttl)
        return vec

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        cache = get_cache()
        if cache is None or not texts:
            return self._inner.embed_documents(texts)
        out: list[list[float] | None] = [None] * len(texts)
        missing: list[int] = []
        for i, t in enumerate(texts):
            hit = cache.get(self._key("d", t))
            if hit is not None:
                out[i] = [_f(v) for v in hit]
            else:
                missing.append(i)
        if missing:
            vecs = self._inner.embed_documents([texts[i] for i in missing])
            for i, v in zip(missing, vecs):
                if v:
                    out[i] = v
                    cache.set(self._key("d", texts[i]), v, self._ttl)
        return [v for v in out if v is not None]


def _f(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class LocalEmbeddings(EmbeddingProvider):
    def __init__(self, model: str | None = None, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding

        self._dimension: int | None = None
        self._model = TextEmbedding(
            model_name=model or settings.embedding_model_repo,
            cache_dir=str(settings.data_path),
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vecs = [v.tolist() for v in self._model.embed([t for t in texts])]
        if self._dimension is None and vecs:
            self._dimension = len(vecs[0])
        return vecs

    def embed_query(self, text: str) -> list[float]:
        vec = next(self._model.embed([text])).tolist()
        if self._dimension is None:
            self._dimension = len(vec)
        return vec


class RemoteEmbeddings(EmbeddingProvider):
    """OpenAI-compatible embeddings endpoint (e.g. OpenAI, Azure, LiteLLM).

    Keeps the RAG pipeline provider-agnostic: same interface as LocalEmbeddings.
    """

    def __init__(
        self,
        url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        import httpx

        self._http = httpx.Client(
            base_url=(url or settings.embedding_remote_url).rstrip("/"),
            headers={"Authorization": f"Bearer {api_key or settings.embedding_remote_api_key}"},
            timeout=timeout,
        )
        self._model = model or settings.embedding_remote_model
        self._dimension: int | None = None

    def _embed(self, input_: list[str]) -> list[list[float]]:
        resp = self._http.post("/embeddings", json={"model": self._model, "input": input_})
        resp.raise_for_status()
        data = resp.json()
        vectors = [item["embedding"] for item in data["data"]]
        if self._dimension is None and vectors:
            self._dimension = len(vectors[0])
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed([t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


class MockEmbeddings(EmbeddingProvider):
    """Live demo embeddings: deterministic pseudo-vectors via hashing. Not semantically strong."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        vec = np.zeros(_UNIT_VECTOR_DIM, dtype=np.float32)
        for i in range(0, len(text) - 1):
            h = int.from_bytes(hashlib.sha256(text[i : i + 3].encode()).digest()[:8], "big")
            idx = h % _UNIT_VECTOR_DIM
            vec[idx] += 1.0
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec.tolist()


def default_dim() -> int:
    return _UNIT_VECTOR_DIM


_embeddings: EmbeddingProvider | None = None


def reset_embeddings() -> None:
    """Drop the cached provider so the next get_embeddings() call re-reads settings."""
    global _embeddings
    _embeddings = None


def get_embeddings() -> EmbeddingProvider:
    global _embeddings
    if _embeddings is not None:
        return _embeddings
    if settings.is_demo or settings.embedding_provider == "mock":
        _embeddings = MockEmbeddings()
    elif settings.embedding_provider == "remote":
        if not settings.embedding_remote_url:
            raise RuntimeError("embedding_provider='remote' requires EMBEDDING_REMOTE_URL")
        try:
            _embeddings = RemoteEmbeddings()
        except Exception:
            _embeddings = MockEmbeddings()  # graceful fallback -> system still works
    else:
        try:
            _embeddings = LocalEmbeddings()
        except Exception:
            _embeddings = MockEmbeddings()  # graceful fallback -> system still works
    if settings.enable_cache:
        _embeddings = CachedEmbeddingProvider(_embeddings)
    return _embeddings