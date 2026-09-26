"""Server-Sent-Event streaming plumbing.

A ContextVar holds the current asyncio.Queue. LangGraph nodes call emit() to publish
safe progress events; the FastAPI StreamingResponse drains the queue into SSE frames.
"""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar

from app.core.logging import get_logger

logger = get_logger("stream")

_queue_var: ContextVar[asyncio.Queue | None] = ContextVar("sse_queue", default=None)


def set_queue(queue: asyncio.Queue | None) -> None:
    _queue_var.set(queue)


def get_queue() -> asyncio.Queue | None:
    return _queue_var.get()


def emit(event: str, data: dict) -> None:
    q = _queue_var.get()
    if q is None:
        return
    try:
        q.put_nowait({"event": event, "data": data})
    except Exception:
        pass


def emit_status(message: str) -> None:
    emit("status", {"message": message})


def emit_source(source: dict) -> None:
    """Publishes a single confirmed source as it becomes available."""
    emit("source_found", source)


def sse_frame(event: str, data: object) -> str:
    from app.core.security import sanitize_sse

    payload = sanitize_sse(json.dumps(data, default=str))
    return f"event: {event}\ndata: {payload}\n\n"