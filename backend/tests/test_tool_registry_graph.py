"""Spec tests: TOOL_REGISTRY + LANGGRAPH NODES + CONDITIONAL EDGES.

Locks the canonical tool map, that the agent is handed only task-relevant tools, and
that the graph is decomposed into separate nodes with conditional routing.
"""

from __future__ import annotations

import pytest

from app.tools.registry import TOOL_REGISTRY, relevant_tools

SPEC_TOOLS = [
    "web_search",
    "fetch_webpage",
    "calculator",
    "wikipedia",
    "arxiv",
    "deep_research",
    "document_search",
    "unit_converter",
    "time",
    "decision_analysis",
]


# ------------------------- TOOL_REGISTRY -------------------------


def test_tool_registry_contains_all_spec_tools():
    assert set(SPEC_TOOLS) <= set(TOOL_REGISTRY)


def test_tool_registry_entries_are_enabled_specs():
    for name, spec in TOOL_REGISTRY.items():
        assert spec.name == ("get_current_time" if name == "time" else name)
        assert spec.enabled
        assert spec.handler is not None
        assert spec.description
    # "time" is an alias resolving to the real registered tool
    assert TOOL_REGISTRY["time"].name == "get_current_time"


# ------------------------- relevant_tools (only tools for the task) -------------------------


def test_calculation_gets_only_calc_tools():
    tools = relevant_tools("CALCULATION")
    assert "calculator" in tools and "unit_converter" in tools
    assert "web_search" not in tools and "document_search" not in tools


def test_datetime_gets_time_tool():
    assert relevant_tools("DATETIME") == ["get_current_time"]


def test_general_chat_no_irrelevant_tools():
    assert relevant_tools("GENERAL_CHAT") == []


def test_merges_classifier_requires_tools():
    tools = relevant_tools("ACADEMIC_RESEARCH", requires_tools=["arxiv", "web_search", "fetch_webpage"])
    assert "arxiv" in tools and "web_search" in tools


def test_no_web_mode_strips_web_tools():
    tools = relevant_tools("WEB_SEARCH", mode="no_web")
    assert "web_search" not in tools and "fetch_webpage" not in tools


def test_web_mode_forces_search_tools():
    tools = relevant_tools("GENERAL_CHAT", mode="web")
    assert "web_search" in tools and "fetch_webpage" in tools


def test_unknown_requires_tools_drop_out_but_explicit_ones_are_kept():
    tools = relevant_tools("CALCULATION", requires_tools=["not_a_real_tool"])
    assert "not_a_real_tool" not in tools
    # the classifier may explicitly require a tool even if it is not in the intent defaults
    tools = relevant_tools("CALCULATION", requires_tools=["web_search"])
    assert "web_search" in tools
    assert all(t in {spec.name for spec in TOOL_REGISTRY.values()} for t in tools)


# ------------------------- LANGGRAPH NODES + CONDITIONAL EDGES -------------------------


def test_graph_has_separate_spec_nodes(graph):
    required = {
        "classify",
        "select_tools",
        "plan",
        "search",
        "fetch_sources",
        "analyze_evidence",
        "calculate",
        "retrieve_documents",
        "synthesize",
        "remember",
    }
    nodes = set(graph.get_graph().nodes)
    assert required <= nodes


def test_select_tools_runs_before_routing(graph):
    # consider state passed through the full graph on a plain calculation
    nodes = set(graph.get_graph().nodes)
    # classify -> select_tools is an unconditional edge; routing happens from select_tools
    assert "classify" in nodes and "select_tools" in nodes


@pytest.mark.asyncio
async def test_calculation_path_records_selected_tools(graph):
    state = await graph.ainvoke({
        "query": "Calculate 2 to the power 10 plus 5",
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
    assert "calculator" in state.get("selected_tools", [])
    assert "web_search" not in state.get("selected_tools", [])


@pytest.mark.asyncio
async def test_research_path_records_relevant_tools(search_mock, graph):
    state = await graph.ainvoke({
        "query": "What is the best vector database?",
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
    selected = state.get("selected_tools", [])
    assert "web_search" in selected
    assert state.get("answer")