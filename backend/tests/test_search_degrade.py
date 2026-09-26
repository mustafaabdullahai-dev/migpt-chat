"""Unit tests for search query rewriting, de-escalation, and zero-evidence degradation."""

from __future__ import annotations

from app.agents.nodes.synthesize import _DYNAMIC_RE, needs_degrade
from app.tools.web_search import WebSearchTool


def test_rewrite_strips_conversational_filler():
    r = WebSearchTool._rewrite("What is the best database for RAG application?")
    assert r == "database for RAG application"


def test_rewrite_keeps_clean_query():
    assert WebSearchTool._rewrite("LangGraph new features 2026") == "LangGraph new features 2026"


def test_deescalate_sequence():
    q = "database for RAG application"
    assert WebSearchTool._deescalate(q, 1) == q
    assert WebSearchTool._deescalate(q, 2) == "database for RAG"
    assert WebSearchTool._deescalate(q, 3) == "database for RAG application"


def test_needs_degrade_true_when_research_evidence_empty():
    state = {"intent": "DEEP_RESEARCH", "evidence": [], "search_queries": ["min 2", "max 3"]}
    assert needs_degrade(state)


def test_needs_degrade_false_with_evidence():
    state = {"intent": "DECISION_ANALYSIS", "evidence": ["z"], "search_queries": ["x"]}
    assert not needs_degrade(state)


def test_needs_degrade_false_for_calculation():
    state = {"intent": "CALCULATION", "evidence": [], "calculation": {"result": 5}}
    assert not needs_degrade(state)


def test_dynamic_re_marks_live_queries():
    assert _DYNAMIC_RE.search("What was today's match score?")
    assert _DYNAMIC_RE.search("Show me the latest LangGraph release notes")
    assert not _DYNAMIC_RE.search("Explain how recursion works in Python")