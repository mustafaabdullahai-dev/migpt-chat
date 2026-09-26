"""Automatic, short conversation titles generated from the first user message."""

from __future__ import annotations

import re

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.services.llm import call_with_retry, get_fast_llm

_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Generate a very short title (max 6 words, no quotes, no punctuation at the end) "
            "that summarizes this user's first message for a chat history list.",
        ),
        ("human", "{message}"),
    ]
)


def generate_title(user_message: str, max_words: int = 6) -> str:
    llm = get_fast_llm(temperature=0.1, max_tokens=128)
    chain = _PROMPT | llm | StrOutputParser()
    try:
        raw = call_with_retry(
            lambda: chain.invoke({"message": user_message[:1000]}),
            attempts=3,
            desc="title generation",
        )
    except Exception:
        raw = ""
    title = _clean(raw) if raw else _fallback(user_message, max_words)
    return title or "New chat"


def _clean(raw: str) -> str:
    text = raw.strip().strip('"').strip("'")
    text = re.sub(r"\s+", " ", text)
    words = text.split()
    if len(words) > 6:
        text = " ".join(words[:6])
    text = text.rstrip(".!?")
    return text[:200]


def _fallback(message: str, max_words: int) -> str:
    words = [w for w in message.split() if w not in ("?", "!", "the", "a", "an", "is", "of")]
    return " ".join(words[:max_words]).capitalize() or "New chat"