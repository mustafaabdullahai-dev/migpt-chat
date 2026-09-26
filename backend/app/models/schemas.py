"""Pydantic schemas shared across tools, services and the API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

IntentName = Literal[
    "GENERAL_CHAT",
    "WEB_SEARCH",
    "DEEP_RESEARCH",
    "CALCULATION",
    "DOCUMENT_QA",
    "CODE",
    "COMPARISON",
    "DECISION_ANALYSIS",
    "SUMMARIZATION",
    "CURRENT_INFORMATION",
    "ACADEMIC_RESEARCH",
    "DATA_ANALYSIS",
    "MULTI_TOOL",
]

ChatMode = Literal["auto", "web", "no_web", "deep"]


# ---------- Search ----------
class SearchResult(BaseModel):
    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""


# ---------- Web page ----------
class PageContent(BaseModel):
    url: str = ""
    title: str = ""
    text: str = ""
    headings: list[str] = []
    metadata: dict[str, str] = {}
    links: list[dict] = Field(default_factory=list)
    truncated: bool = False


# ---------- Evidence ----------
class Evidence(BaseModel):
    source_id: str = ""
    title: str = ""
    url: str = ""
    snippet: str = ""
    relevant_text: str = ""
    source_type: str = "web"
    confidence: float = 0.5
    source_quality: str = "unknown"


# ---------- Citations ----------
class Citation(BaseModel):
    index: int
    url: str = ""
    title: str = ""
    source: str = ""
    quality: str = "unknown"
    page: int = 0
    chunk_id: str = ""


# ---------- Tools ----------
class ToolRun(BaseModel):
    name: str
    status: Literal["running", "success", "error"] = "running"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    latency_ms: int = 0
    detail: dict[str, Any] = Field(default_factory=dict)


class CalculationResult(BaseModel):
    expression: str
    result: float | int | str | list = Field(default=0)
    steps: list[str] = Field(default_factory=list)


# ---------- RAG ----------
class RetrievedChunk(BaseModel):
    document_id: str
    document_title: str
    chunk_id: str
    page: int = 0
    text: str
    score: float = 0.0
    filename: str = ""
    source: str = ""


class DocumentMeta(BaseModel):
    document_id: str
    filename: str
    content_type: str
    size_bytes: int
    chunks: int
    uploaded_at: datetime


# ---------- Chat ----------
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None
    mode: ChatMode = "auto"
    documents: list[str] = Field(default_factory=list)
    regenerate: bool = False


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    intent: str = ""
    created_at: datetime | None = None


class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationOut(BaseModel):
    conversation_id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    metadata: dict = Field(default_factory=dict)
    summary: str = ""
    message_count: int = 0
    last_message: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    metadata: dict | None = None
    summary: str | None = None


class ConversationSearchHit(BaseModel):
    conversation: ConversationOut
    message_id: str | None = None
    snippet_role: str = ""
    snippet: str = ""


class MessageOut(BaseModel):
    message_id: str
    role: str
    content: str
    created_at: datetime
    intent: str = ""
    sources: list[Citation] = Field(default_factory=list)
    attachments: list["Attachment"] = Field(default_factory=list)


class Attachment(BaseModel):
    document_id: str
    filename: str


class ChatResponse(BaseModel):
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    answer: str
    intent: str = ""
    sources: list[Citation] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    tool_runs: list[ToolRun] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=8, ge=1, le=20)


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]


class ResearchRequest(BaseModel):
    query: str
    depth: Literal["quick", "standard", "deep"] = "standard"


class ResearchOut(BaseModel):
    query: str
    intent: str = "research"
    answer: str
    searches: list[str] = Field(default_factory=list)
    sources: list[Citation] = Field(default_factory=list)
    tool_runs: list[ToolRun] = Field(default_factory=list)


class FeedbackIn(BaseModel):
    conversation_id: str
    message_id: str
    rating: Literal["up", "down"] = "up"
    comment: str = ""


class TitleGeneration(BaseModel):
    user_message: str


class TitleOut(BaseModel):
    title: str