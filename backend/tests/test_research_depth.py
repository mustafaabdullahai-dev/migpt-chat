"""Spec tests: RESEARCH DEPTH (quick/standard/deep, configurable limits) + mode routing."""

from __future__ import annotations

import pytest

from app.agents.depth import depth_budget, research_depth
from app.agents.nodes.plan import _query_count
from app.core.config import settings
from app.models.schemas import Evidence


# ------------------------- depth tier selection -------------------------


def test_research_depth_matrix():
    assert research_depth("WEB_SEARCH", "low", "auto") == "quick"
    assert research_depth("CURRENT_INFORMATION", "low", "auto") == "quick"
    assert research_depth("BACKGROUND_KNOWLEDGE", "low", "auto") == "quick"
    # explicit web mode / normal complexity -> standard
    assert research_depth("WEB_SEARCH", "high", "auto") == "standard"
    assert research_depth("WEB_SEARCH", "low", "web") == "standard"
    # deep research + academic intents + deep mode -> deep
    assert research_depth("DEEP_RESEARCH", "low", "auto") == "deep"
    assert research_depth("ACADEMIC_RESEARCH", "medium", "auto") == "deep"
    # general chat carries a web budget in deep mode (default web-backed research)
    assert research_depth("GENERAL_CHAT", "low", "deep") == "deep"
    # offline / non-research intents never get the deep budget
    assert research_depth("CALCULATION", "low", "auto") == "standard"
    assert research_depth("DOCUMENT_QA", "medium", "auto") == "standard"
    assert research_depth("CODE", "low", "auto") == "standard"


# ------------------------- configurable budget per tier -------------------------


def test_depth_budget_quick_bounds(monkeypatch):
    monkeypatch.setattr(settings, "research_quick_max_iterations", 1)
    monkeypatch.setattr(settings, "research_quick_max_sources", 3)
    monkeypatch.setattr(settings, "research_quick_max_searches", 5)
    b = depth_budget({"intent": "WEB_SEARCH", "complexity": "low", "mode": "auto"})
    assert b["depth"] == "quick"
    assert b["max_iterations"] == 1          # no research loops in quick
    assert b["max_sources"] == 3
    assert b["first_queries"] <= 5           # bounded by the search cap
    assert b["followup_queries"] == 0


def test_depth_budget_standard_stays_within_searches(monkeypatch):
    monkeypatch.setattr(settings, "research_standard_max_searches", 6)
    b = depth_budget({"depth": "standard"})
    assert b["depth"] == "standard"
    assert 1 <= b["first_queries"] <= 3
    total = b["first_queries"] + b["followup_queries"]
    assert 3 <= total <= 6                   # spec: standard = 3-6 searches


def test_depth_budget_deep(monkeypatch):
    monkeypatch.setattr(settings, "research_deep_max_iterations", 3)
    monkeypatch.setattr(settings, "research_deep_max_sources", 8)
    monkeypatch.setattr(settings, "research_deep_first_queries", 4)
    monkeypatch.setattr(settings, "research_deep_followup_queries", 3)
    b = depth_budget({"depth": "deep"})
    assert b["max_iterations"] == 3          # additional search loops
    assert b["max_sources"] == 8
    assert b["first_queries"] >= b["followup_queries"] > 0


def test_depth_budget_falls_back_from_state():
    assert depth_budget({})["depth"] == "standard"   # given no depth/intent
    assert depth_budget({"depth": "quick"})["depth"] == "quick"


# ------------------------- planner query counts -------------------------


def test_query_count_quick_is_single_small_pass(monkeypatch):
    monkeypatch.setattr(settings, "research_quick_max_searches", 3)
    assert 1 <= _query_count({"intent": "WEB_SEARCH", "complexity": "low", "mode": "auto", "iterations": 0}) <= 3
    # quick never runs follow-up passes
    assert _query_count({"intent": "WEB_SEARCH", "complexity": "low", "mode": "auto", "iterations": 2, "evidence": [1]}) <= 3


