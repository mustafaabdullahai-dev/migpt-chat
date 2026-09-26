"""Graph node: execute web searches in parallel."""

from __future__ import annotations

import asyncio

from app.agents.nodes.helpers import run_tool
from app.agents.state import AgentState
from app.core.config import settings
from app.models.schemas import SearchResult
from app.services.stream import emit, emit_status


def needs_papers(state: AgentState) -> bool:
    """Technical/academic queries should also pull arXiv papers, prioritized over web hits."""
    if not settings.enable_arxiv:
        return False
    if state.get("intent") == "ACADEMIC_RESEARCH":
        return True
    requires = state.get("requires_tools") or []
    return "arxiv" in requires


async def search_web(state: AgentState) -> dict:
    queries = state.get("search_queries", [])
    if not queries:
        queries = [state.get("query", "")]

    plural = "query" if len(queries) == 1 else "queries"
    emit_status(f"Searching the web ({len(queries)} {plural})…")

    # stagger start times slightly so DDG isn't hit with a simultaneous burst
    tasks = []
    for i, q in enumerate(queries):
        tasks.append(run_tool("web_search", query=q))
        if i < len(queries) - 1:
            await asyncio.sleep(0.35)

    # For academic/technical queries also search arXiv and PRIORITIZE papers.
    include_papers = needs_papers(state) and not (settings.is_demo or settings.search_provider == "mock")
    if include_papers:
        tasks.append(run_tool("arxiv", query=state.get("query", ""), limit=5))

    responses = await asyncio.gather(*tasks)

    results: list[SearchResult] = []
    records = []
    for payload, record in responses:
        records.append(record)
        if isinstance(payload, list):
            results.extend([r for r in payload if isinstance(r, SearchResult)])

    seen: set[str] = set()
    unique: list[SearchResult] = []
    for r in results:
        if r.url and r.url not in seen:
            seen.add(r.url)
            unique.append(r)

    # Tier-2 typo repair: nothing retrieved -> the query may be misspelled in a way
    # the deterministic pass and engine fuzz both missed. Ask the fast model to fix
    # it and re-search ONCE (bounded, only when the repair is meaningfully different).
    if settings.enable_query_repair and not unique:
        from app.services.typos import repair_query

        repaired = repair_query(state.get("query", ""))
        if repaired and repaired != state.get("query", ""):
            emit_status("No results — re-spelling your query…")
            fixed, rec = await run_tool("web_search", query=repaired)
            records.append(rec)
            for r in (fixed if isinstance(fixed, list) else []):
                if isinstance(r, SearchResult) and r.url and r.url not in seen:
                    seen.add(r.url)
                    unique.append(r)

    if include_papers:
        # Stable sort: arXiv papers first so the research loop privileges them.
        unique.sort(key=lambda r: 0 if r.source == "arxiv.org" else 1)

    emit("sources_meta", {"count": len(unique)})
    emit_status(f"Found {len(unique)} candidate sources.")

    return {
        "search_results": unique[: 12],
        "iterations": state.get("iterations", 0) + 1,
        "tool_runs": [r for r in records if r.status == "success"],
    }