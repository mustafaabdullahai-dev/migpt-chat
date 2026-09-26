"""Smart-router + intent-classifier acceptance tests (spec items 24/25)."""

from __future__ import annotations

from app.agents.routers.classifier import classify
from app.agents.routers.router import route_query
from app.agents.state import AgentState


def test_direct_answer():
    assert route_query(AgentState(intent="GENERAL_CHAT")) == "synthesize"


def test_calculation():
    assert route_query(AgentState(intent="CALCULATION")) == "calculate"


def test_document_retrieval():
    assert route_query(AgentState(intent="DOCUMENT_QA", documents=["d1"])) == "retrieve_documents"


def test_deep_research_plan():
    assert route_query(AgentState(intent="DEEP_RESEARCH")) == "plan"
    assert route_query(AgentState(intent="WEB_SEARCH")) == "plan"
    assert route_query(AgentState(intent="ACADEMIC_RESEARCH")) == "plan"


def test_data_analysis_uses_documents():
    assert route_query(AgentState(intent="DATA_ANALYSIS", documents=["d1"])) == "retrieve_documents"


def test_multi_tool_routing_depends_on_web_need():
    # compute-only multi-tool -> dedicated node; research-family -> research loop
    assert route_query(AgentState(intent="MULTI_TOOL", needs_web=False)) == "multi_tool"
    assert route_query(AgentState(intent="MULTI_TOOL", needs_web=True)) == "plan"


def test_classify_python_direct():
    assert classify("What is Python?").intent == "BACKGROUND_KNOWLEDGE"
    assert classify("What is Python?").needs_web is True  # wikipedia-backed background


def test_classify_latest_version_needs_web():
    result = classify("What is Python's latest version?")
    assert result.intent == "CURRENT_INFORMATION"
    assert result.needs_web is True


def test_classify_calculation():
    assert classify("Calculate 27 times 391").intent == "CALCULATION"


def test_classify_comparison():
    assert classify("Compare FastAPI and Flask for 2026").intent == "COMPARISON"


def test_classify_summarize_pdf_with_docs():
    assert classify("Summarize this PDF", has_documents=True).intent == "SUMMARIZATION"


def test_classify_data_analysis():
    result = classify("Analyze this CSV to find patterns", has_documents=True)
    assert result.intent == "DATA_ANALYSIS"
    assert "python_analysis" in result.tools


def test_classify_deep_research():
    result = classify("Research the latest LangGraph architecture in depth")
    assert result.intent == "DEEP_RESEARCH"
    assert result.needs_web is True


def test_lightweight_classification_is_deterministic():
    # simple questions resolve via the cheap heuristic without any LLM call
    assert classify("Calculate 27 times 391").intent == "CALCULATION"
    assert classify("What is today's date?").intent == "DATETIME"