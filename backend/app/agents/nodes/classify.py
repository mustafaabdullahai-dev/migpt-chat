"""Graph node: classify the incoming query."""

from __future__ import annotations

import asyncio

from app.agents.depth import research_depth
from app.agents.routers.classifier import classify
from app.agents.state import AgentState
from app.services.stream import emit_status

_RESEARCH_INTENTS = {
    "WEB_SEARCH", "DEEP_RESEARCH", "CURRENT_INFORMATION", "ACADEMIC_RESEARCH",
    "COMPARISON", "DECISION_ANALYSIS", "MULTI_TOOL", "SUMMARIZATION",
}


async def classify_query(state: AgentState) -> dict:
    query = state.get("query", "")
    mode = state.get("mode", "auto")
    has_docs = bool(state.get("documents"))

    emit_status("Understanding your question…")

    # Guess-ahead: start the raw-query search in the background while we classify
    # and plan, warming the shared cache so the research loop's first search becomes
    # an instant cache hit instead of a fresh network round-trip.
    from app.agents.nodes.speculative import is_web_plausible, warm_search

    if is_web_plausible(query, mode):
        asyncio.create_task(warm_search(query, mode))

    result = classify(query, has_documents=has_docs, force_mode=mode)

    intent = result.intent
    if mode == "deep" and intent not in ("DOCUMENT_QA", "CALCULATION", "DATETIME"):
        intent = "DEEP_RESEARCH"
    if mode == "no_web":
        intent = "DOCUMENT_QA" if has_docs else "CALCULATION" if result.intent == "CALCULATION" else "GENERAL_CHAT"
    if mode == "web" and intent in ("GENERAL_CHAT", "CODE"):
        # Web mode: web search is REQUIRED, except deterministic offline tools
        # (calulation/datetime) and local data the user already provided.
        intent = "WEB_SEARCH"

    tools = list(result.tools)
    if intent in ("WEB_SEARCH", "DEEP_RESEARCH", "CURRENT_INFORMATION", "COMPARISON", "DECISION_ANALYSIS", "MULTI_TOOL"):
        tools = [t for t in tools] + ["web_search"] if "web_search" not in tools else tools

    return {
        "intent": intent,
        "complexity": result.complexity,
        "needs_web": result.needs_web or mode in ("web", "deep"),
        "requires_tools": tools,
        "plan": [f"{intent} plan ready"],
        "iterations": 0,
        "depth": research_depth(intent, result.complexity, mode),
    }