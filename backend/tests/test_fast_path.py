"""Fast-response architecture tests: deterministic classifier fast-path,
planner LLM gate, bounded cache TTL/eviction, and reply-level caching."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.agents.routers.classifier import _fast_classify, classify
from app.services.cache import InMemoryCache, reply_cache_key


# ---- 1. deterministic fast-path classifier (no LLM) ----

def test_fast_create_trivial_smalltalk_is_general_chat():
    for q in (
        "hello", "hi", "hey", "Thanks!", "who are you", "what can you do",
        "good morning", "help",
    ):
        fast = _fast_classify(q, False, "")
        assert fast is not None, q
        assert fast.intent == "GENERAL_CHAT"
        assert classify(q).intent == "GENERAL_CHAT"


def test_fast_path_keeps_deterministic_intents():
    cases = [
        ("Calculate 27 times 391", "CALCULATION"),
        ("what is 15% of 500", "CALCULATION"),
        ("convert 5 miles to km", "CALCULATION"),
        ("what time is it now", "DATETIME"),
        ("Compare FastAPI and Flask for 2026", "COMPARISON"),
        ("what is the best web framework", "WEB_SEARCH"),
        ("Compute 20% of 800 and compare with 15% of 900", "MULTI_TOOL"),
        ("What is Python's latest version?", "CURRENT_INFORMATION"),
    ]
    for q, intent in cases:
        assert classify(q).intent == intent, q


def test_fast_path_leaves_ambiguous_for_llm():
    ambiguous = [
        "I'm feeling a bit confused today",
        "Can you tell me something fun to do this weekend with no internet words",
    ]
    for q in ambiguous:
        assert _fast_classify(q, False, "") is None, q


def test_fast_path_mode_override():
    assert _fast_classify("how do I bake bread", False, "no_web").intent == "GENERAL_CHAT"


# ---- 2. planner LLM gate ----

class _ProdSettingsStub:
    """Stand-in for cfg.settings in planner tests (non-demo, real Groq key)."""
    is_demo = False
    groq_api_key = "gsk-x"
    llm_provider = "groq"


def test_planner_skips_llm_for_simple_research(monkeypatch):
    from app.agents.nodes.plan import _needs_llm_plan, plan_research
    from app.agents.state import AgentState

    # demo/mock mode always skips the planner LLM
    assert _needs_llm_plan(AgentState(intent="WEB_SEARCH", complexity="medium")) is False
    assert _needs_llm_plan(AgentState(intent="DEEP_RESEARCH")) is False

    monkeypatch.setattr("app.agents.nodes.plan.settings", _ProdSettingsStub())
    assert _needs_llm_plan(AgentState(intent="DEEP_RESEARCH")) is True
    assert _needs_llm_plan(AgentState(intent="ACADEMIC_RESEARCH", complexity="medium")) is True
    assert _needs_llm_plan(AgentState(intent="COMPARISON", complexity="high")) is True
    assert _needs_llm_plan(AgentState(intent="WEB_SEARCH", complexity="low")) is False
    assert _needs_llm_plan(AgentState(intent="WEB_SEARCH", mode="deep")) is True

    # low-complexity plans fall back to deterministic queries (no LLM call)
    monkeypatch.setattr("app.agents.nodes.plan.get_fast_llm", lambda **kw: (_ for _ in ()).throw(AssertionError("LLM must not be called")))
    state = AgentState(query="LangGraph multi agent", intent="WEB_SEARCH", complexity="low")
    out = asyncio.run(plan_research(dict(state)))
    assert out["search_queries"]


def test_planner_llm_path_used_when_needed(monkeypatch):
    import app.agents.nodes.plan as plan_mod
    from app.agents.state import AgentState

    monkeypatch.setattr(plan_mod, "settings", _ProdSettingsStub())

    class FakeLLM:
        async def ainvoke(self, messages):
            class Content:
                content = '["alpha query", "beta query", "gamma query"]'
            return Content()

    monkeypatch.setattr(plan_mod, "get_fast_llm", lambda **kw: FakeLLM())
    state = AgentState(query="deep dive into X", intent="DEEP_RESEARCH")
    out = asyncio.run(plan_mod.plan_research(dict(state)))
    assert any("alpha query" in q for q in out["search_queries"])


# ---- 3. cache TTL + bounded capacity ----

def test_imemory_cache_ttl_expires():
    c = InMemoryCache(default_ttl=1)
    c.set("k", "v", ttl=1)
    assert c.get("k") == "v"
    time.sleep(1.2)
    assert c.get("k") is None


def test_imemory_cache_bound_evicts_oldest():
    c = InMemoryCache(capacity=2)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    assert c.get("a") is None
    assert c.get("c") == 3


def test_reply_cache_key_normalizes_whitespace_and_case():
    assert reply_cache_key("  What is 15% of 500  ", "auto") == reply_cache_key("what is 15% of 500", "auto")
    assert reply_cache_key("x", "deep") != reply_cache_key("x", "auto")


# ---- 4. reply-level caching across /api/chat ----

class FakeGraph:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, initial):
        self.calls += 1
        return {
            "query": initial["query"],
            "answer": "75 requests.", "intent": "CALCULATION",
            "confidence": 0.99,
            "calculation": {"expression": "15% of 500", "value": 75},
            "evidence": [], "tool_runs": [], "errors": [],
        }


def test_reply_cache_serves_repeat_calc_without_graph(client, monkeypatch):
    from app.core.config import settings
    from app.services import cache as cache_mod

    graph = FakeGraph()
    monkeypatch.setattr("app.api.chat.get_graph", lambda: graph)
    monkeypatch.setattr(settings, "enable_cache", True)
    cache_mod._cache = None

    first = client.post("/api/chat", json={"message": "Calculate 15% of 500 requests"}).json()
    assert first["answer"]
    assert graph.calls == 1

    second = client.post("/api/chat", json={"message": "Calculate 15% of 500 requests"}).json()
    assert second["answer"] == first["answer"]
    assert graph.calls == 1  # served from cache, LLM/graph untouched