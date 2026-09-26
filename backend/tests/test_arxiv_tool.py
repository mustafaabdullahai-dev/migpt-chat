"""Unit tests for the arXiv research-paper tool (spec shape, parsing, graph hook)."""

from __future__ import annotations

import pytest

from app.agents.nodes.search import needs_papers
from app.models.schemas import SearchResult
from app.tools.arxiv import clean_query, parse_entries, search_arxiv, ArxivTool

_SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v1</id>
    <title>Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks</title>
    <published>2024-01-20T00:00:00Z</published>
    <author><name>Patrick Lewis</name></author>
    <author><name>Ethan Perez</name></author>
    <summary>We explore a general-purpose fine-tuning recipe for retrieval-augmented generation.</summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2402.99999v2</id>
    <title>Survey of Agentic AI Architectures</title>
    <published>2024-02-10T12:30:00Z</published>
    <author><name>Ada Lovelace</name></author>
    <summary>A comprehensive survey of agentic AI system designs.</summary>
  </entry>
</feed>
"""


def test_parse_entries_returns_spec_shape():
    papers = parse_entries(_SAMPLE_FEED)
    assert len(papers) == 2
    paper = papers[0]
    assert set(paper) == {"title", "authors", "abstract", "published", "url"}
    assert paper["title"] == "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
    assert paper["authors"] == ["Patrick Lewis", "Ethan Perez"]
    assert "fine-tuning" in paper["abstract"]
    assert paper["published"] == "2024-01-20"
    assert paper["url"] == "http://arxiv.org/abs/2401.12345v1"


def test_parse_entries_handles_empty_feed():
    assert parse_entries('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"/>') == []


def test_clean_query_sanitizes_and_caps_length():
    assert clean_query("Transformer  (attention) & NLP!:") == "Transformer attention NLP"
    assert len(clean_query("a" * 5000)) <= 400


@pytest.mark.asyncio
async def test_search_arxiv_returns_paper_dicts(monkeypatch):
    import httpx

    async def fake_get(url, params):
        assert params["sortBy"] == "relevance"
        assert params["max_results"] == 2
        return httpx.Response(200, text=_SAMPLE_FEED, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.tools.arxiv.httpx.AsyncClient", _FakeClient(fake_get))
    papers = await search_arxiv("RAG survey", limit=2)
    assert set(papers[0]) == {"title", "authors", "abstract", "published", "url"}
    assert papers[0]["url"].startswith("http://arxiv.org/")


@pytest.mark.asyncio
async def test_arxiv_tool_call_builds_search_results(monkeypatch):
    import app.tools.arxiv as arxiv_mod

    async def stub(*a, **k):
        return parse_entries(_SAMPLE_FEED)

    monkeypatch.setattr(arxiv_mod, "search_arxiv", stub)
    tool = ArxivTool()
    results = await tool("RAG survey", limit=2)
    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].source == "arxiv.org"
    assert results[0].url == "http://arxiv.org/abs/2401.12345v1"
    assert "Patrick Lewis" in results[0].snippet


@pytest.mark.asyncio
async def test_arxiv_tool_returns_empty_on_error(monkeypatch):
    import app.tools.arxiv as arxiv_mod

    async def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(arxiv_mod, "search_arxiv", boom)
    tool = ArxivTool()
    assert await tool("query") == []


def test_needs_papers_true_for_academic_intent_and_tools():
    assert needs_papers({"intent": "ACADEMIC_RESEARCH", "requires_tools": []})
    assert needs_papers({"intent": "WEB_SEARCH", "requires_tools": ["arxiv"]})


def test_needs_papers_false_for_others():
    assert not needs_papers({"intent": "WEB_SEARCH", "requires_tools": []})
    assert not needs_papers({"intent": "", "requires_tools": None})


class _FakeClient:
    """Instantiates like httpx.AsyncClient(timeout=...); __aenter__ yields itself."""

    def __init__(self, getter):
        self._getter = getter

    def __call__(self, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, **kw):
        return await self._getter(url, params or {})