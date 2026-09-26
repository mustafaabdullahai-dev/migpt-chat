"""Multi-tool agent tests (item 26): parallel orchestration, classifier/router, graph flow."""

from __future__ import annotations

import pytest

from app.agents.nodes.helpers import run_tool_plan
from app.agents.nodes.multi_tool import _pct_calc_steps, multi_tool_answer
from app.agents.routers.classifier import classify
from app.agents.routers.router import route_query
from app.agents.state import AgentState
import app.tools.registry  # noqa: F401  (populates the tool registry)


def test_pct_calc_steps_discovers_independent_calculations():
    steps = _pct_calc_steps("Calculate 20% of 800 and compare with 15% of 900")
    exprs = {s["expression"] for s in steps}
    assert exprs == {"(20/100)*800", "(15/100)*900"}


@pytest.mark.asyncio
async def test_run_tool_plan_parallel_group():
    results = await run_tool_plan([
        [
            {"name": "calculator", "expression": "(20/100)*800"},
            {"name": "calculator", "expression": "(15/100)*900"},
        ]
    ])
    assert len(results) == 2
    assert all(r.status == "success" for r in results)
    values = {round(r.payload.result) for r in results}
    assert values == {160, 135}


@pytest.mark.asyncio
async def test_run_tool_plan_sequential_then_error():
    results = await run_tool_plan([
        {"name": "calculator", "expression": "2+2"},
        {"name": "calculator", "expression": "1/0"},
    ])
    assert results[0].status == "success"
    assert results[0].payload.result == 4
    assert results[1].status == "error"


@pytest.mark.asyncio
async def test_multi_tool_node_runs_calculations_in_parallel():
    out = await multi_tool_answer(AgentState(query="Calculate 20% of 800 and compare with 15% of 900"))
    calcs = out["calculations"]
    assert len(calcs) == 2
    assert {round(c.result) for c in calcs} == {160, 135}
    assert out["calculation"] is not None
    assert out["tool_runs"]


def test_classify_frames_arithmetic_compare_as_multi_tool():
    result = classify("Calculate 20% of 800 and compare with 15% of 900")
    assert result.intent == "MULTI_TOOL"
    assert result.needs_web is False
    assert "calculator" in result.tools


def test_classify_named_options_multi_tool_needs_web():
    result = classify("Compare FastAPI vs Flask and show what 20% of their cost is")
    assert result.intent == "MULTI_TOOL"
    assert result.needs_web is True


def test_classify_pure_compare_stays_comparison():
    assert classify("Compare FastAPI and Flask for 2026").intent == "COMPARISON"


def test_classify_pure_calc_stays_calculation():
    assert classify("Calculate 27 times 391").intent == "CALCULATION"


def test_router_multi_tool_split():
    assert route_query(AgentState(intent="MULTI_TOOL", needs_web=False)) == "multi_tool"
    assert route_query(AgentState(intent="MULTI_TOOL", needs_web=True)) == "plan"


def test_evidence_sufficient_routes_multi_tool():
    from app.agents.nodes.evidence import evidence_sufficient

    assert evidence_sufficient(AgentState(evidence_ready=True, intent="MULTI_TOOL")) == "multi_tool"
    assert evidence_sufficient(AgentState(evidence_ready=True, intent="WEB_SEARCH")) == "synthesize"
    assert evidence_sufficient(AgentState(evidence_ready=False, intent="MULTI_TOOL")) == "plan"


@pytest.mark.asyncio
async def test_graph_compute_only_multi_tool(graph):
    state = await graph.ainvoke({
        "query": "Calculate 20% of 800 and compare with 15% of 900",
        "conversation_id": "test-conv",
        "user_id": "tester",
        "mode": "auto",
        "documents": [],
        "history": [],
        "intent": "",
        "confidence": 1.0,
        "errors": [],
        "tool_runs": [],
        "iterations": 0,
    })
    assert state.get("intent") == "MULTI_TOOL"
    calcs = state.get("calculations", [])
    assert len(calcs) == 2
    assert {round(c.result) for c in calcs} == {160, 135}
    assert state.get("answer")


@pytest.mark.asyncio
async def test_graph_web_multi_tool_runs_research_then_tools(search_mock, graph):
    state = await graph.ainvoke({
        "query": "Compare FastAPI vs Flask server setups and calculate 15% of 500 typical requests",
        "conversation_id": "test-conv",
        "user_id": "tester",
        "mode": "auto",
        "documents": [],
        "history": [],
        "intent": "",
        "confidence": 1.0,
        "errors": [],
        "tool_runs": [],
        "iterations": 0,
    })
    assert state.get("intent") == "MULTI_TOOL"
    assert state.get("evidence")
    assert state.get("calculations")
    assert state.get("reasoning_scaffold", "")  # comparison scaffold attached evidence
    assert state.get("answer")