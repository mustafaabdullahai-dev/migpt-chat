"""Retriever: query embedding -> FAISS top-k -> rerank (optional) -> chunk metadata from DB -> RetrievedChunk list."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models import DocumentChunk
from app.models.schemas import RetrievedChunk
from app.rag.embeddings import get_embeddings
from app.rag.rerank import get_reranker
from app.rag.vectorstore import get_vectorstore


def retrieve(query: str, k: int | None = None, document_ids: list[str] | None = None) -> list[RetrievedChunk]:
    k = k or settings.top_k_retrieval
    embeddings = get_embeddings()
    vectorstore = get_vectorstore()
    if vectorstore.count == 0:
        return []

    qvec = embeddings.embed_query(query)
    hits = vectorstore.search(qvec, k * 2)  # oversample for filtering/reranking

    with SessionLocal() as db:
        candidates: list[tuple[str, float, str]] = []  # (chunk_id, score, text)
        for chunk_id, score in hits:
            doc_id = chunk_id.split("::", 1)[0]
            if document_ids and doc_id not in document_ids:
                continue
            row = db.get(DocumentChunk, chunk_id.split("::", 1)[1])
            if row is None:
                continue
            candidates.append((chunk_id, score, row.text))

        # OPTIONAL RERANKING stage: deterministic lexical+cosine blend or none.
        reranker = get_reranker()
        if reranker is not None and candidates:
            candidates = reranker.rerank(query, candidates)

        results: list[RetrievedChunk] = []
        for chunk_id, score, _text in candidates[:k]:
            doc_id = chunk_id.split("::", 1)[0]
            row = db.get(DocumentChunk, chunk_id.split("::", 1)[1])
            if row is None:
                continue
            meta = JsonSafe.loads(row.meta_json, {})
            filename = meta.get("filename") or doc_id
            results.append(
                RetrievedChunk(
                    document_id=meta.get("document_id", doc_id),
                    document_title=filename,
                    filename=filename,
                    source=meta.get("source") or ("file" if meta else doc_id),
                    chunk_id=chunk_id,
                    page=int(meta.get("page", row.page or 0)),
                    text=row.text,
                    score=score,
                )
            )
        return results


def all_chunk_count() -> int:
    with SessionLocal() as db:
        return len(list(db.scalars(select(DocumentChunk).limit(1))))


class JsonSafe:
    @staticmethod
    def loads(raw: str, default: object) -> dict:
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else default
        except Exception:
            return default