"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.research import router as research_router
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.base import init_db

setup_logging()
logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("startup complete", extra={"event": "app.startup", "env": settings.app_env, "demo": settings.is_demo})
    yield


app = FastAPI(
    title="Agentic Search & Reasoning Engine",
    version="1.0.0",
    description="ChatGPT-style agentic AI search and reasoning platform built on LangChain + LangGraph + Groq.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for local dev; scope to a domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(research_router)
app.include_router(documents_router)


@app.get("/", include_in_schema=False)
def root():
    return {"name": "Agentic Search & Reasoning Engine", "version": "1.0.0", "docs": "/docs"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Log the full traceback server-side; return a clean, generic detail to the client.
    # Never expose stack traces / internals to end users (see ERROR HANDLING spec).
    logger.exception(
        "unhandled error",
        extra={"event": "error.unhandled", "path": request.url.path, "method": request.method},
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})