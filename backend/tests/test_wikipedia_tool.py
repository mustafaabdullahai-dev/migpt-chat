"""Tests for the Wikipedia search/retrieval tool, authority labels, and background node."""

from __future__ import annotations

import pytest

from app.agents.nodes.wikipedia import _wiki_page_evidence, background_research
from app.agents.nodes.helpers import run_tool
from app.models.schemas import Evidence, PageContent, SearchResult, ToolRun
from app.services.citations import (
    build_citation_block,
    infer_quality,
    source_authority,
    source_label,
)
from app.tools.wikipedia import IS_SECONDARY, _parse_page, _parse_search, article_url

SAMPLE_SEARCH = {
    "query": {
        "search": [
            {"title": "Python (programming language)", "extract": "  Python is a high-level\n  general-purpose language. "},
            {"title": "Python (mythology)", "extract": "Python was a serpent in Greek myth."},
        ]
    }
}

SAMPLE_PAGE = {
    "title": "Python (programming language)",
    "extract": "Python is a high-level, general-purpose programming language. Its design philosophy "
               "emphasizes code readability with the use of significant indentation.",
    "sections": [
        {"indole": "1", "title": "History"},
        {"indole": "2", "title": "Design philosophy"},
        {"indole": "3", "title": "Features and philosophy"},
    ],
    "categories": [
        {"title": "Category:Python (programming language)"},
        {"title": "Category:Programming languages created in 1991"},
    ],
    "info": {"touched": "2023-05-01T12:00:00Z"},
}


def test_article_url_escapes_title():
    assert article_url("Python (programming language)") == (
        "https://en.wikipedia.org/wiki/Python_%28programming_language%29"
    )


def test_parse_search_produces_clean_results():
    results = _parse_search(SAMPLE_SEARCH, limit=10)
    assert len(results) == 2
    r = results[0]
    assert r.title == "Python (programming language)"
    assert r.source == "en.wikipedia.org"
    assert r.snippet == "Python is a high-level general-purpose language."
    assert "en.wikipedia.org" in r.url


def test_parse_search_respects_limit():
    assert len(_parse_search(SAMPLE_SEARCH, limit=1)) == 1


def test_parse_page_structures_background_dict():
    page = _parse_page(SAMPLE_PAGE)
    assert page["title"] == "Python (programming language)"
    assert page["url"].startswith("https://en.wikipedia.org/wiki/")
    assert "high-level" in page["intro"]
    assert page["sections"][0] == "History"
    assert page["categories"] == ["Python (programming language)", "Programming languages created in 1991"]
    assert page["last_edited"] == "2023-05-01T12:00:00Z"
    assert page["is_secondary"] is IS_SECONDARY is True


def test_wiki_page_evidence_is_marked_secondary_encyclopedia():
    ev = _wiki_page_evidence(_parse_page(SAMPLE_PAGE), snippet="s")
    assert isinstance(ev, Evidence)
    assert ev.source_type == "encyclopedia"
    assert ev.source_quality == "wikipedia"
    assert "History" in ev.relevant_text
    assert "secondary" in source_label(ev.source_quality)

    assert _wiki_page_evidence(None, "s") is None


# ---------- citation authority: Wikipedia vs primary sources ----------

def test_source_authority_mapping():
    assert source_authority("wikipedia") == "secondary"
    assert source_authority("official documentation") == "primary"
    assert source_authority("government") == "primary"
    assert source_authority("academic") == "primary"
    assert source_authority("community discussion") == "community"
    assert source_authority("nope") == "unknown"


def test_source_label_wikipedia_is_explicit_secondary():
    label = source_label("wikipedia")
    assert "encyclopedia" in label and "secondary" in label


def test_citation_block_annotates_source_type():
    from app.models.schemas import Citation

    block = build_citation_block(
        [
            Citation(index=1, url="https://docs.python.org/3/", title="Python docs", quality="official documentation"),
            Citation(index=2, url="https://en.wikipedia.org/wiki/Python_(programming_language)", title="Python (programming language)", quality="wikipedia"),
        ]
    )
    assert "[1] Python docs" in block
    assert "Type: official documentation (primary)" in block
    assert "[2] Python (programming language)" in block
    assert "Type: encyclopedia (secondary reference)" in block


def test_infer_quality_flags_wikipedia():
    assert infer_quality("https://en.wikipedia.org/wiki/Panama_Canal") == "wikipedia"
    assert infer_quality("https://www.nationalarchives.gov.uk/records/") == "government"


# ---------- background_research node (offline, tool mocked) ----------

async def _fake_run_tool(name: str, **kwargs):
    if name == "wikipedia" and kwargs.get("mode") == "search":
        payload = [SearchResult(title="Python (programming language)", url="https://en.wikipedia.org/wiki/Python_(programming_language)", snippet="Python is a language.")]
    elif name == "wikipedia" and kwargs.get("mode") == "page":
        payload = _parse_page(SAMPLE_PAGE)
    elif name == "web_search":
        payload = [SearchResult(title="Python docs", url="https://docs.python.org/3/", snippet="Official docs.")]
    elif name == "fetch_webpage":
        payload = PageContent(url="https://docs.python.org/3/", title="Python docs", text="Official Python documentation — the authoritative reference.")
    else:
        payload = []
    return payload, ToolRun(name=name, status="success")


@pytest.mark.asyncio
async def test_background_node_marks_wikipedia_secondary_and_primary(monkeypatch):
    from types import SimpleNamespace

    import app.agents.nodes.wikipedia as node

    # Force the real (web) path: stub the module-level settings, bypassing the demo short-circuit.
    monkeypatch.setattr(
        node, "settings",
        SimpleNamespace(is_demo=False, search_provider="duckduckgo", enable_web_search=True),
    )
    monkeypatch.setattr(node, "run_tool", _fake_run_tool)

    state = {"query": "What is Python?"}
    out = await background_research(state)

    evidence = out["evidence"]
    assert len(evidence) >= 2
    wiki_ev = [e for e in evidence if e.source_type == "encyclopedia"]
    primary_ev = [e for e in evidence if e.source_type == "webpage"]
    assert wiki_ev and primary_ev
    assert wiki_ev[0].source_quality == "wikipedia"
    assert source_authority(wiki_ev[0].source_quality) == "secondary"
    assert "docs.python.org" in primary_ev[0].url
    assert out["iterations"] == 1