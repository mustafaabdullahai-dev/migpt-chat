"""Spec tests: CACHING abstraction + the five cached categories.

CacheBackend / InMemoryCache / RedisCache must let the app run without Redis at all
(the local default is in-memory). These tests keep the whole flow cache-off by
default via conftest and flip ENABLE_CACHE on for the cache-specific checks.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.cache import (
    InMemoryCache,
    RedisCache,
    cache_key,
    cached_call,
    get_cache,
    reset_cache,
)


def test_cache_key_is_deterministic_and_scoped():
    assert cache_key("search", "ddg", "hello world", 8) == cache_key("search", "ddg", "hello world", 8)
    assert cache_key("page", "url") != cache_key("search", "url")


def test_in_memory_cache_ttl_and_capacity():
    cache = InMemoryCache(capacity=2, default_ttl=100)
    cache.set("a", 1, ttl=1)
    cache.set("b", 2)
    cache.set("c", 3)  # evicts the oldest (a)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3
    cache.set("a", 99, ttl=1)
    import time

    time.sleep(1.1)
    assert cache.get("a") is None  # expired


def test_redis_pack_unpack_round_trips_without_a_server():
    """The serialization contract of the optional Redis backend must survive any
    value the pipeline caches — tested without needing a live Redis."""
    cases: list[object] = [
        "a plain string",
        "j:that",
        "s:this",
        123,
        1.5,
        [1, 2, 3],
        {"title": "x", "url": "https://example.com", "source_type": "web"},
        {"nested": [{"a": [1.5, 2.0]}]},
    ]
    for case in cases:
        packed = RedisCache._pack(case)
        unpacked = RedisCache._unpack(packed)
        assert unpacked == case, f"round-trip failed for {case!r}: {unpacked!r}"


def test_redis_json_encodes_pydantic_models():
    from app.models.schemas import SearchResult

    results = [SearchResult(title="t", url="https://x", snippet="snip", source="x")]
    packed = RedisCache._pack(results)
    revived = RedisCache._unpack(packed)
    assert isinstance(revived, list) and isinstance(revived[0], dict)
    assert revived[0]["url"] == "https://x"


@pytest.mark.asyncio
async def test_cached_call_hits_cache_and_decode_revives():
    cache = InMemoryCache()
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return [{"url": "https://example.com"}]

    first = await cached_call(cache, cache_key("t", "q"), fetch, decode=lambda r: r)
    second = await cached_call(cache, cache_key("t", "q"), fetch, decode=lambda r: r)
    assert first == second == [{"url": "https://example.com"}]
    assert calls["n"] == 1


# ------------------------- local runs without Redis -------------------------


def test_local_default_is_in_memory(monkeypatch):
    """No Redis available → the app still runs with the in-memory backend."""
    monkeypatch.setattr(settings, "enable_cache", True)
    monkeypatch.setattr(settings, "redis_url", "")
    reset_cache()
    try:
        backend = get_cache()
        assert backend is not None
        assert isinstance(backend, InMemoryCache)
    finally:
        reset_cache()


# ------------------------- repeated calculations -------------------------


@pytest.mark.asyncio
async def test_calculate_caches_identical_query(monkeypatch):
    from app.agents.nodes.calculate import calculate
    from app.agents.nodes.helpers import run_tool

    import app.tools.registry  # noqa: F401  (registers tools into app.tools.base.registry)

    monkeypatch.setattr(settings, "enable_cache", True)
    reset_cache()
    state_tpl = {
        "query": "2**10 plus 5",
        "conversation_id": "c-cache",
        "user_id": "u",
        "mode": "auto",
        "history": [],
        "documents": [],
        "tool_runs": [],
        "errors": [],
    }
    calls = {"n": 0}
    original = run_tool

    async def counting_run_tool(name, **kwargs):
        if name in ("calculator", "unit_converter"):
            calls["n"] += 1
        return await original(name, **kwargs)

    monkeypatch.setattr("app.agents.nodes.calculate.run_tool", counting_run_tool)
    try:
        first = await calculate(dict(state_tpl))
        assert first["calculation"] is not None
        second = await calculate(dict(state_tpl))
        assert second["answer"] == first["answer"]
        # identical query ran the underlying tool only ONCE
        assert calls["n"] == 1
        # cached copy carries an empty tool_run list by design
        assert second["tool_runs"] == []
    finally:
        reset_cache()


@pytest.mark.asyncio
async def test_calc_cache_key_normalizes():
    from app.agents.nodes.calculate import _calc_cache_key

    assert _calc_cache_key("  2**10  plus 5 ") == _calc_cache_key("2**10 plus 5")


# ------------------------- document processing -------------------------


def test_parse_file_cached_parses_once(monkeypatch):
    from app.rag.ingestion import parse_file_cached, parse_file

    monkeypatch.setattr(settings, "enable_cache", True)
    reset_cache()

    calls = {"n": 0}

    def counting_parse_file(filename, content):
        calls["n"] += 1
        return parse_file(filename, content)

    monkeypatch.setattr("app.rag.ingestion.parse_file", counting_parse_file)
    content = b"same bytes repeated"
    try:
        a = parse_file_cached("notes.txt", content)
        b = parse_file_cached("notes.txt", content)
        assert a == b == parse_file("notes.txt", content)
        assert calls["n"] == 1  # parsed once despite two requests
    finally:
        reset_cache()


def test_as_pages_revives_json_shape():
    from app.rag.ingestion import _as_pages

    assert _as_pages([[0, "hello"], [1, "world"]]) == [(0, "hello"), (1, "world")]
    assert _as_pages("junk") == []


# ------------------------- embeddings -------------------------


def test_cached_embedding_provider_queries_once(monkeypatch):
    from app.rag.embeddings import CachedEmbeddingProvider, MockEmbeddings

    monkeypatch.setattr(settings, "enable_cache", True)
    reset_cache()

    class CountingEmbeddings(MockEmbeddings):
        def __init__(self) -> None:
            super().__init__()
            self.query_calls: list[str] = []

        def embed_query(self, text: str) -> list[float]:
            self.query_calls.append(text)
            return super().embed_query(text)

    inner = CountingEmbeddings()
    provider = CachedEmbeddingProvider(inner)
    try:
        a = provider.embed_query("octowalrus embedding token")
        b = provider.embed_query("octowalrus embedding token")
        assert a == b
        assert inner.query_calls == ["octowalrus embedding token"]  # inner hit once
    finally:
        reset_cache()


def test_cached_embedding_documents_partial(monkeypatch):
    from app.rag.embeddings import CachedEmbeddingProvider, MockEmbeddings

    monkeypatch.setattr(settings, "enable_cache", True)
    reset_cache()

    class CountingEmbeddings(MockEmbeddings):
        def __init__(self) -> None:
            super().__init__()
            self.doc_calls: list[list[str]] = []

        def embed_documents(self, texts):
            self.doc_calls.append(list(texts))
            return super().embed_documents(texts)

    inner = CountingEmbeddings()
    provider = CachedEmbeddingProvider(inner)
    try:
        texts = ["same token one", "same token two"]
        first = provider.embed_documents(texts)
        second = provider.embed_documents(["same token one", "same token two"])
        assert first == second
        # second embedding batch only asked the inner provider again... for nothing
        assert inner.doc_calls == [texts]  # no re-embed of already-cached texts
    finally:
        reset_cache()