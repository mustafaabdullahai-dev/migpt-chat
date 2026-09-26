"""Chat-scoped memory: CRUD persistence, per-uuid isolation + prompt injection (offline/mocked).

Each conversation has a unique uuid with its own isolated memory — a fact shared in one
chat never leaks into another chat/tab.
"""

from __future__ import annotations

import pytest

from app.agents.nodes.synthesize import _build_messages, _recent_history
from app.db.base import SessionLocal
from app.db.crud import (
    clear_conversation_memory,
    conversation_memory_dict,
    upsert_conversation_memory,
)
from langchain_core.messages import SystemMessage


def _fresh_conv() -> str:
    import uuid

    return str(uuid.uuid4())


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables():
    from app.db.base import init_db

    init_db()


def test_upsert_list_and_clear_chat_memory():
    conv = _fresh_conv()
    with SessionLocal() as db:
        upsert_conversation_memory(db, conv, [{"key": "Name", "value": "Abdullah"}])
        upsert_conversation_memory(db, conv, [{"key": "job", "value": "Software Engineer"}])
        upsert_conversation_memory(db, conv, [{"key": "name", "value": "Abdullah (updated)"}])  # upsert
        mem = conversation_memory_dict(db, conv)
        assert mem["name"] == "Abdullah (updated)"
        assert mem["job"] == "Software Engineer"
        assert clear_conversation_memory(db, conv) >= 2
        assert conversation_memory_dict(db, conv) == {}


def test_chat_memory_isolation_between_conversations():
    """The key requirement: uuid A's facts are invisible to conversation B."""
    conv_a = _fresh_conv()
    conv_b = _fresh_conv()
    with SessionLocal() as db:
        upsert_conversation_memory(db, conv_a, [{"key": "name", "value": "Abdullah"}])
        upsert_conversation_memory(db, conv_a, [{"key": "city", "value": "Lahore"}])
        mem_a = conversation_memory_dict(db, conv_a)
        mem_b = conversation_memory_dict(db, conv_b)

        assert mem_a == {"name": "Abdullah", "city": "Lahore"}
        assert mem_b == {}  # nothing leaked into the other chat
        assert "Abdullah" not in str(mem_b)


def test_memory_dict_empty_when_nothing_saved():
    with SessionLocal() as db:
        assert conversation_memory_dict(db, _fresh_conv()) == {}


def test_synthesize_prompt_includes_chat_memory():
    state = {
        "query": "What should I read next?",
        "history": [],
        "memory": {"name": "Abdullah", "interests": "distributed systems"},
    }
    msgs = _build_messages(state)
    system = msgs[0]
    assert isinstance(system, SystemMessage)
    text = str(system.content)
    assert "REMEMBERED IN THIS CHAT" in text
    assert "name: Abdullah" in text
    assert "interests: distributed systems" in text
    assert "NOT shared" in text.lower() or "NOT shared" in text or "not shared" in text.lower()


def test_recent_history_window_and_budget():
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i} " + "x" * 100}
        for i in range(30)
    ]
    kept = _recent_history(history, budget=500)
    assert len(kept) < 30
    assert "msg 29" in str(kept[-1]["content"])  # newest kept
    total = sum(len(str(h["content"])) for h in kept)
    assert total <= 500


@pytest.mark.asyncio
async def test_remember_node_is_safe_without_valid_json(monkeypatch):
    from app.agents.nodes import remember as remember_mod

    class _Stub:
        async def ainvoke(self, prompt):
            class _M:
                content = "Thanks! I'll note that down."

            return _M()

    monkeypatch.setattr(remember_mod, "get_fast_llm", lambda *a, **k: _Stub())
    monkeypatch.setattr(remember_mod, "emit_status", lambda *a, **k: None)
    monkeypatch.setattr(remember_mod, "settings", type("S", (), {"memory_enabled": True, "memory_conv_chars": 4000})())

    result = await remember_mod.remember(
        {"conversation_id": _fresh_conv(), "memory": {}, "history": [], "query": "hi", "answer": "hello"}
    )
    assert result == {}


@pytest.mark.asyncio
async def test_remember_saves_facts_scoped_to_conversation(monkeypatch):
    import json as _json

    from app.agents.nodes import remember as remember_mod

    class _Stub:
        async def ainvoke(self, prompt):
            class _M:
                content = _json.dumps({"memory": [{"key": "name", "value": "Abdullah"}]})

            return _M()

    monkeypatch.setattr(remember_mod, "get_fast_llm", lambda *a, **k: _Stub())
    monkeypatch.setattr(remember_mod, "emit_status", lambda *a, **k: None)
    monkeypatch.setattr(remember_mod, "settings", type("S", (), {"memory_enabled": True, "memory_conv_chars": 4000, "is_demo": True})())

    conv_a = _fresh_conv()
    conv_b = _fresh_conv()
    result = await remember_mod.remember(
        {"conversation_id": conv_a, "memory": {}, "history": [], "query": "Hi, my name is Abdullah", "answer": "Nice to meet you!"}
    )
    assert result["memory"]["name"] == "Abdullah"

    with SessionLocal() as db:  # isolated: only conv_a holds the fact
        assert conversation_memory_dict(db, conv_a) == {"name": "Abdullah"}
        assert conversation_memory_dict(db, conv_b) == {}