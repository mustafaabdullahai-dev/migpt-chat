"""Graph node: encyclopedic background research for definitions/history/intro questions.

Uses the Wikipedia tool for structured background (intros + key sections) and makes a
best-effort attempt to also pull in a NON-Wikipedia primary/official source so the answer
can contrast them. Wikipedia evidence is tagged source_type="encyclopedia" (secondary);
the synthesizer and citations layer label it as such.
"""

from __future__ import annotations

from app.agents.nodes.helpers import run_tool
from app.agents.state import AgentState
from app.core.config import settings
from app.models.schemas import Evidence, SearchResult
from app.services.citations import infer_quality
from app.services.stream import emit, emit_source, emit_status


def _is_wikipedia_url(url: str) -> bool:
    return "wikipedia.org" in url or "wikimedia.org" in url


def _to_evidence(title: str, url: str, text: str, snippet: str, source_type: str) -> Evidence:
    return Evidence(
        source_id=url,
        title=title or url,
        url=url,
        snippet=(snippet or "")[:300],
        relevant_text=text[:2200],
        source_type=source_type,
        source_quality=infer_quality(url, title),
        confidence=0.7,
    )


def _wiki_page_evidence(page: dict | None, snippet: str) -> Evidence | None:
    if not page or not page.get("title"):
        return None
    text = page.get("intro", "")
    sections = page.get("sections", [])
    categories = page.get("categories", [])
    if sections:
        text += "\n\nKey sections: " + ", ".join(sections)
    if categories:
        text += "\nCategories: " + ", ".join(categories)
    if page.get("last_edited"):
        text += f"\nLast updated on Wikipedia: {page['last_edited'][:10]}"
    return _to_evidence(page["title"], page["url"], text, snippet, source_type="encyclopedia")


async def background_research(state: AgentState) -> dict:
    query = state.get("query", "")
    evidence: list[Evidence] = []
    records = []
    pages = []

    # Fully offline path (tests / DEMO): use the canned provider only.
    if settings.is_demo or settings.search_provider == "mock":
        from app.services.search import MockSearchProvider

        results = await MockSearchProvider().search(query, 4)
        for r in results[:3]:
            evidence.append(
                _to_evidence(r.title, r.url, r.snippet, r.snippet, source_type="webpage")
            )
        emit("sources_found", {"count": len(evidence)})
        return {
            "evidence": evidence,
            "fetched_pages": pages,
            "tool_runs": records,
            "iterations": 1,
        }

    emit_status("Looking up background references (Wikipedia + primary sources)…")

    # 1) Wikipedia article search — the encyclopedic backbone of this path.
    wiki_results, rec = await run_tool("wikipedia", query=query, mode="search", limit=5)
    records.append(rec)
    wiki_list = [r for r in wiki_results if isinstance(r, SearchResult)] if isinstance(wiki_results, list) else []

    # 2) Full intro + sections of the best-matching article.
    page_evidence = None
    if wiki_list:
        best = wiki_list[0]
        page, rec_page = await run_tool("wikipedia", query=best.title, mode="page")
        records.append(rec_page)
        page_evidence = _wiki_page_evidence(page if isinstance(page, dict) else None, best.snippet)
        if page_evidence:
            evidence.append(page_evidence)

    # 3) Best-effort PRIMARY-source contrast: a non-Wikipedia fetch from web search.
    web_search_q = query
    web_results, rec_web = await run_tool("web_search", query=web_search_q, limit=6)
    records.append(rec_web)
    if isinstance(web_results, list):
        for r in web_results:
            if not isinstance(r, SearchResult) or _is_wikipedia_url(r.url):
                continue
            fetched, rec_fetch = await run_tool("fetch_webpage", url=r.url)
            records.append(rec_fetch)
            if isinstance(fetched, dict) and "error" in fetched:
                evidence.append(_to_evidence(r.title, r.url, r.snippet, r.snippet, source_type="webpage"))
            elif hasattr(fetched, "text"):
                evidence.append(
                    _to_evidence(
                        getattr(fetched, "title", None) or r.title,
                        r.url,
                        fetched.text,
                        r.snippet,
                        source_type="webpage",
                    )
                )
                pages.append(fetched)
            break  # one contrasting primary/non-Wikipedia source is enough

    # 4) Snippet-level evidence for the remaining Wikipedia hits.
    seen = {e.url for e in evidence}
    for r in wiki_list:
        if r.url in seen:
            continue
        seen.add(r.url)
        evidence.append(
            _to_evidence(r.title, r.url, r.snippet, r.snippet, source_type="encyclopedia")
        )

    for ev in evidence:
        emit_source({
            "source_id": ev.source_id,
            "title": ev.title,
            "url": ev.url,
            "snippet": ev.snippet[:200],
            "quality": ev.source_quality,
            "category": ev.source_type,
        })
    emit("sources", [
        {
            "source_id": ev.source_id,
            "title": ev.title,
            "url": ev.url,
            "snippet": ev.snippet[:200],
            "quality": ev.source_quality,
            "category": ev.source_type,
        }
        for ev in evidence
    ])
    emit("sources_found", {"count": len(evidence)})
    return {
        "evidence": evidence,
        "fetched_pages": pages,
        "tool_runs": [r for r in records if r.status == "success"],
        "iterations": 1,
    }