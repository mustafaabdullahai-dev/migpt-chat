"""Graph nodes: fetch top sources, analyze evidence, decide sufficiency."""

from __future__ import annotations

import asyncio
import json

from app.agents.depth import depth_budget
from app.agents.nodes.helpers import run_tool
from app.agents.state import AgentState
from app.core.config import settings
from app.models.schemas import Evidence, PageContent, SearchResult
from app.services.citations import dedupe_evidence, infer_quality, rank_evidence
from app.services.stream import emit, emit_source, emit_status

PAGE_TEXTS = 4
MAX_TEXT_PER_SOURCE = 1800
_FETCH_SEMAPHORE_LIMIT = 4


async def fetch_sources(state: AgentState) -> dict:
    results: list[SearchResult] = state.get("search_results", [])
    candidates = results[:PAGE_TEXTS]
    if not candidates:
        return {"fetched_pages": [], "evidence": []}

    emit_status(f"Reading top {len(candidates)} sources…")
    sem = asyncio.Semaphore(_FETCH_SEMAPHORE_LIMIT)

    async def fetch_one(url: str):
        async with sem:
            return await run_tool("fetch_webpage", url=url)

    responses = await asyncio.gather(*[fetch_one(r.url) for r in candidates])

    pages: list[PageContent] = []
    records = []
    source_map: dict[str, SearchResult] = {r.url: r for r in results}
    for candidate, (payload, record) in zip(candidates, responses):
        records.append(record)
        if isinstance(payload, dict) and "error" in payload:
            # fetch failed: fall back to the search snippet as weak evidence
            pages.append(
                PageContent(url=candidate.url, title=candidate.title, text=candidate.snippet, headings=[])
            )
            continue
        if isinstance(payload, PageContent):
            pages.append(payload)

    evidence: list[Evidence] = []
    for page in pages:
        meta = source_map.get(page.url)
        snippet = meta.snippet if meta else ""
        text = page.text or snippet
        if not text:
            continue
        evidence.append(
            Evidence(
                source_id=page.url,
                title=page.title or (meta.title if meta else page.url),
                url=page.url,
                snippet=snippet[:400],
                relevant_text=text[:MAX_TEXT_PER_SOURCE],
                source_type="webpage",
                source_quality=infer_quality(page.url, page.title),
                confidence=0.7,
            )
        )

    for ev in evidence:
        emit_source({
            "source_id": ev.source_id,
            "title": ev.title,
            "url": ev.url,
            "snippet": ev.snippet[:200] if ev.snippet else "",
            "quality": ev.source_quality,
            "category": ev.source_type,
        })
    emit("sources", [
        {
            "source_id": ev.source_id,
            "title": ev.title,
            "url": ev.url,
            "snippet": ev.snippet[:200] if ev.snippet else "",
            "quality": ev.source_quality,
            "category": ev.source_type,
        }
        for ev in evidence
    ])
    emit("sources_found", {"count": len(evidence)})
    return {
        "fetched_pages": pages,
        "evidence": evidence,
        "tool_runs": [r for r in records if r.status == "success"],
    }


async def analyze_evidence(state: AgentState) -> dict:
    evidence = dedupe_evidence(state.get("evidence", []))
    ranked = rank_evidence(evidence)
    iterations = state.get("iterations", 0)
    budget = depth_budget(state)

    emit_status(f"Analyzing {len(ranked)} sources…")

    scaffold_output: dict = {}
    if not state.get("reasoning_scaffold"):
        if state.get("intent") in ("COMPARISON", "DECISION_ANALYSIS", "MULTI_TOOL"):
            emit_status("Comparing evidence…")
        scaffold = await _build_scaffold(state, evidence)
        if scaffold:
            scaffold_output = {"reasoning_scaffold": scaffold}

    enough = len(ranked) >= budget["max_sources"] or iterations >= budget["max_iterations"]
    if not enough and settings.enable_web_search:
        emit_status("Gathering additional evidence…")
    return {
        "evidence": ranked,
        "evidence_ready": enough,
        "iterations": iterations,
        "depth": budget["depth"],
        **scaffold_output,
    }


async def _build_scaffold(state: AgentState, evidence: list) -> str | None:
    """For structured-reasoning intents, emit a reasoning scaffold (decision/comparison)
    the answer step can fill with the gathered evidence."""
    intent = state.get("intent", "")
    query = state.get("query", "")
    if intent == "DECISION_ANALYSIS":
        payload, record = await run_tool(
            "decision_analysis",
            question=query,
            options=[],
            criteria=[],
        )
    elif intent == "COMPARISON":
        from app.tools.comparison import parse_options

        items = parse_options(query) or []
        payload, record = await run_tool("comparison", items=items, criteria=[])
    else:
        return None
    if record.status != "success" or not isinstance(payload, str):
        return None
    try:
        scaffold = json.loads(payload)
    except json.JSONDecodeError:
        return payload
    # attach the evidence we already have so the framework is not empty
    scaffold["evidence"] = [
        {"title": e.title, "url": e.url, "snippet": (e.snippet or e.relevant_text or "")[:300]}
        for e in evidence[:8]
    ]
    return json.dumps(scaffold, indent=2)


def evidence_sufficient(state: AgentState) -> str:
    if state.get("evidence_ready"):
        if state.get("intent") == "MULTI_TOOL":
            return "multi_tool"  # run computation/comparison before synthesizing
        return "synthesize"
    return "plan"  # loop: plan refined follow-up searches