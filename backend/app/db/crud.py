"""Repository functions for all persisted entities."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Conversation,
    ConversationMemory,
    Document,
    DocumentChunk,
    Feedback,
    Message,
    Source,
    ToolRun,
    User,
    UserMemory,
)


def new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- users ----------
def get_or_create_user(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        user = User(user_id=user_id)
        db.add(user)
        db.commit()
    return user


# ---------- conversations ----------
def create_conversation(
    db: Session, user_id: str, title: str | None = None, conversation_id: str | None = None
) -> Conversation:
    """Creates a conversation. Pass a client-generated UUID to keep the optimistic
    frontend chat keyed under the SAME id it was started with (independent chats)."""
    conv = Conversation(
        conversation_id=conversation_id or new_id(),
        user_id=user_id,
        title=title or "New chat",
    )
    db.add(conv)
    try:
        db.commit()
    except Exception:
        db.rollback()
        conv.conversation_id = new_id()  # id collision with another user's row
        db.add(conv)
        db.commit()
    db.refresh(conv)
    return conv


def get_conversation(db: Session, conversation_id: str, user_id: str) -> Conversation | None:
    return db.scalar(
        select(Conversation).where(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == user_id,
        )
    )


def list_conversations(db: Session, user_id: str, limit: int = 100) -> list[Conversation]:
    return list(
        db.scalars(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
    )


def rename_conversation(db: Session, conversation_id: str, title: str) -> Conversation | None:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        return None
    conv.title = title[:200]
    db.commit()
    db.refresh(conv)
    return conv


def conv_metadata(conv) -> dict:
    """Parse a conversation's stored metadata (tolerant of corrupt/legacy rows)."""
    try:
        data = json.loads(conv.metadata_json or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def patch_conversation_metadata(
    db: Session, conversation_id: str, metadata: dict
) -> Conversation | None:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        return None
    merged = conv_metadata(conv)
    merged.update(metadata or {})
    conv.metadata_json = json.dumps(merged, default=str)[:20000]
    db.commit()
    db.refresh(conv)
    return conv


def update_conversation_summary(db: Session, conversation_id: str, summary: str) -> None:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        return
    conv.summary = (summary or "")[:8000]
    db.commit()


def conv_stats(db: Session, conv_ids: list[str]) -> dict[str, dict]:
    """Message count + last message content per conversation (one scan)."""
    if not conv_ids:
        return {}
    rows = db.execute(
        select(
            Message.conversation_id,
            Message.content,
            Message.created_at,
        )
        .where(Message.conversation_id.in_(conv_ids))
        .order_by(Message.created_at.asc())
    ).all()
    stats: dict[str, dict] = {}
    for cid, content, _ts in rows:
        entry = stats.setdefault(cid, {"count": 0, "last": None})
        entry["count"] += 1
        entry["last"] = content
    return stats


def _snippet(content: str, query: str, radius: int = 90) -> str:
    text = str(content or "")
    if not text:
        return ""
    idx = text.lower().find(str(query).lower())
    if idx < 0:
        return text[:180]
    start = max(0, idx - radius)
    end = min(len(text), idx + len(query) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"


def search_conversations(
    db: Session, user_id: str, query: str, limit: int = 50
) -> list[dict]:
    """Search a user's chats across titles and message contents.

    Returns [{conv, message_id, snippet_role, snippet}] deduped by conversation,
    ordered by most recent match.
    """
    q = (query or "").strip()
    if not q:
        return []
    pattern = f"%{q}%"
    rows = db.execute(
        select(
            Conversation.conversation_id,
            Message.message_id,
            Message.role,
            Message.content,
        )
        .outerjoin(Message, Message.conversation_id == Conversation.conversation_id)
        .where(Conversation.user_id == user_id)
        .where(or_(Conversation.title.ilike(pattern), Message.content.ilike(pattern)))
        .order_by(Message.created_at.desc())
        .limit(limit)
    ).all()

    results: list[dict] = []
    seen: set[str] = set()
    for conv_id, msg_id, role, content in rows:
        if conv_id in seen:
            continue
        seen.add(conv_id)
        conv = db.get(Conversation, conv_id)
        if conv is None:
            continue
        results.append(
            {
                "conv": conv,
                "message_id": msg_id,
                "snippet_role": role or "",
                "snippet": "" if msg_id is None else _snippet(content, q),
            }
        )
    return results


def touch_conversation(db: Session, conversation_id: str) -> None:
    conv = db.get(Conversation, conversation_id)
    if conv:
        conv.updated_at = _now()
        db.commit()


def delete_conversation(db: Session, conversation_id: str, user_id: str) -> bool:
    result = db.execute(
        delete(Conversation).where(
            Conversation.conversation_id == conversation_id, Conversation.user_id == user_id
        )
    )
    db.execute(delete(ConversationMemory).where(ConversationMemory.conversation_id == conversation_id))
    db.commit()
    return result.rowcount > 0


def delete_all_conversations(db: Session, user_id: str) -> int:
    conv_ids = list(
        db.scalars(select(Conversation.conversation_id).where(Conversation.user_id == user_id))
    )
    if not conv_ids:
        db.commit()
        return 0

    msg_ids = list(
        db.scalars(select(Message.message_id).where(Message.conversation_id.in_(conv_ids)))
    )
    if msg_ids:
        db.execute(delete(Source).where(Source.message_id.in_(msg_ids)))
        db.execute(delete(Message).where(Message.conversation_id.in_(conv_ids)))
    db.execute(delete(ToolRun).where(ToolRun.conversation_id.in_(conv_ids)))
    db.execute(delete(Feedback).where(Feedback.conversation_id.in_(conv_ids)))
    db.execute(delete(ConversationMemory).where(ConversationMemory.conversation_id.in_(conv_ids)))
    db.execute(delete(Conversation).where(Conversation.user_id == user_id))
    db.commit()
    return len(conv_ids)


def count_conversations(db: Session, user_id: str) -> int:
    return db.scalar(select(Conversation).where(Conversation.user_id == user_id).count())


# ---------- messages ----------
def add_message(
    db: Session,
    conversation_id: str,
    role: str,
    content: str,
    intent: str = "",
    metadata: dict | None = None,
) -> Message:
    msg = Message(
        message_id=new_id(),
        conversation_id=conversation_id,
        role=role,
        content=content,
        intent=intent,
        metadata_json=json.dumps(metadata or {}) if metadata else "{}",
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def message_metadata(msg: Message) -> dict:
    try:
        return json.loads(msg.metadata_json or "{}") if isinstance(msg.metadata_json, str) else {}
    except (TypeError, ValueError):
        return {}


def list_messages(db: Session, conversation_id: str, limit: int | None = None) -> list[Message]:
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt))


def history_messages(db: Session, conversation_id: str) -> list[Message]:
    msgs = list_messages(db, conversation_id)
    return msgs[-settings.max_history_messages * 2 :]


def count_messages(db: Session, conversation_id: str) -> int:
    return db.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.conversation_id == conversation_id)
    )


