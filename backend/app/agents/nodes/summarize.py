"""Graph node: map-reduce summarization of webpages, documents, conversations and
research collections. Never dumps the full source into the answer — the raw text is
chunked + summarized first, and only the summary reaches the synthesizer."""

from __future__ import annotations

import re

from app.agents.nodes.helpers import run_tool
from app.agents.state import AgentState
from app.core.config import settings
from app.models.schemas import Evidence
from app.services.stream import emit_status
from app.services.summarizer import format_conversation, summarize_text

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_CONVERSATION_WORDS = (
    "summarize this conversation", "summarise this conversation",
    "summary of this conversation", "summarize this chat", "summarise this chat",
    "summarize our chat", "summarize our conversation", "summary of the conversation",
    "summarize the chat",
)

MAX_PAGE_CHARS = settings.max_page_size


def looks_like_url(query: str) -> bool:
    return bool(_URL_RE.search(query or ""))


def wants_conversation_summary(query: str, history: list) -> bool:
    lowered = (query or "").lower()
    return any(w in lowered for w in _CONVERSATION_WORDS) and bool(history)


async def summarize_answer(state: AgentState) -> dict:
    emit_status("Summarizing…")
    kind, text, cite_url, cite_title = await _gather_text(state)
    if not text:
        return {}

    result = await summarize_text(text)
    outcome: dict = {"summary": result.summary, "intent": state.get("intent", "SUMMARIZATION")}

    # if the summary came from a single webpage, add it as citable evidence so the
    # final answer carries a Sources section and clickable citation.
    if cite_url:
        snippet = (result.summary[:600] or text[:300]).replace("\n", " ")
        evidence = Evidence(
            source_id=cite_url,
            url=cite_url,
            title=cite_title or cite_url,
            snippet=snippet,
            relevant_text=result.summary[:900],
            source_type="web",
            source_quality="unknown",
            confidence=0.9,
        )
        outcome["evidence"] = [evidence]
    return outcome


async def _gather_text(state: AgentState) -> tuple[str, str, str, str]:
    query = state.get("query", "")

    # 1) uploaded documents (retrieved chunks already staged by retrieve_documents)
    retrieved = state.get("retrieved", [])
    if retrieved:
        text = "\n\n".join(f"[{d.document_title} page {d.page}] {d.text}" for d in retrieved)
        return "document", text[:MAX_PAGE_CHARS], "", ""

    # 2) a URL in the query
    match = _URL_RE.search(query)
    if match:
        url = match.group(0).rstrip(".,;:)")
        payload, _ = await run_tool("fetch_webpage", url=url)
        if isinstance(payload, dict) and payload.get("error"):
            return "", "", "", ""
        title = getattr(payload, "title", "") or url
        page_text = getattr(payload, "text", "") or ""
        return "webpage", page_text[:MAX_PAGE_CHARS], url, title

    # 3) this conversation / chat history
    if wants_conversation_summary(query, state.get("history", [])):
        return "conversation", format_conversation(state.get("history", [])), "", ""

    # 4) research collection (evidence from the research loop already gathered)
    evidence = state.get("evidence", [])
    if evidence:
        text = "\n\n".join(f"[{e.title}] {e.snippet or e.relevant_text}" for e in evidence[:12])
        return "research_collection", text[:MAX_PAGE_CHARS], "", ""

    return "", "", "", ""