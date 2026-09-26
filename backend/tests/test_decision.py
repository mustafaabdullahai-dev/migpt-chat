"""Decision-analysis tool tests: full spec framework + registry + graph integration."""

from __future__ import annotations

import json

import pytest

from app.agents.routers.classifier import classify
from app.agents.routers.router import route_query
from app.agents.state import AgentState
from app.tools.base import registry
from app.tools.decision import build_decision_framework

_FULL_KEYS = {
    "decision", "options", "criteria", "constraints", "evidence", "trade_offs",
    "risks", "unknowns", "conditional_recommendations", "recommendation_rationale",
    "instruction",
}


def test_framework_has_full_spec_structure():
    fw = build_decision_framework(
        "Should I use PostgreSQL or MongoDB?",
        ["PostgreSQL", "MongoDB"],
        ["cost", "performance"],
    )
    assert set(fw) >= _FULL_KEYS
    assert fw["type"] == "decision_analysis"
    assert fw["decision"] == "Should I use PostgreSQL or MongoDB?"
    assert fw["options"] == ["PostgreSQL", "MongoDB"]
    assert fw["criteria"] == ["cost", "performance"]
    assert len(fw["conditional_recommendations"]) == 2


def test_framework_defaults_criteria_when_empty():
    fw = build_decision_framework("Which laptop?", ["MacBook Air", "ThinkPad"], [])
    assert fw["criteria"]


def test_framework_never_blind_pick():
    fw = build_decision_framework("Pick a db", ["A", "B"], ["cost"])
    assert "single winner" in fw["instruction"].lower()
    assert "conditional" in json.dumps(fw)
    assert fw["recommendation_rationale"]


def test_tool_registered_and_callable():
    spec = registry.get("decision_analysis")
    assert spec is not None and spec.enabled
    payload = spec.handler(
        question="Should I self-host or use a cloud platform?",
        options=["self-host", "cloud"],
        criteria=["cost"],
    )
    fw = json.loads(payload)
    assert fw["type"] == "decision_analysis"
    assert fw["options"] == ["self-host", "cloud"]


def test_classifier_routes_decisions():
    r = classify("Should I use FastAPI or Express for my next API?")
    assert r.intent == "DECISION_ANALYSIS"
    assert "decision_analysis" in r.tools
    assert route_query(AgentState(intent="DECISION_ANALYSIS")) == "plan"


@pytest.mark.asyncio
async def test_graph_builds_reasoning_scaffold(search_mock, graph):
    state = await graph.ainvoke({
        "query": "Should I use PostgreSQL or MongoDB for a high-traffic news site?",
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
    assert state.get("intent") == "DECISION_ANALYSIS"
    scaffold = state.get("reasoning_scaffold", "")
    assert scaffold and "decision_analysis" in scaffold
    evidence = state.get("evidence") or []
    assert any(e.url for e in evidence) or state.get("answer")