# ---------- sources ----------
def add_sources(db: Session, message_id: str, sources: list[dict]) -> None:
    for s in sources:
        db.add(
            Source(
                source_id=new_id(),
                message_id=message_id,
                title=s.get("title", ""),
                url=s.get("url", ""),
                source_type=s.get("source_type", "web"),
                quality=s.get("quality", "unknown"),
                snippet=s.get("snippet", ""),
            )
        )
    db.commit()


def list_sources(db: Session, message_id: str) -> list[Source]:
    return list(
        db.scalars(select(Source).where(Source.message_id == message_id).order_by(Source.source_id))
    )


# ---------- tool runs ----------
def add_tool_runs(db: Session, conversation_id: str, runs: list[dict]) -> None:
    for r in runs:
        db.add(
            ToolRun(
                id=new_id(),
                conversation_id=conversation_id,
                name=r.get("name", ""),
                status=r.get("status", "success"),
                latency_ms=int(r.get("latency_ms", 0)),
                detail_json=json.dumps(r.get("detail", {}), default=str),
                started_at=datetime.fromisoformat(r["started_at"]) if r.get("started_at") else _now(),
                finished_at=datetime.fromisoformat(r["finished_at"]) if r.get("finished_at") else None,
            )
        )
    db.commit()


# ---------- documents ----------
def get_document(db: Session, document_id: str, user_id: str) -> Document | None:
    return db.scalar(
        select(Document).where(
            Document.document_id == document_id, Document.user_id == user_id
        )
    )


def list_documents(db: Session, user_id: str) -> list[Document]:
    return list(
        db.scalars(select(Document).where(Document.user_id == user_id).order_by(Document.uploaded_at.desc()))
    )


def list_documents_by_ids(db: Session, document_ids: list[str]) -> list[Document]:
    if not document_ids:
        return []
    return list(db.scalars(select(Document).where(Document.document_id.in_(document_ids))))