def test_query_count_deep_decomposes_and_loops():
    assert _query_count({"intent": "DEEP_RESEARCH", "complexity": "low", "mode": "auto", "iterations": 0}) >= 4
    deep_loop = _query_count({"intent": "DEEP_RESEARCH", "complexity": "low", "mode": "auto", "iterations": 1})
    assert 1 <= deep_loop <= 3  # gap-aware follow-up pass
    assert _query_count({"mode": "deep", "complexity": "low", "iterations": 0}) >= 4


def test_query_count_standard_is_bounded():
    q = _query_count({"intent": "WEB_SEARCH", "complexity": "high", "mode": "auto", "iterations": 0})
    assert 1 <= q <= 3
    follow = _query_count({"intent": "WEB_SEARCH", "complexity": "high", "mode": "auto", "iterations": 1, "evidence": [1]})
    assert 0 <= follow <= q


# ------------------------- classify node wiring -------------------------


@pytest.mark.asyncio
async def test_classify_sets_depth_and_web_mode_upgrades():
    from app.agents.nodes.classify import classify_query

    out = await classify_query({"query": "hello there", "mode": "web"})
    assert out["intent"] == "WEB_SEARCH"          # web mode requires search
    assert out["needs_web"] is True
    assert out["depth"] == "standard"

    code = await classify_query({"query": "write a merge sort", "mode": "web"})
    assert code["intent"] == "WEB_SEARCH"


@pytest.mark.asyncio
async def test_classify_deep_mode_forces_deep_research():
    from app.agents.nodes.classify import classify_query

    out = await classify_query({"query": "tell me about transformers", "mode": "deep"})
    assert out["intent"] == "DEEP_RESEARCH"
    assert out["depth"] == "deep"

    calc = await classify_query({"query": "2+2", "mode": "deep"})
    assert calc["depth"] == "standard"            # offline tools unaffected


@pytest.mark.asyncio
async def test_classify_no_web_disables_search():
    from app.agents.nodes.classify import classify_query

    out = await classify_query({"query": "latest news", "mode": "no_web"})
    assert out["intent"] == "GENERAL_CHAT"        # external search disabled
    assert out["needs_web"] is False


# ------------------------- evidence loop honors the tier budget -------------------------


@pytest.mark.asyncio
async def test_analyze_evidence_quick_budget_stops(monkeypatch):
    from app.agents.nodes.evidence import analyze_evidence

    monkeypatch.setattr(settings, "research_quick_max_iterations", 1)
    monkeypatch.setattr(settings, "research_quick_max_sources", 6)
    state = {"intent": "WEB_SEARCH", "mode": "auto", "complexity": "low", "depth": "quick",
             "iterations": 1, "evidence": [Evidence(source_id=f"s{i}") for i in range(2)]}
    out = await analyze_evidence(state)
    assert out["evidence_ready"] is True          # iterations hit the quick cap


@pytest.mark.asyncio
async def test_analyze_evidence_deep_keeps_looping(monkeypatch):
    from app.agents.nodes.evidence import analyze_evidence

    monkeypatch.setattr(settings, "research_deep_max_iterations", 3)
    monkeypatch.setattr(settings, "research_deep_max_sources", 8)
    state = {"intent": "DEEP_RESEARCH", "mode": "auto", "complexity": "low", "depth": "deep",
             "iterations": 1, "evidence": [Evidence(source_id=f"s{i}") for i in range(2)]}
    out = await analyze_evidence(state)
    assert out["evidence_ready"] is False         # budget allows more passes


# ------------------------- classifier doc phrases (spec §46 examples) -------------------------


def test_methodology_phrase_classifies_as_document_qa():
    from app.agents.routers.classifier import classify

    r = classify("Find the methodology used in this paper.", has_documents=True)
    assert r.intent == "DOCUMENT_QA"


def test_summarize_this_with_docs_uses_rag():
    from app.agents.routers.classifier import classify

    r = classify("Summarize this.", has_documents=True)
    assert r.intent == "SUMMARIZATION"
    assert "document_search" in r.tools