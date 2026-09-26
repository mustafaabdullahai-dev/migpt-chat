"""Per-request structured observability (SPEC §59).

Each chat/research request gets a `RequestSpan` bound to the current task via a
ContextVar. As the pipeline runs, tool calls, counts and errors are recorded; `end()`
emits ONE structured JSON log line carrying: request_id, conversation_id, user_id,
query, intent, depth, mode, model, tools_used, tool_latency_ms, search_count,
sources_count, total_latency_ms, errors (tokens only where the provider exposes it).

Never logged: API keys, passwords, OAuth secrets, private credentials. All logged text
is run through `mask_secrets`, and the JSON formatter additionally redacts any record
key whose name contains password/token/secret/key/authorization.
"""

from __future__ import annotations

import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import new_request_id

logger = get_logger("observability")

_SECRET_RE = [
    re.compile(r"\bbearer\s+[a-z0-9._\-]{6,}\b", re.IGNORECASE),
    re.compile(r"\b(?:sk|pk|rk)\-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\b(?:ghp|gho|github_pat)_[A-Za-z0-9]{8,}\b"),
    re.compile(
        r"(api[_-]?key|password|passwd|secret|token|authorization|credential|apikey)['\"]{0,2}\s*[:=]\s*['\"]?[^\s'\",}]+",
        re.IGNORECASE,
    ),
]

_QUERY_MAX = 400


def mask_secrets(text: str) -> str:
    """Masks credential-shaped values inside otherwise-loggable text."""
    masked = str(text)
    for pattern in _SECRET_RE:
        masked = pattern.sub(lambda m: f"{m.group(0)[:6]}[REDACTED]", masked)
    return masked


def active_model() -> str:
    if settings.is_demo:
        return "mock"
    return settings.llm_provider or settings.groq_model or "unknown"


@dataclass
class RequestSpan:
    request_id: str
    started: float = field(default_factory=time.perf_counter)
    request_type: str = "chat"
    conversation_id: str = ""
    user_id: str = ""
    query: str = ""
    mode: str = ""
    intent: str = ""
    depth: str = ""
    sources_count: int = 0
    search_count: int = 0
    tokens: int | None = None
    status: str = "ok"
    errors: list[str] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)

    _SEARCH_TOOLS = {"web_search", "wikipedia", "arxiv", "deep_research"}

    def record_tool(self, name: str, status: str, latency_ms: int | None = None) -> None:
        self.tools.append(
            {"name": str(name), "status": status, "latency_ms": int(latency_ms or 0)}
        )
        if str(name) in self._SEARCH_TOOLS:
            self.search_count += 1

    def total_latency_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)

    def summary(self) -> dict:
        return {
            "event": "request.complete",
            "request_id": self.request_id,
            "request_type": self.request_type,
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "query": mask_secrets(self.query)[:_QUERY_MAX],
            "intent": self.intent,
            "depth": self.depth,
            "mode": self.mode,
            "model": active_model(),
            "tools_used": [t["name"] for t in self.tools],
            "tool_latency_ms": [t["latency_ms"] for t in self.tools],
            "search_count": self.search_count,
            "sources_count": self.sources_count,
            "tokens": self.tokens,
            "total_latency_ms": self.total_latency_ms(),
            "status": self.status,
            "errors": self.errors,
        }


_span_var: ContextVar[RequestSpan | None] = ContextVar("request_span", default=None)


def begin(**fields: object) -> RequestSpan:
    span = RequestSpan(request_id=new_request_id(), **fields)
    _span_var.set(span)
    return span


def current() -> RequestSpan | None:
    return _span_var.get()


def request_id_for_log() -> str:
    span = _span_var.get()
    return span.request_id if span else ""


def end() -> dict | None:
    span = _span_var.get()
    if span is None:
        return None
    _span_var.set(None)
    summary = span.summary()
    logger.info("request complete", extra=summary)
    return summary