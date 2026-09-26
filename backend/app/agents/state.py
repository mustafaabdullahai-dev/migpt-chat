"""Strongly-typed LangGraph agent state (TypedDict, fully serializable).

List fields that should accumulate across nodes use reducer annotations (operator.add).
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from app.models.schemas import (
    CalculationResult,
    Citation,
    Evidence,
    RetrievedChunk,
    SearchResult,
    ToolRun,
)


class AgentState(TypedDict, total=False):
    # ---- request context ----
    query: str
    conversation_id: str
    user_id: str
    mode: str          # auto | web | no_web | deep
    documents: list[str]
    history: list      # previous chat messages as {role, content}
    memory: dict       # persistent user profile facts {key: value}, reused across chats
    conversation_summary: str  # rolling per-conversation summary (context management)

    # ---- classification ----
    intent: str
    complexity: str    # low | medium | high
    needs_web: bool
    requires_tools: list[str]
    selected_tools: list[str]  # minimal tool subset relevant to the task (select_tools node)
    depth: str         # research depth tier: quick | standard | deep

    # ---- planning / search ----
    plan: list[str]
    search_queries: Annotated[list[str], operator.add]
    search_results: Annotated[list[SearchResult], operator.add]

    # ---- evidence ----
    fetched_pages: Annotated[list, operator.add]
    evidence: Annotated[list[Evidence], operator.add]
    sources: Annotated[list[Citation], operator.add]
    citations: Annotated[list[Citation], operator.add]

    # ---- computation ----
    calculation: CalculationResult | None
    calculations: Annotated[list[CalculationResult], operator.add]
    datetime_result: str   # server-clock result for DATETIME queries

    # ---- RAG ----
    retrieved: Annotated[list[RetrievedChunk], operator.add]
    reasoning_scaffold: str

    # ---- summarization ----
    summary: str      # map-reduce summary output (source content never dumped in full)

    # ---- answer ----
    answer: str
    followups: list[str]
    confidence: float
    errors: Annotated[list[str], operator.add]

    # ---- observability ----
    tool_runs: Annotated[list[ToolRun], operator.add]
    iterations: int
    evidence_ready: bool | None = None