def create_document(
    db: Session, user_id: str, filename: str, content_type: str, size_bytes: int
) -> Document:
    doc = Document(
        document_id=new_id(),
        user_id=user_id,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def save_chunks(db: Session, document_id: str, chunks: list[tuple[int, str, dict]]) -> None:
    for page, text, meta in chunks:
        db.add(
            DocumentChunk(
                # Primary key must match the id registered in the vector store
                # (ingestion already puts `chunk_id` + `document_id` in meta).
                chunk_id=meta.get("chunk_id") or new_id(),
                document_id=document_id,
                page=page,
                text=text,
                meta_json=json.dumps(meta, default=str),
            )
        )
    db.commit()


def patch_document_chunk_count(db: Session, document_id: str, count: int) -> None:
    doc = db.get(Document, document_id)
    if doc:
        doc.chunks = count
        db.commit()


def delete_document(db: Session, document_id: str, user_id: str) -> bool:
    doc = get_document(db, document_id, user_id)
    if doc is None:
        return False
    db.delete(doc)
    db.commit()
    return True


# ---------- feedback ----------
def add_feedback(
    db: Session, conversation_id: str, message_id: str, rating: str, comment: str
) -> Feedback:
    fb = Feedback(
        id=new_id(),
        conversation_id=conversation_id,
        message_id=message_id,
        rating=rating,
        comment=comment,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return fb


# ---------- long-term user memory ----------
def list_memory(db: Session, user_id: str) -> list[UserMemory]:
    return list(
        db.scalars(
            select(UserMemory)
            .where(UserMemory.user_id == user_id)
            .order_by(UserMemory.updated_at.desc())
        )
    )


def memory_dict(db: Session, user_id: str) -> dict[str, str]:
    return {m.key: m.value for m in list_memory(db, user_id)}


def upsert_memory(db: Session, user_id: str, facts: list[dict]) -> list[UserMemory]:
    """Insert or update memory facts: [{"key": "name", "value": "Abdullah"}, ...]."""
    get_or_create_user(db, user_id)
    saved: list[UserMemory] = []
    for fact in facts:
        key = str(fact.get("key", "")).strip().lower()[:80]
        value = str(fact.get("value", "")).strip()[:2000]
        if not key or not value:
            continue
        row = db.scalar(
            select(UserMemory).where(
                UserMemory.user_id == user_id, UserMemory.key == key
            )
        )
        if row is None:
            row = UserMemory(id=new_id(), user_id=user_id, key=key, value=value)
            db.add(row)
        else:
            row.value = value
            row.updated_at = _now()
        saved.append(row)
    if saved:
        db.commit()
    return saved


def clear_memory(db: Session, user_id: str) -> int:
    res = db.execute(delete(UserMemory).where(UserMemory.user_id == user_id))
    db.commit()
    return res.rowcount or 0


# ---------- chat-scoped memory (ISOLATED PER CONVERSATION uuid) ----------
def list_conversation_memory(db: Session, conversation_id: str) -> list[ConversationMemory]:
    return list(
        db.scalars(
            select(ConversationMemory)
            .where(ConversationMemory.conversation_id == conversation_id)
            .order_by(ConversationMemory.updated_at.desc())
        )
    )


def conversation_memory_dict(db: Session, conversation_id: str) -> dict[str, str]:
    return {m.key: m.value for m in list_conversation_memory(db, conversation_id)}


def upsert_conversation_memory(
    db: Session, conversation_id: str, facts: list[dict]
) -> list[ConversationMemory]:
    """Insert or update chat-scoped facts: [{"key": "name", "value": "Abdullah"}, ...].

    Rows are keyed by conversation_id only, so a fact learned in one chat never
    leaks into any other chat/tab.
    """
    saved: list[ConversationMemory] = []
    for fact in facts:
        key = str(fact.get("key", "")).strip().lower()[:80]
        value = str(fact.get("value", "")).strip()[:2000]
        if not key or not value:
            continue
        row = db.scalar(
            select(ConversationMemory).where(
                ConversationMemory.conversation_id == conversation_id,
                ConversationMemory.key == key,
            )
        )
        if row is None:
            row = ConversationMemory(
                id=new_id(), conversation_id=conversation_id, key=key, value=value
            )
            db.add(row)
        else:
            row.value = value
            row.updated_at = _now()
        saved.append(row)
    if saved:
        db.commit()
    return saved


def clear_conversation_memory(db: Session, conversation_id: str) -> int:
    res = db.execute(
        delete(ConversationMemory).where(ConversationMemory.conversation_id == conversation_id)
    )
    db.commit()
    return res.rowcount or 0