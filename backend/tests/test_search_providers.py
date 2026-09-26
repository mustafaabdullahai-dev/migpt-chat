"""Spec tests: FREE-FIRST ARCHITECTURE + OPTIONAL SEARCH PROVIDERS.

Locks the SearchProvider abstraction, the free default (DuckDuckGo), and that paid
providers are strictly opt-in: no key = the app still works and falls back to DDG.
"""

from __future__ import annotations

import inspect
import types

import pytest

from app.models.schemas import SearchResult
from app.services import search as search_mod


def _fake_settings(**overrides) -> types.SimpleNamespace:
    base = {
        "is_demo": False,
        "search_provider": "duckduckgo",
        "enable_web_search": True,
        "tavily_api_key": "",
        "brave_api_key": "",
        "serper_api_key": "",
        "google_search_api_key": "",
        "google_search_cx": "",
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _reset_provider(monkeypatch):
    monkeypatch.setattr(search_mod, "_search", None)
    yield


def test_search_provider_abstract_signature():
    from app.services.search import SearchProvider

    assert getattr(SearchProvider.search, "__isabstractmethod__", False)
    assert inspect.iscoroutinefunction(SearchProvider.search)

    sig = inspect.signature(SearchProvider.search)
    assert list(sig.parameters) == ["self", "query", "limit"]
    assert sig.parameters["limit"].default == 8


def test_default_provider_is_duckduckgo_and_free(monkeypatch):
    from app.services.search import DuckDuckGoSearchProvider, get_search_provider

    monkeypatch.setattr(search_mod, "settings", _fake_settings(search_provider="duckduckgo"))
    assert isinstance(get_search_provider(), DuckDuckGoSearchProvider)

    monkeypatch.setattr(search_mod, "settings", _fake_settings(search_provider=""))
    assert isinstance(get_search_provider(), DuckDuckGoSearchProvider)


def test_demo_mode_uses_mock(monkeypatch):
    from app.services.search import MockSearchProvider, get_search_provider

    monkeypatch.setattr(search_mod, "settings", _fake_settings(is_demo=True))
    assert isinstance(get_search_provider(), MockSearchProvider)


@pytest.mark.parametrize(
    "provider,name,key_attr",
    [
        ("tavily", "TavilySearchProvider", "tavily_api_key"),
        ("brave", "BraveSearchProvider", "brave_api_key"),
        ("serper", "SerperSearchProvider", "serper_api_key"),
    ],
)
def test_paid_providers_require_key_else_fall_back_to_duckduckgo(monkeypatch, provider, name, key_attr):
    from app.services.search import DuckDuckGoSearchProvider, get_search_provider

    cls = getattr(search_mod, name)

    # selected only when SEARCH_PROVIDER matches AND the key is set
    monkeypatch.setattr(search_mod, "settings", _fake_settings(search_provider=provider, **{key_attr: "sekret-key"}))
    search_mod._search = None
    assert isinstance(get_search_provider(), cls)

    # without the key the app keeps working: falls back to the free provider
    monkeypatch.setattr(search_mod, "settings", _fake_settings(search_provider=provider))
    search_mod._search = None
    assert isinstance(get_search_provider(), DuckDuckGoSearchProvider)


def test_google_requires_provider_name_and_key_and_cx(monkeypatch):
    from app.services.search import DuckDuckGoSearchProvider, GoogleSearchProvider, get_search_provider

    # config must name google AND have both key and cx for the google provider
    monkeypatch.setattr(
        search_mod,
        "settings",
        _fake_settings(search_provider="google", google_search_api_key="k", google_search_cx="cx"),
    )
    search_mod._search = None
    assert isinstance(get_search_provider(), GoogleSearchProvider)

    # provider named google without cx must NOT force google — falls back to DDG
    monkeypatch.setattr(
        search_mod,
        "settings",
        _fake_settings(search_provider="google", google_search_api_key="k"),
    )
    search_mod._search = None
    assert isinstance(get_search_provider(), DuckDuckGoSearchProvider)

    # leftover google keys must NOT hijack another configured provider
    monkeypatch.setattr(
        search_mod,
        "settings",
        _fake_settings(search_provider="duckduckgo", google_search_api_key="k", google_search_cx="cx"),
    )
    search_mod._search = None
    assert isinstance(get_search_provider(), DuckDuckGoSearchProvider)


# ------------------------- response mapping for optional providers -------------------------


class _FakeResp:
    def __init__(self, data: dict):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeHttp:
    def __init__(self, resp: _FakeResp):
        self._resp = resp
        self.calls = 0

    async def get(self, url, **kw):
        self.calls += 1
        return self._resp

    async def post(self, url, **kw):
        self.calls += 1
        return self._resp


def _run_search(monkeypatch, provider_cls, resp_payload, **kwargs):
    import asyncio

    fake = _FakeHttp(_FakeResp(resp_payload))
    monkeypatch.setattr(search_mod, "get_http_client", lambda: fake)
    prov = provider_cls(api_key="k", **(kwargs if kwargs else {}))
    return asyncio.run(prov.search("pydantic vs langchain", 8)), fake


def test_tavily_maps_and_dedupes(monkeypatch):
    from app.services.search import TavilySearchProvider

    payload = {
        "results": [
            {"title": "A", "url": "https://x.com/a", "content": "snip"},
            {"title": "A dup", "url": "https://x.com/a", "content": "again"},
            {"title": "B", "url": "https://y.com/b", "content": "two"},
        ]
    }
    results, _ = _run_search(monkeypatch, TavilySearchProvider, payload)
    assert [r.url for r in results] == ["https://x.com/a", "https://y.com/b"]
    assert results[0].source == "x.com"
    assert results[0].snippet == "snip"


def test_brave_maps_results(monkeypatch):
    from app.services.search import BraveSearchProvider

    payload = {"web": {"results": [{"title": "T", "url": "https://brave.test/t", "description": "d"}]}}
    results, _ = _run_search(monkeypatch, BraveSearchProvider, payload)
    assert results == [
        SearchResult(title="T", url="https://brave.test/t", snippet="d", source="brave.test")
    ]


def test_serper_maps_results(monkeypatch):
    from app.services.search import SerperSearchProvider

    payload = {"organic": [{"title": "S", "link": "https://s.test/s", "snippet": "ss"}]}
    results, _ = _run_search(monkeypatch, SerperSearchProvider, payload)
    assert results == [
        SearchResult(title="S", url="https://s.test/s", snippet="ss", source="s.test")
    ]