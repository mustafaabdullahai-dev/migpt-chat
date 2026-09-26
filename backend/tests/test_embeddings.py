"""Embedding abstraction tests: provider interface, dispatch, remote + mock behavior."""

from __future__ import annotations

import numpy as np
import pytest

from app.rag.embeddings import (
    EmbeddingProvider,
    MockEmbeddings,
    RemoteEmbeddings,
    get_embeddings,
    reset_embeddings,
)

_SAMPLE = ["fast API and async", "the quick brown fox jumps"]


def test_provider_interface_satisfied():
    assert issubclass(MockEmbeddings, EmbeddingProvider)
    assert issubclass(RemoteEmbeddings, EmbeddingProvider)


def test_mock_embeddings_are_deterministic_and_normalized():
    emb = MockEmbeddings()
    v1 = emb.embed_query(_SAMPLE[0])
    v2 = emb.embed_query(_SAMPLE[0])
    assert v1 == v2
    norm = float(np.linalg.norm(v1))
    assert abs(norm - 1.0) < 1e-6
    assert emb.embed_documents(_SAMPLE)[0] == v1


def test_mock_embeddings_different_texts_differ():
    emb = MockEmbeddings()
    assert emb.embed_query(_SAMPLE[0]) != emb.embed_query(_SAMPLE[1])


def test_remote_embeddings_call_openai_shape(monkeypatch):
    """RemoteEmbeddings must POST {model, input} to /embeddings and map OpenAI response order."""
    calls: dict = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            data = [{"embedding": [float(i)]} for i in range(len(_SAMPLE))]
            return {"object": "list", "data": data, "model": "text-embedding-3-small"}

    class _FakeClient:
        def __init__(self, **kwargs):
            calls["kwargs"] = kwargs

        def post(self, path, json):
            calls["path"] = path
            calls["payload"] = json
            return _FakeResponse()

        def close(self):
            return None

    monkeypatch.setattr("httpx.Client", _FakeClient)
    remote = RemoteEmbeddings(url="https://fake.local/v1", model="m", api_key="k")

    assert remote.embed_query("abc") == [0.0]
    assert remote.embed_documents(_SAMPLE) == [[0.0], [1.0]]
    assert calls["path"] == "/embeddings"
    assert calls["payload"]["model"] == "m"
    assert calls["payload"]["input"] == list(_SAMPLE)
    assert calls["kwargs"]["base_url"] == "https://fake.local/v1"
    assert "Bearer k" in calls["kwargs"]["headers"]["Authorization"]


def test_remote_embeddings_raise_exposes_failure(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self) -> None:
            raise RuntimeError("401 unauthorized")

        def json(self):
            return {}

    class _FakeClient:
        def __init__(self, **kwargs):
            return None

        def post(self, path, json):
            return _FakeResponse()

        def close(self):
            return None

    monkeypatch.setattr("httpx.Client", _FakeClient)
    remote = RemoteEmbeddings(url="https://fake.local/v1", model="m", api_key="k")
    with pytest.raises(RuntimeError):
        remote.embed_query("abc")


def test_dispatch_mock_when_demo_like(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "embedding_provider", "local")
    reset_embeddings()
    try:
        assert isinstance(get_embeddings(), MockEmbeddings)
    finally:
        reset_embeddings()


def test_dispatch_remote_requires_url(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "embedding_provider", "remote")
    monkeypatch.setattr(settings, "embedding_remote_url", "")
    reset_embeddings()
    try:
        with pytest.raises(RuntimeError, match="EMBEDDING_REMOTE_URL"):
            get_embeddings()
    finally:
        reset_embeddings()


def test_dispatch_remote_falls_back_to_mock(monkeypatch):
    from app.core.config import settings

    def boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "embedding_provider", "remote")
    monkeypatch.setattr(settings, "embedding_remote_url", "https://fake.local/v1")
    monkeypatch.setattr("app.rag.embeddings.RemoteEmbeddings", boom)
    reset_embeddings()
    try:
        assert isinstance(get_embeddings(), MockEmbeddings)
    finally:
        reset_embeddings()