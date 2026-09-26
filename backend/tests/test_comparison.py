"""Comparison-tool tests: option parsing, canonical criteria rows, scaffold integration."""

from __future__ import annotations

import json

import pytest

from app.agents.routers.classifier import classify
from app.agents.routers.router import after_retrieve, route_query
from app.agents.state import AgentState
from app.tools.base import registry
from app.tools.comparison import ComparisonTool, parse_options

EXPECTED_ROWS = [
    "architecture", "performance", "ecosystem", "learning curve",
    "production use", "community", "best use case",
]


def test_parse_options_vs_query():
    assert parse_options("Compare LangChain vs LlamaIndex vs Haystack.") == ["LangChain", "LlamaIndex", "Haystack"]
    assert parse_options("LangChain versus LlamaIndex") == ["LangChain", "LlamaIndex"]
    assert parse_options("difference between pandas vs polars") == ["difference between pandas", "polars"] or \
           parse_options("difference between pandas vs polars")[0].startswith("pandas")


def test_parse_options_returns_empty_without_separator():
    assert parse_options("Compare FastAPI and Flask for 2026") == []


def test_default_rows_include_community_and_best_use_case():
    fw = json.loads(ComparisonTool()(items=["LangChain", "LlamaIndex", "Haystack"]))
    assert fw["type"] == "comparison"
    assert fw["items"] == ["LangChain", "LlamaIndex", "Haystack"]
    assert fw["criteria"] == EXPECTED_ROWS


def test_instruction_requires_citations_and_exact_rows():
    fw = json.loads(ComparisonTool()(items=[]))
    assert "NUMBERED SOURCES" in fw["instruction"]
    assert "essential row" in fw["instruction"] or "exact order" in fw["instruction"]
    assert "never invent" in fw["instruction"]


def test_comparison_registered_and_callable():
    spec = registry.get("comparison")
    assert spec is not None and spec.enabled
    payload = spec.handler(items=["A", "B"])
    assert json.loads(payload)["items"] == ["A", "B"]


def test_classifier_and_router_for_comparison():
    assert classify("Compare FastAPI and Flask for 2026").intent == "COMPARISON"
    assert route_query(AgentState(intent="COMPARISON")) == "plan"


@pytest.mark.asyncio
async def test_graph_builds_comparison_scaffold_with_options(search_mock, graph):
    state = await graph.ainvoke({
        "query": "Compare FastAPI vs Flask for building web APIs",
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
    assert state.get("intent") == "COMPARISON"
    scaffold = state.get("reasoning_scaffold", "")
    assert "FastAPI" in scaffold and "Flask" in scaffold
    assert "community" in scaffold
    assert state.get("answer")