"""Unit tests for the query classifier / router."""

from __future__ import annotations

import pytest

from app.agents.routers.classifier import classify
from app.agents.routers.router import route_query
from app.agents.state import AgentState


@pytest.mark.parametrize(
    "query,expected_intent",
    [
        ("What is Python?", "BACKGROUND_KNOWLEDGE"),
        ("What is the history of the Panama Canal?", "BACKGROUND_KNOWLEDGE"),
        ("Define neural networks in simple terms", "BACKGROUND_KNOWLEDGE"),
        ("Calculate 27 * 391", "CALCULATION"),
        ("17.5% of 85000", "CALCULATION"),
        ("What is the latest LangGraph version?", "CURRENT_INFORMATION"),
        ("Compare FastAPI and Flask", "COMPARISON"),
        ("Should I use PostgreSQL or MongoDB?", "DECISION_ANALYSIS"),
        ("Summarize this uploaded PDF", "SUMMARIZATION"),
        ("Research the latest developments in agentic AI", "DEEP_RESEARCH"),
        ("Write a python function to sort a list", "CODE"),
        ("Find papers on multimodal LLMs", "ACADEMIC_RESEARCH"),
        ("Survey of agentic AI architectures", "ACADEMIC_RESEARCH"),
        ("State-of-the-art RAG retrieval methods survey", "ACADEMIC_RESEARCH"),
        ("Summarize the latest transformer fine-tuning paper on arXiv", "ACADEMIC_RESEARCH"),
        ("What is the best vector database for RAG?", "WEB_SEARCH"),
    ],
)
def test_classifier(query, expected_intent):
    result = classify(query)
    assert result.intent == expected_intent


def test_calculation_classified_without_web():
    result = classify("Calculate 17.5% of 85000")
    assert result.needs_web is False
    assert "calculator" in result.tools


def test_document_classification_when_docs_present():
    result = classify("Summarize this", has_documents=True)
    assert result.intent == "SUMMARIZATION"


def test_router_maps_intents():
    assert route_query({"intent": "CALCULATION"} if False else {"query": "", "intent": "CALCULATION"}) == "calculate"
    assert route_query(AgentState(intent="DOCUMENT_QA", documents=["d1"])) == "retrieve_documents"
    assert route_query(AgentState(intent="GENERAL_CHAT")) == "synthesize"
    assert route_query(AgentState(intent="WEB_SEARCH")) == "plan"
    assert route_query(AgentState(intent="BACKGROUND_KNOWLEDGE")) == "background_research"
    assert route_query(AgentState(intent="DEEP_RESEARCH")) == "plan"