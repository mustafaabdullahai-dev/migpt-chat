"""Chat endpoints: POST /api/chat (JSON) and POST /api/chat/stream (SSE)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agents.graph import get_graph
from app.agents.state import AgentState
from app.api.deps import resolve_user
from app.core.config import settings
from app.core.logging import get_logger
from app.db import crud
from app.db.base import get_db
from app.db.crud import (
    add_message,
    add_sources,
    add_tool_runs,
    conversation_memory_dict,
    create_conversation,
    get_conversation,
    history_messages,
    list_messages,
    rename_conversation,
    touch_conversation,
)
from app.models.schemas import ChatRequest, ChatResponse
from app.services.cache import get_cache, reply_cache_key
from app.services.observability import begin, current, end
from app.services.stream import emit, set_queue, sse_frame
from app.services.followups import generate_followups

logger = get_logger("api.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])

_bg_tasks: set[asyncio.Task] = set()


def _fire_title_task(conversation_id: str, query: str) -> None:
    """Generates a short title ASAP (background, non-blocking) so the sidebar names
    the chat ~a second after the first message instead of waiting for the whole answer."""
    task = asyncio.create_task(_title_worker(conversation_id, query))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def _title_worker(conversation_id: str, query: str) -> None:
    try:
        await asyncio.sleep(0.6)  # let the first SSE frames flush first
        from app.db.base import SessionLocal
        from app.services.titles import generate_title

        title = await asyncio.to_thread(generate_title, query)
        if not title or title == "New chat":
            return
        with SessionLocal() as db:
            rename_conversation(db, conversation_id, title)
        emit("conversation_titled", {"conversation_id": conversation_id, "title": title})
    except Exception:
        logger.exception("background title generation failed")


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, user_id: str = Depends(resolve_user), db: Session = Depends(get_db)) -> ChatResponse:
    conv, initial = _prepare(req, user_id, db)
    user_msg = add_message(
        db,
        conv.conversation_id,
        "user",
        req.message,
        "",
        metadata=_attachment_meta(db, req.documents),
    )

    if _first_user_message(db, conv.conversation_id):
        _fire_title_task(conv.conversation_id, req.message)

    span = begin(
        request_type="chat",
        conversation_id=conv.conversation_id,
        user_id=user_id,
        query=req.message,
        mode=req.mode,
    )
    state: AgentState | None = None
    try:
        hit = _reply_cache_hit(req)
        if hit is not None:
            return _finalize(db, conv, user_msg.message_id, _reply_mini_state(hit, req.message))

        state = await get_graph().ainvoke(initial)
        _store_reply_cache(req.mode, req.documents, state)
        return _finalize(db, conv, user_msg.message_id, state)
    except Exception as exc:
        span.status = "error"
        span.errors.append(str(exc))
        raise
    finally:
        if state:
            _enrich_span(span, state)
        end()


@router.post("/stream")
async def chat_stream(req: ChatRequest, user_id: str = Depends(resolve_user), db: Session = Depends(get_db)):
    conv, initial = _prepare(req, user_id, db)
    user_msg = add_message(
        db,
        conv.conversation_id,
        "user",
        req.message,
        "",
        metadata=_attachment_meta(db, req.documents),
    )
    conversation_id = conv.conversation_id
    first_message = _first_user_message(db, conversation_id)

    async def gen():
        if first_message:
            _fire_title_task(conversation_id, req.message)
        hit = _reply_cache_hit(req)
        if hit is not None:
            _emit_cached_reply(db, conversation_id, user_msg.message_id, req.message, hit)
            yield sse_frame("done", {"conversation_id": conversation_id})
            return

        span = begin(
            request_type="stream",
            conversation_id=conversation_id,
            user_id=user_id,
            query=req.message,
            mode=req.mode,
        )
        queue: asyncio.Queue = asyncio.Queue()
        set_queue(queue)
        emit("message_start", {
            "conversation_id": conversation_id,
            "user_message_id": user_msg.message_id,
            "role": "assistant",
        })
        task = asyncio.create_task(_run_workflow(initial, conversation_id, user_msg.message_id, db))
        try:
            while True:
                item = await queue.get()
                _observe_span(item, span)
                yield sse_frame(item["event"], item["data"])
                if item["event"] in ("message_complete", "error"):
                    yield sse_frame("done", {"conversation_id": conversation_id})
                    break
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception:
            task.cancel()
            raise
        finally:
            if not task.done():
                task.cancel()
            set_queue(None)
            end()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _attachment_meta(db: Session, document_ids: list[str] | None) -> dict | None:
    """Build message metadata describing attached documents (filename + id)."""
    ids = [d for d in (document_ids or []) if d]
    if not ids:
        return None
    docs = crud.list_documents_by_ids(db, ids)
    return {"attachments": [{"document_id": d.document_id, "filename": d.filename} for d in docs]}


def _prepare(req: ChatRequest, user_id: str, db: Session):
    """Loads or creates the conversation and builds the initial graph state."""
    conversation_id: str | None = None
    if req.conversation_id and req.conversation_id != "new":
        try:
            uuid.UUID(req.conversation_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="conversation_id must be a valid UUID")
        conversation_id = req.conversation_id

    if conversation_id:
        conv = get_conversation(db, conversation_id, user_id)
        if conv is None:
            # A unique client UUID for a brand-new chat: create it so the optimistic
            # frontend messages keyed under this id stay in one independent chat.
            conv = create_conversation(db, user_id, conversation_id=conversation_id)
        if req.regenerate:
            _strip_last_assistant(db, conv.conversation_id)
    else:
        conv = create_conversation(db, user_id)
        if req.regenerate:
            _strip_last_assistant(db, conv.conversation_id)

    history = [
        {"role": m.role, "content": m.content}
        for m in history_messages(db, conv.conversation_id)
    ]
    if history and history[-1].get("role") == "user":
        history.pop()

    # Chat-scoped memory: facts learned in THIS conversation (its unique uuid) only.
    memory = conversation_memory_dict(db, conv.conversation_id) if settings.memory_enabled else {}

    initial: AgentState = {
        "query": req.message,
        "conversation_id": conv.conversation_id,
        "user_id": user_id,
        "mode": req.mode,
        "documents": req.documents or [],
        "history": history,
        "memory": memory,
        "conversation_summary": conv.summary or "",
        "intent": "",
        "confidence": 1.0,
        "errors": [],
        "tool_runs": [],
        "iterations": 0,
    }
    return conv, initial


def _reply_cache_hit(req: ChatRequest) -> dict | None:
    """Instant reply for repeated, deterministic (LLM-free) questions. Gated: only
    pure CALCULATION (no docs, no deep/web mode) — never research or personal data."""
    if req.documents or req.mode in ("deep", "web"):
        return None
    from app.agents.routers.classifier import _fast_classify

    fast = _fast_classify(req.message.lower(), False, "")
    if fast is None or fast.intent != "CALCULATION":
        return None
    cache = get_cache()
    if cache is None:
        return None
    hit = cache.get(reply_cache_key(req.message, req.mode))
    if isinstance(hit, dict) and hit.get("answer"):
        return hit
    return None


def _store_reply_cache(mode: str, documents: list | None, state: AgentState) -> None:
    cache = get_cache()
    if cache is None:
        return
    if state.get("intent") != "CALCULATION" or state.get("calculation") is None:
        return
    if documents or mode in ("deep", "web"):
        return
    cache.set(
        reply_cache_key(state.get("query", ""), mode),
        {
            "answer": state.get("answer", ""),
            "intent": state.get("intent", "CALCULATION"),
            "confidence": state.get("confidence", 1.0),
        },
        ttl=1800,
    )


def _reply_mini_state(hit: dict, query: str) -> AgentState:
    return {
        "query": query,
        "answer": hit.get("answer", ""),
        "intent": hit.get("intent", "CALCULATION"),
        "confidence": hit.get("confidence", 1.0),
        "evidence": [],
        "tool_runs": [],
        "errors": [],
    }


def _emit_cached_reply(db: Session, conversation_id: str, user_msg_id: str, query: str, hit: dict) -> None:
    answer = hit.get("answer", "")
    emit("message_start", {
        "conversation_id": conversation_id,
        "user_message_id": user_msg_id,
        "role": "assistant",
    })
    emit("status", {"message": "Generating your answer…"})
    for i in range(0, len(answer), 96):
        emit("message_delta", {"delta": answer[i : i + 96]})
    _persist_success(db, conversation_id, user_msg_id, _reply_mini_state(hit, query))


async def _run_workflow(initial: AgentState, conversation_id: str, user_msg_id: str, db: Session):
    try:
        state = await get_graph().ainvoke(initial)
    except Exception as exc:
        logger.error("graph run failed", extra={"event": "chat.graph_error", "conversation_id": conversation_id, "error": str(exc)})
        span = current()
        if span:
            span.status = "error"
            span.errors.append(str(exc))
        _persist_failure(db, conversation_id)
        return
    span = current()
    if span:
        span.intent = state.get("intent") or span.intent
        span.depth = state.get("depth") or span.depth
        for err in state.get("errors", []) or []:
            span.errors.append(str(err))
    _store_reply_cache(initial.get("mode", "auto"), initial.get("documents", []), state)
    _persist_success(db, conversation_id, user_msg_id, state)


def _observe_span(item: dict, span) -> None:
    """Folds observed SSE events into the request span (streaming path)."""
    event, data = item.get("event"), item.get("data") or {}
    if event == "tool_result":
        span.record_tool(
            str(data.get("name") or ""),
            str(data.get("status") or "success"),
            int(data.get("latency_ms") or 0),
        )
    elif event == "sources_found":
        try:
            span.sources_count = max(span.sources_count, int(data.get("count") or 0))
        except (TypeError, ValueError):
            pass
    elif event == "sources":
        n = len(data) if isinstance(data, list) else 0
        span.sources_count = max(span.sources_count, n)
    elif event == "message_complete":
        span.intent = data.get("intent") or span.intent
        srcs = data.get("sources")
        if isinstance(srcs, list):
            span.sources_count = max(span.sources_count, len(srcs))
        if data.get("error"):
            span.status = "error"


def _enrich_span(span, state: AgentState) -> None:
    """Copies tool/counts/intent info into the span after a non-stream run."""
    span.intent = state.get("intent") or span.intent
    span.depth = state.get("depth") or span.depth
    span.sources_count = len(state.get("evidence") or []) + len(state.get("retrieved") or [])
    for err in state.get("errors", []) or []:
        span.errors.append(str(err))
    for run in state.get("tool_runs", []) or []:
        span.record_tool(
            str(getattr(run, "name", "") or ""),
            str(getattr(run, "status", "success") or "success"),
            int(getattr(run, "latency_ms", 0) or 0),
        )


def _persist_success(db: Session, conversation_id: str, user_msg_id: str, state: AgentState):
    answer = state.get("answer", "")
    intent = state.get("intent", "")
    assistant = add_message(db, conversation_id, "assistant", answer, intent=intent)

    from app.services.citations import build_citations

    citations = build_citations(state.get("evidence", []))
    sources: list[dict] = [
        {"title": c.title, "url": c.url, "source_type": c.source, "quality": c.quality}
        for c in citations
    ]
    if sources:
        add_sources(db, assistant.message_id, sources)

    runs = state.get("tool_runs", [])
    if runs:
        add_tool_runs(db, conversation_id, [r.model_dump() for r in runs])

    touch_conversation(db, conversation_id)

    from app.services.conversation_summary import schedule_refresh

    schedule_refresh(db, conversation_id)

    followups: list[str] = []
    if settings.enable_followups and answer:
        try:
            followups = generate_followups(state.get("query", ""), answer)
        except Exception:
            followups = []

    emit("message_complete", {
        "conversation_id": conversation_id,
        "message_id": assistant.message_id,
        "user_message_id": user_msg_id,
        "content": answer,
        "role": "assistant",
        "intent": intent,
        "sources": sources,
        "tool_runs": [r.model_dump() for r in runs],
        "followups": followups,
        "created_at": getattr(assistant, "created_at", None).isoformat()
        if getattr(assistant, "created_at", None)
        else datetime.now(timezone.utc).isoformat(),
    })


def _persist_failure(db: Session, conversation_id: str):
    add_message(
        db,
        conversation_id,
        "assistant",
        "I hit an internal error while processing your request. Please try again. "
        "(This failure has been logged server-side.)",
        intent="error",
    )
    emit("message_complete", {"conversation_id": conversation_id, "error": True})


def _strip_last_assistant(db: Session, conversation_id: str) -> None:
    msgs = list_messages(db, conversation_id)
    if msgs and msgs[-1].role == "assistant":
        db.delete(msgs[-1])
        db.commit()


def _first_user_message(db: Session, conversation_id: str) -> bool:
    msgs = list_messages(db, conversation_id)
    user_msgs = [m for m in msgs if m.role == "user"]
    return len(user_msgs) == 1


def _finalize(db: Session, conv, user_msg_id: str, state: AgentState) -> ChatResponse:
    answer = state.get("answer", "")
    assistant = add_message(db, conv.conversation_id, "assistant", answer, intent=state.get("intent", ""))

    from app.services.citations import build_citations

    citations = build_citations(state.get("evidence", []))
    if citations:
        add_sources(
            db,
            assistant.message_id,
            [
                {"title": c.title, "url": c.url, "source_type": c.source, "quality": c.quality}
                for c in citations
            ],
        )

    from app.services.conversation_summary import schedule_refresh

    schedule_refresh(db, conv.conversation_id)

    followups: list[str] = []
    if settings.enable_followups and answer:
        try:
            followups = generate_followups(state.get("query", ""), answer)
        except Exception:
            followups = []

    return ChatResponse(
        conversation_id=conv.conversation_id,
        user_message_id=user_msg_id,
        assistant_message_id=assistant.message_id,
        answer=answer,
        intent=state.get("intent", ""),
        sources=citations,
        citations=citations,
        tool_runs=state.get("tool_runs", []),
        followups=followups,
        confidence=state.get("confidence", 1.0),
    )