"""Graph workflow tests using mocked providers (demo mode, no external calls required to succeed)."""

from __future__ import annotations

import pytest


def _initial(query: str, mode: str = "auto") -> dict:
    return {
        "query": query,
        "conversation_id": "test-conv",
        "user_id": "tester",
        "mode": mode,
        "documents": [],
        "history": [],
        "intent": "",
        "confidence": 1.0,
        "errors": [],
        "tool_runs": [],
        "iterations": 0,
    }


@pytest.mark.asyncio
async def test_calculation_path(graph):
    state = await graph.ainvoke(_initial("Calculate 2 to the power 10 plus 5"))
    assert state.get("intent") == "CALCULATION"
    assert state.get("calculation") is not None
    assert "1029" in state.get("answer", "")


@pytest.mark.asyncio
async def test_calculation_percent_of(graph):
    state = await graph.ainvoke(_initial("Calculate 17.5% of 85000"))
    assert "14875" in state.get("answer", "")


@pytest.mark.asyncio
async def test_direct_answer_path(graph):
    state = await graph.ainvoke(_initial("What is LangGraph?"))
    assert state.get("intent") == "BACKGROUND_KNOWLEDGE"
    assert state.get("answer")


@pytest.mark.asyncio
async def test_background_path_builds_evidence(graph):
    state = await graph.ainvoke(_initial("What is the history of the Panama Canal?"))
    assert state.get("intent") == "BACKGROUND_KNOWLEDGE"
    assert state.get("evidence")
    assert state.get("answer")


@pytest.mark.asyncio
async def test_web_research_path_uses_plan(search_mock, graph):
    state = await graph.ainvoke(_initial("What is the best vector database?"))
    # mock search returns results; graph should still produce an answer
    assert state.get("answer")


@pytest.mark.asyncio
async def test_no_web_mode_skips_research(graph):
    state = await graph.ainvoke(_initial("What is FastAPI?", mode="no_web"))
    assert state.get("intent") in ("GENERAL_CHAT", "WEB_SEARCH", "COMPARISON", "DECISION_ANALYSIS")
    assert state.get("answer")