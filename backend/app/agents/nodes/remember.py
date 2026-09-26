"""Graph node: extract personal facts the user shared and persist them to THIS CHAT's memory.

Each conversation has a unique uuid with its own isolated memory: if the user says
"my name is xyz" in one chat, xyz is remembered for THAT chat only and never leaks
into any other chat/tab. Runs AFTER an answer is produced so the response itself is
never blocked on this bookkeeping step.
"""

from __future__ import annotations

import json
import re

from langchain_core.prompts import ChatPromptTemplate

from app.agents.state import AgentState
from app.core.config import settings
from app.db.base import SessionLocal
from app.db.crud import conversation_memory_dict, upsert_conversation_memory
from app.services.llm import get_fast_llm
from app.services.stream import emit_status

_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You manage the chat-scoped memory for ONE conversation of a personal AI assistant. "
            "The memory is stored under THIS conversation's unique id and is NEVER shared with "
            "other chats or tabs. From the SHORT conversation below, extract personal facts the USER "
            "shared about themselves: name, age, job, company, location, languages, education, "
            "preferences, goals, contact details, family, or other stable identity facts. "
            "NEVER extract facts about the assistant, the tools used, answers, or research "
            "content. NEVER invent facts - if nothing personal was shared, return an empty list. "
            "When new info contradicts an existing key, prefer the newest value. "
            "Return ONLY JSON, no prose, using this exact shape: "
            "memory as a JSON array of objects, each with key "
            "(a lowercase slug like name, job, company) and value (a short concise fact).",
        ),
        ("human", "Existing memory:\n{existing}\n\nRecent conversation:\n{conversation}"),
    ]
)


def _extract_json(text: str) -> str:
    """Pull the first balanced {...} JSON object out of a model reply."""
    if not text:
        return "{}"
    start = text.find("{")
    if start < 0:
        return "{}"
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def _conversation_text(state: AgentState) -> str:
    history = state.get("history", [])[-6:]
    parts = [
        f"user: {str(h.get('content', ''))[:800]}"
        if h.get("role") == "user"
        else f"assistant: {str(h.get('content', ''))[:800]}"
        for h in history
    ]
    parts.append(f"user: {str(state.get('query', ''))[:800]}")
    answer = state.get("answer", "")
    if answer:
        parts.append(f"assistant: {answer[:1200]}")
    return "\n".join(parts)[: settings.memory_conv_chars]


def _normalize(facts: list) -> list[dict]:
    out: list[dict] = []
    for fact in facts or []:
        key = re.sub(r"[^a-z0-9_]+", "_", str(fact.get("key", "")).lower().strip())
        value = str(fact.get("value", "")).strip()
        if key and value and key not in {f["key"] for f in out}:
            out.append({"key": key[:60], "value": value[:1500]})
    return out


async def remember(state: AgentState) -> dict:
    if not settings.memory_enabled:
        return {}
    conversation_id = state.get("conversation_id") or ""
    if not conversation_id:
        return {}

    existing = state.get("memory") or {}
    existing_text = "\n".join(f"- {k}: {v}" for k, v in list(existing.items())[:40]) or "(empty)"

    try:
        prompt = _PROMPT.format_messages(
            existing=existing_text,
            conversation=_conversation_text(state),
        )
        llm = get_fast_llm(temperature=0, max_tokens=600)
        raw = await llm.ainvoke(prompt)
        text = str(getattr(raw, "content", "") or "")
        data = json.loads(_extract_json(text))
        facts = _normalize(data.get("memory", []) if isinstance(data, dict) else [])
    except Exception:
        return {}

    if not facts:
        return {}

    try:
        with SessionLocal() as db:
            upsert_conversation_memory(db, conversation_id, facts)
            updated = conversation_memory_dict(db, conversation_id)
    except Exception:
        return {}

    if not settings.is_demo:
        emit_status("Remembered that for this chat.")
    return {"memory": updated}