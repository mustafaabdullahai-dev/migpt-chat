"""Conditional-edge router: maps classified intent to the next graph node."""

from __future__ import annotations

from app.agents.nodes.summarize import wants_conversation_summary
from app.agents.state import AgentState
from app.core.config import settings
from app.tools.comparison import parse_options

_URL_HINT_WORDS = ("url", "link", "http", "readme")


def _url_in(query: str) -> bool:
    low = (query or "").lower()
    return "http://" in low or "https://" in low or any(w in low for w in _URL_HINT_WORDS)


def route_query(state: AgentState) -> str:
    intent = state.get("intent", "GENERAL_CHAT")

    if intent == "CALCULATION":
        return "calculate"
    if intent == "DATETIME":
        return "datetime"
    # multi-tool: pure arithmetic+compare runs offline; anything needing live
    # evidence goes through the research loop and re-enters multi_tool at the end.
    if intent == "MULTI_TOOL":
        return "multi_tool" if not state.get("needs_web") else "plan"
    if intent in ("DOCUMENT_QA", "DATA_ANALYSIS"):
        return "retrieve_documents"
    if intent in ("GENERAL_CHAT", "CODE"):
        # Default to the live web-research loop for topical questions so answers
        # reflect current, running information on the internet, not just the LLM's
        # training data. Only trivial chit-chat/smalltalk stays offline-fast.
        if state.get("needs_web") and settings.enable_web_search:
            return "plan"
        return "synthesize"
    # SUMMARIZATION: uploaded files -> RAG first; URL/conversation -> dedicated
    # map-reduce summarize node; otherwise the research loop gathers content.
    if intent == "SUMMARIZATION":
        if state.get("documents"):
            return "retrieve_documents"
        if _url_in(state.get("query", "")) or wants_conversation_summary(state.get("query", ""), state.get("history", [])):
            return "summarize"
        return "plan"
    # definitions/history/intro -> encyclopedic background (Wikipedia-backed)
    if intent == "BACKGROUND_KNOWLEDGE":
        return "background_research"
    # research family (WEB_SEARCH, DEEP_RESEARCH, CURRENT_INFORMATION,
    # ACADEMIC_RESEARCH, COMPARISON, DECISION_ANALYSIS, MULTI_TOOL)
    return "plan"


def after_retrieve(state: AgentState) -> str:
    """Retrieved chunks for a SUMMARIZATION intent go through map-reduce first."""
    if state.get("intent") == "SUMMARIZATION":
        return "summarize"
    return "synthesize"