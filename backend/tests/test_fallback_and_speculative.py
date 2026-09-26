"""Tests: search-provider fallback chain, cache normalization, speculative warm search."""

from __future__ import annotations

import asyncio
import types

from app.models.schemas import SearchResult
from app.services import cache as cache_mod
from app.services import search as search_mod


def _chain(primary, fallback=None):
    if fallback is None:
        return [primary]
    return [primary, fallback]


# ------------------------- fallback chain -------------------------


def test_chain_appends_duckduckgo_when_primary_is_paid(monkeypatch):
    monkeypatch.setattr(search_mod, "_search", None)
    monkeypatch.setattr(
        search_mod,
        "settings",
        types.SimpleNamespace(
            is_demo=False,
            search_provider="tavily",
            tavily_api_key="k",
            brave_api_key="",
            serper_api_key="",
            google_search_api_key="",
            google_search_cx="",
            enable_web_search=True,
        ),
    )
    chain = search_mod.get_search_provider_chain()
    assert [p.name for p in chain] == ["tavily", "duckduckgo"]


def test_chain_is_single_when_primary_is_duckduckgo_or_mock(monkeypatch):
    from app.services.search import DuckDuckGoSearchProvider

    def _settings(is_demo=False, provider="duckduckgo"):
        return types.SimpleNamespace(
            is_demo=is_demo,
            search_provider=provider,
            tavily_api_key="",
            brave_api_key="",
            serper_api_key="",
            google_search_api_key="",
            google_search_cx="",
            enable_web_search=True,
        )

    monkeypatch.setattr(search_mod, "_search", None)
    monkeypatch.setattr(search_mod, "settings", _settings(is_demo=False, provider="duckduckgo"))
    assert len(search_mod.get_search_provider_chain()) == 1

    monkeypatch.setattr(search_mod, "settings", _settings(is_demo=True, provider="duckduckgo"))
    monkeypatch.setattr(search_mod, "_search", None)
    chain = search_mod.get_search_provider_chain()
    assert [p.name for p in chain] == ["mock"]

    # even a *mock* chain must never append a DDG fallback
    assert len(chain) == 1
    assert not isinstance(chain[0], DuckDuckGoSearchProvider)


def test_web_search_falls_back_when_primary_returns_nothing(monkeypatch):
    """Primary yields no results -> DuckDuckGo fallback results are returned."""
    from app.tools.web_search import WebSearchTool

    primary_results: list[SearchResult] = []
    fallback_results = [SearchResult(title="F", url="https://f.test/f", snippet="ddg hit")]

    class _Primary:
        name = "tavily"
        async def search(self, query, limit=8):
            return primary_results

    class _Fallback:
        name = "duckduckgo"
        async def search(self, query, limit=8):
            return fallback_results

    monkeypatch.setattr("app.tools.web_search.get_search_provider_chain", lambda: _chain(_Primary(), _Fallback()))

    tool = WebSearchTool()
    results = asyncio.run(tool(query="python 2026"))
    assert [r.url for r in results] == ["https://f.test/f"]


def test_web_search_prefers_primary_results(monkeypatch):
    from app.tools.web_search import WebSearchTool

    primary_results = [SearchResult(title="P", url="https://p.test/p", snippet="hit")]
    fallback_results = [SearchResult(title="F", url="https://f.test/f", snippet="hit")]

    class _Primary:
        name = "tavily"
        async def search(self, query, limit=8):
            return primary_results

    class _Fallback:
        name = "duckduckgo"
        async def search(self, query, limit=8):
            return fallback_results

    monkeypatch.setattr("app.tools.web_search.get_search_provider_chain", lambda: _chain(_Primary(), _Fallback()))

    tool = WebSearchTool()
    results = asyncio.run(tool(query="task queues in python"))
    assert [r.url for r in results] == ["https://p.test/p"]


def test_web_search_raises_to_fallback_and_returns_host_of_primary(monkeypatch, search_mock):
    """Primary errors -> fallback still serves results (chain never hard-fails)."""
    from app.tools.web_search import WebSearchTool

    class _Exploding:
        name = "tavily"
        async def search(self, query, limit=8):
            raise RuntimeError("ups")

    class _Fallback:
        name = "duckduckgo"
        async def search(self, query, limit=8):
            return [SearchResult(title="F", url="https://f.test/f", snippet="hit")]

    monkeypatch.setattr("app.tools.web_search.get_search_provider_chain", lambda: _chain(_Exploding(), _Fallback()))

    tool = WebSearchTool()
    results = asyncio.run(tool(query="something topical"))
    assert [r.url for r in results] == ["https://f.test/f"]


# ------------------------- cache normalization -------------------------


def test_cache_key_normalizes_whitespace_and_case():
    assert cache_mod.cache_key("search", "duckduckgo", "  Latest   Python NEWS ", 8) == cache_mod.cache_key(
        "search", "duckduckgo", "latest python news", 8
    )
    assert cache_mod.cache_key("search", "duckduckgo", "a") != cache_mod.cache_key("search", "duckduckgo", "b")


# ------------------------- speculative warm search -------------------------


def test_is_web_plausible_gates_off_offline_intents(monkeypatch):
    import app.agents.nodes.speculative as spec

    on = types.SimpleNamespace(enable_web_search=True, enable_cache=True)
    monkeypatch.setattr(spec, "settings", on)

    monkeypatch.setattr(spec, "_fast_classify", lambda q, d, m: types.SimpleNamespace(needs_web=False))
    assert spec.is_web_plausible("2+2", "auto") is False

    monkeypatch.setattr(spec, "_fast_classify", lambda q, d, m: types.SimpleNamespace(needs_web=True))
    assert spec.is_web_plausible("latest ai news", "auto") is True

    monkeypatch.setattr(spec, "_fast_classify", lambda q, d, m: None)
    assert spec.is_web_plausible("something weird here", "auto") is True

    assert spec.is_web_plausible("latest ai news", "no_web") is False

    monkeypatch.setattr(spec.settings, "enable_cache", False)
    assert spec.is_web_plausible("latest ai news", "auto") is False


def test_is_web_plausible_uses_real_fast_classify(monkeypatch):
    import app.agents.nodes.speculative as spec

    monkeypatch.setattr(
        spec,
        "settings",
        types.SimpleNamespace(enable_web_search=True, enable_cache=True),
    )

    # CALCULATION/trivial chat are offline -> never warm
    assert spec.is_web_plausible("what is 2 + 2?", "auto") is False
    # topical current-events query -> warm (CURRENT_INFORMATION needs_web=True)
    assert spec.is_web_plausible("what is the latest python version?", "auto") is True