"""Summarizer tests: map-reduce service, tool registry, URL + conversation graph paths."""

from __future__ import annotations

import pytest

from app.agents.routers.router import after_retrieve, route_query
from app.agents.state import AgentState
from app.services.summarizer import format_conversation, summarize_text
from app.tools.base import registry


def _initial(query: str, history: list | None = None, docs: list[str] | None = None) -> dict:
    return {
        "query": query,
        "conversation_id": "test-conv",
        "user_id": "tester",
        "mode": "auto",
        "documents": docs or [],
        "history": history or [],
        "intent": "",
        "confidence": 1.0,
        "errors": [],
        "tool_runs": [],
        "iterations": 0,
    }


@pytest.mark.asyncio
async def test_direct_path_for_short_text():
    res = await summarize_text("The quick brown fox jumps over the lazy dog. " * 10)
    assert res.method == "direct"
    assert res.chunk_count == 1
    assert isinstance(res.summary, str) and res.summary


@pytest.mark.asyncio
async def test_map_reduce_path_for_long_text():
    body = "Parallel tokenizers and framework orchestration notes. " * 700  # > 28k chars
    res = await summarize_text(body)
    assert res.method == "map_reduce"
    assert res.chunk_count > 1
    assert res.source_chars > 0
    assert res.summary


@pytest.mark.asyncio
async def test_empty_text_returns_empty():
    res = await summarize_text("   \n  ")
    assert res.method == "direct"
    assert res.summary == ""


def test_format_conversation():
    out = format_conversation([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello there"},
    ])
    assert "User: hi" in out and "Assistant: hello there" in out


@pytest.mark.asyncio
async def test_tool_runs_via_registry():
    spec = registry.get("summarizer")
    assert spec is not None and spec.enabled and spec.is_async
    from app.tools.base import run_spec

    value, _ = await run_spec(spec, {"text": "Framework orchestration content. " * 200, "kind": "webpage"})
    assert isinstance(value, str) and "webpage" in value and "summarized via" in value


def test_summarization_routing():
    # documents -> RAG -> then map-reduce summarize (via after_retrieve)
    assert route_query(AgentState(intent="SUMMARIZATION", documents=["d1"], query="Summarize this PDF")) == "retrieve_documents"
    assert after_retrieve(AgentState(intent="SUMMARIZATION")) == "summarize"
    assert after_retrieve(AgentState(intent="DOCUMENT_QA")) == "synthesize"
    # URL -> dedicated summarize node
    assert route_query(AgentState(intent="SUMMARIZATION", query="Summarize https://example.com/readme")) == "summarize"
    # conversation history -> summarize node
    assert route_query(AgentState(intent="SUMMARIZATION", query="summarize this conversation", history=[{"role": "user", "content": "x"}])) == "summarize"
    # plain topic -> research loop gathers content, then summaries
    assert route_query(AgentState(intent="SUMMARIZATION", query="Summarize the LangGraph architecture")) == "plan"


@pytest.mark.asyncio
async def test_graph_url_summarize_adds_citable_evidence(search_mock, graph):
    state = await graph.ainvoke(_initial("Summarize https://example.com/readme"))
    assert state.get("intent") == "SUMMARIZATION"
    assert state.get("summary")
    evidence = state.get("evidence") or []
    assert any(e.url == "https://example.com/readme" for e in evidence)


@pytest.mark.asyncio
async def test_graph_conversation_summarize(graph):
    history = [
        {"role": "user", "content": "I need a FastAPI cheatsheet"},
        {"role": "assistant", "content": "Here is a cheatsheet covering routes, models and templates."},
    ]
    state = await graph.ainvoke(_initial("summarize this conversation", history=history))
    assert state.get("intent") == "SUMMARIZATION"
    assert state.get("summary")