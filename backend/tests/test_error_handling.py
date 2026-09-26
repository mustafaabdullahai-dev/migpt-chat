"""Spec tests: ERROR HANDLING + SECURITY hardening.

Covers: clean errors to end users (no stack traces / internals leaked), detailed
server-side logging of unhandled errors, graceful document-upload failures, and the
synthesize streaming retry with exponential backoff on transient LLM failures.
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage

from app.main import unhandled_exception_handler  # noqa: E402 (module import also wires logging)


# ------------------------- global exception handler -------------------------


def test_unhandled_exception_returns_generic_detail(caplog):
    import logging

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    caplog.set_level(logging.ERROR)
    app = FastAPI()
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internal path /home/me/app.db")

    res = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert res.status_code == 500
    assert res.json() == {"detail": "Internal server error"}
    assert "secret internal path" not in res.text
    assert any("unhandled error" in r.getMessage() for r in caplog.records)


# ------------------------- document upload graceful failure -------------------------


def test_document_upload_does_not_leak_internal_error(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("internal ingest SECRET_index_path")

    monkeypatch.setattr("app.api.documents.ingest_bytes", boom)
    res = client.post(
        "/api/documents/upload",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )
    assert res.status_code == 400
    assert "SECRET_index_path" not in res.text
    assert "Could not process this file" in res.json()["detail"]


# ------------------------- synthesize streaming retry -------------------------


class _FlakyStreamer:
    """Minimal stand-in for a chat model whose astream fails transiently."""

    def __init__(self, fail_attempts=2, out="final answer"):
        self.fail_attempts = fail_attempts
        self.calls = 0
        self.out = out

    async def astream(self, messages):
        self.calls += 1
        if self.calls <= self.fail_attempts:
            raise ValueError("temporary rate limit")
        yield AIMessage(content=self.out)


class _PartialThenFail:
    """Fails AFTER emitting a token — must NOT be retried."""

    def __init__(self):
        self.calls = 0

    async def astream(self, messages):
        self.calls += 1
        yield AIMessage(content="partial ")
        raise RuntimeError("mid-stream failure")


def _empty_state():
    return {"query": "hi", "intent": "GENERAL_CHAT", "evidence": [], "history": []}


def test_synthesize_retries_transient_failure_then_succeeds(monkeypatch):
    from app.agents.nodes.synthesize import synthesize_answer

    flaky = _FlakyStreamer(fail_attempts=2, out="retried answer")
    monkeypatch.setattr("app.agents.nodes.synthesize.get_llm", lambda **kw: flaky)

    result = asyncio.run(synthesize_answer(_empty_state()))
    assert flaky.calls == 3
    assert result.get("errors", []) == []
    assert "retried answer" in result["answer"]


def test_synthesize_does_not_retry_after_partial_stream(monkeypatch):
    from app.agents.nodes.synthesize import synthesize_answer

    partial = _PartialThenFail()
    monkeypatch.setattr("app.agents.nodes.synthesize.get_llm", lambda **kw: partial)

    result = asyncio.run(synthesize_answer(_empty_state()))
    assert partial.calls == 1  # no retry once tokens were emitted
    assert result["errors"]


def test_synthesize_returns_fallback_after_exhausted_retries(monkeypatch):
    from app.agents.nodes.synthesize import synthesize_answer

    always = _FlakyStreamer(fail_attempts=99, out="never")
    monkeypatch.setattr("app.agents.nodes.synthesize.get_llm", lambda **kw: always)

    result = asyncio.run(synthesize_answer(_empty_state()))
    assert always.calls == 3
    assert result["errors"]
    assert "language model" in result["answer"].lower() or "couldn't" in result["answer"].lower()