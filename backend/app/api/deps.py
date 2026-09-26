"""FastAPI dependencies: db session, user identity, rate limiting, request id."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import InMemoryRateLimiter, new_request_id
from app.db.base import get_db
from app.db.crud import get_or_create_user

logger = get_logger("api.deps")

_limiter = InMemoryRateLimiter(max_requests=40, window_seconds=60)


def get_user_id(x_user_id: str = Header(default="local")) -> str:
    uid = x_user_id.strip()[:64]
    if not uid:
        raise HTTPException(status_code=400, detail="Empty user id")
    return uid


def enforce_rate_limit(request: Request, user_id: str = Depends(get_user_id)) -> str:
    if not _limiter.allow(user_id):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment.")
    return user_id


def request_id() -> str:
    return new_request_id()


def resolve_user(db: Session = Depends(get_db), user_id: str = Depends(get_user_id)) -> str:
    get_or_create_user(db, user_id)
    return user_id