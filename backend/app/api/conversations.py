"""Conversation history endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import resolve_user
from app.db.base import get_db
from app.db.crud import (
    add_message,
    conv_metadata,
    conv_stats,
    count_conversations,
    create_conversation,
    delete_all_conversations,
    delete_conversation,
    get_conversation,
    list_conversations,
    list_messages,
    list_sources,
    patch_conversation_metadata,
    rename_conversation,
    search_conversations,
)
from app.models.schemas import (
    Attachment,
    ConversationOut,
    ConversationSearchHit,
    ConversationUpdate,
    MessageOut,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _conv_out(conv, stats: dict | None = None) -> ConversationOut:
    st = (stats or {}).get(conv.conversation_id, {}) if stats else {}
    if stats is None:
        st = {"count": getattr(conv, "_msg_count", 0), "last": getattr(conv, "_last_message", None)}
    return ConversationOut(
        conversation_id=conv.conversation_id,
        user_id=conv.user_id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        metadata=conv_metadata(conv),
        summary=conv.summary or "",
        message_count=int(st.get("count", 0)),
        last_message=(st.get("last") or None),
    )


def _msg_out(msg, db: Session) -> MessageOut:
    from app.db.crud import message_metadata

    attachments = message_metadata(msg).get("attachments", []) or []
    return MessageOut(
        message_id=msg.message_id,
        role=msg.role,
        content=msg.content,
        created_at=msg.created_at,
        intent=msg.intent,
        attachments=[Attachment(**a) for a in attachments if isinstance(a, dict)],
        sources=[],
    )


@router.get("/search", response_model=list[ConversationSearchHit])
def search(
    q: str = Query(min_length=1, max_length=200),
    user_id: str = Depends(resolve_user),
    db: Session = Depends(get_db),
):
    hits = search_conversations(db, user_id, q, limit=50)
    convs = [h["conv"] for h in hits]
    stats = conv_stats(db, [c.conversation_id for c in convs])
    return [
        ConversationSearchHit(
            conversation=_conv_out(h["conv"], {h["conv"].conversation_id: stats.get(h["conv"].conversation_id, {})}),
            message_id=h["message_id"],
            snippet_role=h["snippet_role"],
            snippet=h["snippet"],
        )
        for h in hits
    ]


@router.get("", response_model=list[ConversationOut])
def list_convs(user_id: str = Depends(resolve_user), db: Session = Depends(get_db)):
    convs = list_conversations(db, user_id, limit=200)
    stats = conv_stats(db, [c.conversation_id for c in convs])
    return [_conv_out(c, {c.conversation_id: stats.get(c.conversation_id, {})}) for c in convs]


@router.post("", response_model=ConversationOut)
def create_conv(user_id: str = Depends(resolve_user), db: Session = Depends(get_db)):
    conv = create_conversation(db, user_id)
    return _conv_out(conv)


@router.delete("", status_code=204)
def clear_all(user_id: str = Depends(resolve_user), db: Session = Depends(get_db)):
    delete_all_conversations(db, user_id)
    return None


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_conv(conversation_id: str, user_id: str = Depends(resolve_user), db: Session = Depends(get_db)):
    conv = get_conversation(db, conversation_id, user_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    stats = conv_stats(db, [conv.conversation_id])
    return _conv_out(conv, {conv.conversation_id: stats.get(conv.conversation_id, {})})


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def get_messages(conversation_id: str, user_id: str = Depends(resolve_user), db: Session = Depends(get_db)):
    conv = get_conversation(db, conversation_id, user_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = list_messages(db, conversation_id)
    return [_msg_out(m, db) for m in msgs]


@router.patch("/{conversation_id}", response_model=ConversationOut)
def update_conv(
    conversation_id: str,
    body: ConversationUpdate,
    user_id: str = Depends(resolve_user),
    db: Session = Depends(get_db),
):
    conv = get_conversation(db, conversation_id, user_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if body.title is not None:
        conv = rename_conversation(db, conversation_id, body.title.strip() or "Untitled")
    if body.metadata is not None:
        conv = patch_conversation_metadata(db, conversation_id, body.metadata)
    if body.summary is not None:
        conv.summary = body.summary[:8000]
        db.commit()
        db.refresh(conv)
    stats = conv_stats(db, [conv.conversation_id])
    return _conv_out(conv, {conv.conversation_id: stats.get(conv.conversation_id, {})})


@router.delete("/{conversation_id}", status_code=204)
def delete_conv(conversation_id: str, user_id: str = Depends(resolve_user), db: Session = Depends(get_db)):
    if not delete_conversation(db, conversation_id, user_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return None