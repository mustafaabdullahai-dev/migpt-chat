"""FAISS vector store with disk persistence and an id->chunk mapping.

Architecture is provider-agnostic: FAISS is the default local backbone, swappable later.
"""

from __future__ import annotations

import pickle
import threading
from pathlib import Path

import faiss
import numpy as np

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("rag.vectorstore")

_META_PATH = settings.data_path / "faiss_meta.pkl"


class FaissVectorStore:
    """Thread-safe FAISS index. IndexFlatIP over L2-normalized vectors => cosine similarity."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids: list[str] = []
        self._index: faiss.Index | None = None
        self._dim: int | None = None
        self._load_legacy()

    def _ensure_index(self, dim: int) -> None:
        if self._index is None or (self._dim and self._dim != dim):
            self._index = faiss.IndexFlatIP(dim)
            self._dim = dim

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._ids)

    def add(self, ids: list[str], vectors: list[list[float]]) -> None:
        if not ids:
            return
        with self._lock:
            self._add_locked(ids, vectors)

    def _add_locked(self, ids: list[str], vectors: list[list[float]]) -> None:
        """Insert vectors, assuming self._lock is already held (re-entrant-safe)."""
        dim = len(vectors[0])
        self._ensure_index(dim)
        matrix = np.asarray(vectors, dtype=np.float32)
        faiss.normalize_L2(matrix)
        self._index.add(matrix)  # type: ignore[union-attr]
        self._ids.extend(ids)
        self._persist()

    def search(self, query_vector: list[float], k: int) -> list[tuple[str, float]]:
        with self._lock:
            if self._index is None or self._index.ntotal == 0:
                return []
            q = np.asarray([query_vector], dtype=np.float32)
            faiss.normalize_L2(q)
            scores, indices = self._index.search(q, min(k, self._index.ntotal))  # type: ignore[union-attr]
        out: list[tuple[str, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._ids):
                continue
            out.append((self._ids[int(idx)], float(score)))
        return out

    def remove_document(self, doc_ids_to_remove: set[str]) -> None:
        """Rebuild index excluding chunks belonging to removed documents."""
        with self._lock:
            if not self._ids:
                return
            keep_idx = [i for i, cid in enumerate(self._ids) if cid.split("::", 1)[0] not in doc_ids_to_remove]
            if len(keep_idx) == len(self._ids):
                return
            # Re-collect from the loaded index is complex; simplest: mark and rebuild lazily
            # Instead, we rebuild from persisted vectors.
            vectors = self._decode_all()
            new_ids = [self._ids[i] for i in keep_idx]
            new_vectors = [vectors[i] for i in keep_idx]
            self._ids = []
            self._index = None
            self._dim = None
            if new_ids:
                self._add_locked(new_ids, new_vectors)
            else:
                self._persist()

    def _decode_all(self) -> list[list[float]]:
        if self._index is None or self._index.ntotal == 0:
            return []
        return self._index.reconstruct_n(0, self._index.ntotal).tolist()  # type: ignore[union-attr]

    def _persist(self) -> None:
        try:
            faiss.write_index(self._index, str(settings.vector_index_path))
            with open(_META_PATH, "wb") as fh:
                pickle.dump({"ids": self._ids}, fh)
        except Exception as exc:
            logger.warning("faiss persist failed", extra={"event": "rag.persist.error", "error": str(exc)})

    def _load_legacy(self) -> None:
        try:
            if settings.vector_index_path.exists() and _META_PATH.exists():
                self._index = faiss.read_index(str(settings.vector_index_path))
                with open(_META_PATH, "rb") as fh:
                    self._ids = pickle.load(fh).get("ids", [])
                if self._index.ntotal > 0:  # type: ignore[union-attr]
                    vec = self._index.reconstruct(0)  # type: ignore[union-attr]
                    self._dim = int(vec.shape[0])
        except Exception:
            logger.warning("faiss load failed; starting empty", extra={"event": "rag.load.error"})
            self._ids = []
            self._index = None


_vectorstore_singleton: FaissVectorStore | None = None
_lock = threading.Lock()


def get_vectorstore() -> FaissVectorStore:
    global _vectorstore_singleton
    if _vectorstore_singleton is None:
        with _lock:
            if _vectorstore_singleton is None:
                _vectorstore_singleton = FaissVectorStore()
    return _vectorstore_singleton