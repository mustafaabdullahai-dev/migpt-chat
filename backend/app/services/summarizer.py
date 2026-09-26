"""Map-reduce summarization so huge documents are never sent whole to the LLM.

    - short inputs (<= DIRECT_LIMIT chars): one direct pass
    - long inputs: split into chunks, summarize each (map, bounded concurrency),
      then merge the section summaries into a final summary (reduce).

Works for webpages, PDFs/documents, search results, conversations and research
collections — anything that can be reduced to plain text.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.logging import get_logger
from app.services.llm import get_fast_llm

logger = get_logger("summarizer")

CHUNK_SIZE = 4000
CHUNK_OVERLAP = 300
DIRECT_LIMIT = 6000
MAX_INPUT_CHARS = 200_000
MAX_MAP_CONCURRENCY = 4
DEFAULT_WORDS = 180

_MAP_PROMPT = (
    "You are a precise summarizer. Distill the following text into a concise "
    "{words}-word summary of the key facts, claims and number. Use short bullets. "
    "Do not add commentary. If the text is an answer/chat turn, keep its subject.\n\n"
    "TEXT:\n{text}"
)

_REDUCE_PROMPT = (
    "Below are {n} section summaries of one larger piece of content. Merge them into ONE "
    "coherent {words}-word summary: a one-line lead about the subject, then 4-8 bullets with the "
    "most important points. Do not repeat points across sections. No fluff.\n\n{parts}"
)


@dataclass
class SummaryResult:
    summary: str
    method: str          # "direct" | "map_reduce"
    source_chars: int
    chunk_count: int
    target_words: int


def _clean(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


async def _llm(prompt: str, max_tokens: int) -> str:
    llm = get_fast_llm(temperature=0.2, max_tokens=max_tokens)
    resp = await llm.ainvoke([("human", prompt)])
    content = getattr(resp, "content", None)
    return str(content or "").strip()


async def summarize_text(text: str, *, target_words: int = DEFAULT_WORDS) -> SummaryResult:
    text = _clean(text or "")[:MAX_INPUT_CHARS]
    if not text:
        return SummaryResult(summary="", method="direct", source_chars=0, chunk_count=0, target_words=target_words)

    if len(text) <= DIRECT_LIMIT:
        try:
            summary = await _llm(_MAP_PROMPT.format(words=target_words, text=text), max_tokens=600)
        except Exception as exc:
            logger.warning("direct summarization failed", extra={"event": "summarizer.direct_fail", "error": str(exc)})
            summary = ""
        return SummaryResult(summary=summary, method="direct", source_chars=len(text), chunk_count=1, target_words=target_words)

    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    chunks = [c for c in splitter.split_text(text) if c.strip()]
    if len(chunks) == 1:
        # after cleaning/splitting it is effectively short
        try:
            summary = await _llm(_MAP_PROMPT.format(words=target_words, text=chunks[0]), max_tokens=600)
        except Exception as exc:
            logger.warning("direct summarization failed", extra={"event": "summarizer.direct_fail", "error": str(exc)})
            summary = ""
        return SummaryResult(summary=summary, method="direct", source_chars=len(text), chunk_count=1, target_words=target_words)

    per_chunk_words = max(60, target_words // max(len(chunks), 1))
    sem = asyncio.Semaphore(MAX_MAP_CONCURRENCY)

    async def map_chunk(chunk: str, idx: int) -> str:
        async with sem:
            for attempt in range(2):
                try:
                    return await _llm(_MAP_PROMPT.format(words=per_chunk_words, text=chunk[:CHUNK_SIZE]), max_tokens=350)
                except Exception:
                    if attempt == 1:
                        return f"[section {idx + 1}: summary failed]"
                    await asyncio.sleep(0.5)
            return ""

    parts = await asyncio.gather(*[map_chunk(c, i) for i, c in enumerate(chunks)])
    numbered = "\n\n".join(f"### Section {i + 1}\n{p}" for i, p in enumerate(parts) if p)
    if not numbered:
        return SummaryResult(summary="", method="map_reduce", source_chars=len(text), chunk_count=len(chunks), target_words=target_words)

    reduce_words = max(target_words, 120)
    try:
        final = await _llm(_REDUCE_PROMPT.format(n=len(chunks), words=reduce_words, parts=numbered[:18000]), max_tokens=700)
    except Exception as exc:
        logger.warning("reduce summarization failed", extra={"event": "summarizer.reduce_fail", "error": str(exc)})
        final = numbered

    return SummaryResult(summary=final, method="map_reduce", source_chars=len(text), chunk_count=len(chunks), target_words=target_words)


def format_conversation(history: list) -> str:
    """Turn {role, content} turns into a compact transcript for summarization."""
    lines = []
    for turn in history[-60:]:
        role = turn.get("role", "")
        content = str(turn.get("content", ""))[:1500]
        if role == "user":
            lines.append(f"User: {content}")
        elif role == "assistant":
            lines.append(f"Assistant: {content}")
    return "\n".join(lines)