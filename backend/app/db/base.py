"""SQLAlchemy engine/session + declarative base (sync; SQLite local, PG-compatible)."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _engine_url() -> str:
    url = settings.database_url
    # force check_same_thread off for FastAPI threadpool usage
    if url.startswith("sqlite"):
        return url
    return url


engine = create_engine(
    _engine_url(),
    connect_args={"check_same_thread": False} if _engine_url().startswith("sqlite") else {},
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _auto_migrate(bind=None) -> None:
    """Idempotent column migration for existing databases opened by create_all."""
    from sqlalchemy import inspect, text

    target = bind or engine
    try:
        insp = inspect(target)
        tables = set(insp.get_table_names())
        if "conversations" not in tables:
            return
        cols = {c["name"] for c in insp.get_columns("conversations")}
        with target.begin() as conn:
            if "summary" not in cols:
                conn.execute(text("ALTER TABLE conversations ADD COLUMN summary TEXT DEFAULT ''"))
            if "metadata_json" not in cols:
                conn.execute(text("ALTER TABLE conversations ADD COLUMN metadata_json TEXT DEFAULT '{}'"))
        if "messages" in tables:
            msg_cols = {c["name"] for c in insp.get_columns("messages")}
            if "metadata_json" not in msg_cols:
                with target.begin() as conn:
                    conn.execute(text("ALTER TABLE messages ADD COLUMN metadata_json TEXT DEFAULT '{}'"))
    except Exception:
        from app.core.logging import get_logger

        get_logger("base.migrate").exception("auto migration failed; continuing with current schema")


def init_db() -> None:
    from app.db import models  # noqa: F401  (register tables)

    Base.metadata.create_all(bind=engine)
    _auto_migrate()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def session_scope():
    """Context-managed session for use outside FastAPI dependencies."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()