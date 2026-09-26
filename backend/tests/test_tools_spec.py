"""Spec-compliance tests for the modular tool system (web_search, fetch_webpage, deep_research)."""

from __future__ import annotations

import pytest

from app.models.schemas import PageContent, SearchResult
from app.tools.deep_research import DeepResearchTool
from app.tools.web_search import WebSearchTool
from app.tools.webpage import WebpageTool, _extract_links


def _sr(url: str, title: str = "t") -> SearchResult:
    return SearchResult(title=title, url=url, snippet="s", source=url.split("/")[2])


# ---------- web_search: multiple queries + domain filtering + dedupe ----------

def test_web_search_merge_dedupes_and_limits():
    merged = [  # duplicate url + extra results
        _sr("https://site-a.com/1", "a1"),
        _sr("https://site-a.com/1", "a1-dup"),
        _sr("https://site-b.com/2", "b2"),
        _sr("https://site-c.com/3", "c3"),
    ]
    out = WebSearchTool._merge(merged, limit=2, exclude=set())
    assert [r.url for r in out] == ["https://site-a.com/1", "https://site-b.com/2"]


def test_web_search_domain_filtering_exact_and_subdomain():
    merged = [
        _sr("https://spam.myads.net/x"),
        _sr("https://myads.net/y"),
        _sr("https://fine.example.com/z"),
    ]
    out = WebSearchTool._merge(merged, limit=10, exclude={"myads.net"})
    assert [r.url for r in out] == ["https://fine.example.com/z"]


def test_web_search_args_accept_multiple_queries():
    from app.tools.web_search import WebSearchArgs

    args = WebSearchArgs(queries=["one", "two"], exclude_domains=["x.com"], limit=5)
    assert args.queries == ["one", "two"]
    assert args.exclude_domains == ["x.com"]


# ---------- fetch_webpage: useful links + schema ----------

def test_page_content_has_links_field():
    p = PageContent(url="https://a.b", text="hello " * 20)
    assert p.links == []
    p2 = PageContent(url="https://a.b", links=[{"title": "x", "url": "https://a.b/c"}])
    assert p2.links[0]["url"] == "https://a.b/c"


def test_extract_links_skips_nav_social_deduplicates():
    html = (
        "<html><body><main>"
        "<a href='/docs'>Read the docs</a>"
        "<a href='https://docs.example.com'>Docs</a>"
        "<a href='https://facebook.com/share'>FB</a>"
        "<a href='#fragment'>anchor</a>"
        "<a href='/docs'>Read the docs again</a>"
        "</main></body></html>"
    )
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    links = _extract_links(soup, "https://example.com")
    urls = [l["url"] for l in links]
    assert "https://example.com/docs" in urls
    assert not any("facebook.com" in u for u in urls)
    assert len(urls) == len(set(urls))


# ---------- deep_research: hermetic bounded workflow ----------

@pytest.fixture()
def dr_tool():
    class FakeLLM:
        async def ainvoke(self, prompt):
            return type("R", (), {"content": "Synthesis answer citing [1] and [2]."})()

    class FakeSearch:
        async def __call__(self, query, limit=None):
            return [
                _sr("https://ok.example.com/1", f"{query} one"),
                _sr("https://ok.example.com/2", f"{query} two"),
            ]

    async def fake_fetch(url):
        return PageContent(url=url, title="Page", text="Useful readable content for evidence extraction. " * 20)

    async def fake_plan(query, prior):
        return ["alpha 2026", "beta"]

    return DeepResearchTool(
        search=FakeSearch(),
        fetch=fake_fetch,
        plan=fake_plan,
        answer_llm=lambda: FakeLLM(),
    )


@pytest.mark.asyncio
async def test_deep_research_structured_output(dr_tool):
    out = await dr_tool("Best vector store", max_iterations=2, max_sources=6)
    assert out["query"] == "Best vector store"
    assert out["decomposed_queries"], "query decomposition must produce queries"
    assert 1 <= out["iterations"] <= 2, "must be bounded"
    assert out["sources"], "must return citable sources"
    assert all(s["url"].startswith("https://") for s in out["sources"])
    assert "Synthesis answer" in out["answer"]


@pytest.mark.asyncio
async def test_deep_research_stops_early_when_enough(dr_tool):
    out = await dr_tool("A narrow question", max_iterations=3, max_sources=4)
    assert out["iterations"] <= 3
    assert len(out["sources"]) <= 4


@pytest.mark.asyncio
async def test_deep_research_respects_fetch_failures():
    """Fetch failures must degrade to the search snippet rather than crash."""

    class S:
        async def __call__(self, q, limit=None):
            return [_sr("https://snippet.example.com/a")]

    async def bad_fetch(url):
        raise RuntimeError("connection refused")

    tool = DeepResearchTool(
        search=S(),
        fetch=bad_fetch,
        plan=lambda q, p: ["x"],
        answer_llm=lambda: type("L", (), {"ainvoke": lambda self, p: None})(),
    )
    out = await tool("anything", max_iterations=1)
    assert out["sources"], "snippet fallback should still produce a citable source"
    assert out["sources"][0]["url"] == "https://snippet.example.com/a"