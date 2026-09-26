"""Spec tests: document RAG architecture — metadata, optional reranking, doc citations."""

from __future__ import annotations

import pytest

from app.models.schemas import RetrievedChunk


# ------------------------- metadata (document_id, filename, page, chunk_id, source) -------------------------


def test_retrieved_chunk_carries_full_metadata(client):
    from app.rag.retriever import retrieve

    text = "octowalrus_query embedding maps text and queries into a shared vector space for retrieval."
    resp = client.post("/api/documents/upload", files={"file": ("paper.txt", text.encode(), "text/plain")})
    assert resp.status_code == 200
    doc_id = resp.json()["document_id"]
    try:
        results = retrieve("octowalrus_query embedding vector", k=4, document_ids=[doc_id])
        assert results
        chunk = results[0]
        assert chunk.document_id == doc_id
        assert chunk.document_title == "paper.txt"
        assert isinstance(chunk.page, int)
        assert chunk.chunk_id.startswith(f"{doc_id}::")
        assert chunk.filename == "paper.txt"
        assert chunk.source == "paper"  # source metadata persisted at ingestion
    finally:
        client.delete(f"/api/documents/{doc_id}")


def test_chunk_meta_json_contains_source(client):
    resp = client.post(
        "/api/documents/upload",
        files={"file": ("meta.txt", b"octowalrus_meta page-zero details " * 6, "text/plain")},
    )
    assert resp.status_code == 200
    doc_id = resp.json()["document_id"]

    from app.db.base import SessionLocal
    from app.db.models import DocumentChunk

    try:
        with SessionLocal() as db:
            rows = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).all()
            assert rows
            meta = __import__("json").loads(rows[0].meta_json)
        for key in ("document_id", "filename", "page", "chunk_id", "source"):
            assert key in meta, f"missing meta key {key}"
        assert meta["filename"] == "meta.txt"
        assert meta["source"]  # spec metadata: source
    finally:
        client.delete(f"/api/documents/{doc_id}")


# ------------------------- optional reranking stage -------------------------


def test_rerank_disabled_returns_none_by_default():
    from app.rag.rerank import reset_reranker, get_reranker
    from app.core.config import settings

    reset_reranker()
    old = (settings.enable_reranking, settings.rerank_mode)
    settings.enable_reranking = True
    settings.rerank_mode = "hybrid"
    try:
        assert get_reranker() is not None
    finally:
        settings.enable_reranking, settings.rerank_mode = old
    reset_reranker()


def test_reranker_off_when_disabled(monkeypatch):
    from app.core.config import settings
    from app.rag.rerank import reset_reranker, get_reranker

    monkeypatch.setattr(settings, "enable_reranking", False)
    reset_reranker()
    assert get_reranker() is None


def test_hybrid_reranker_promotes_lexically_relevant_hit():
    from app.rag.rerank import HybridReranker

    reranker = HybridReranker(w_cos=0.0, w_lex=1.0)  # pure lexical for deterministic test
    candidates = [
        ("c1", 1.0, "completely unrelated topic about llama farms"),
        ("c2", 0.6, "octowalrus the blue herring migrates north"),
    ]
    out = reranker.rerank("octowalrus herring", candidates)
    assert out[0][0] == "c2"  # higher lexical overlap wins despite lower cosine


def test_hybrid_reranker_keeps_order_for_empty_query():
    from app.rag.rerank import HybridReranker

    out = HybridReranker().rerank("", [("c1", 1.0, "x"), ("c2", 0.9, "y")])
    assert [o[0] for o in out] == ["c1", "c2"]


# ------------------------- citations reference document + page/chunk -------------------------


def test_build_document_citations_numbered_and_located():
    from app.services.citations import build_document_citations

    chunks = [
        RetrievedChunk(
            document_id="d1", document_title="paper.pdf", filename="paper.pdf",
            chunk_id="d1::abc123xyz", page=7, text="methodology", source="paper",
        )
    ]
    cites = build_document_citations(chunks)
    assert len(cites) == 1
    c = cites[0]
    assert c.title == "paper.pdf"
    assert c.source == "document"
    assert c.quality == "user document"
    assert c.page == 7
    assert c.chunk_id == "d1::abc123xyz"
    assert c.url == ""  # local file, no URL


def test_build_citation_block_renders_doc_location():
    from app.models.schemas import Citation
    from app.services.citations import build_citation_block

    block = build_citation_block([Citation(index=1, title="paper.pdf", source="document", quality="user document", page=7, chunk_id="d1::abc123xyz")])
    assert "page 7" in block
    assert "chunk 123xyz" in block
    assert "[1] paper.pdf" in block
    assert "uploaded file (your content)" in block


def test_source_label_handles_user_document():
    from app.services.citations import source_label

    assert source_label("user document") == "uploaded file (your content)"