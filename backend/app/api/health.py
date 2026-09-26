"""Feedback and health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import resolve_user
from app.db.base import get_db, SessionLocal
from app.db.crud import add_feedback, get_conversation
from app.models.schemas import FeedbackIn

router = APIRouter()


@router.post("/api/feedback", status_code=201)
def feedback(
    body: FeedbackIn,
    user_id: str = Depends(resolve_user),
    db: Session = Depends(get_db),
):
    if not get_conversation(db, body.conversation_id, user_id):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Conversation not found")
    add_feedback(db, body.conversation_id, body.message_id, body.rating, body.comment.strip())
    return {"ok": True}


@router.get("/api/health")
def health():
    db_ok = True
    try:
        with SessionLocal() as db:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception:
        db_ok = False

    from app.core.config import settings
    from app.services.llm import provider_name
    from app.tools.base import registry

    return {
        "status": "ok" if db_ok else "degraded",
        "llm": provider_name(),
        "web_search": settings.enable_web_search,
        "calculator": settings.enable_calculator,
        "document_rag": settings.enable_document_rag,
        "tool_count": len(registry.names),
        "database": "sqlite" if settings.database_url.startswith("sqlite") else "postgres",
        "demo_mode": settings.is_demo,
    }