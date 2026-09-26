"""Pytest fixtures/config. Runs tests fully offline (demo mode, mocked search)."""

from __future__ import annotations

import os

os.environ["DEMO_MODE"] = "true"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["SEARCH_PROVIDER"] = "mock"
os.environ["ENABLE_WEB_SEARCH"] = "true"
os.environ["ENABLE_FOLLOWUPS"] = "false"
os.environ["ENABLE_CLAIM_VERIFICATION"] = "false"
os.environ["ENABLE_CACHE"] = "false"
os.environ["REDIS_URL"] = ""
os.environ["DATA_DIR"] = "/tmp/chatgpt-test-data"
os.environ["DATABASE_URL"] = "sqlite:////tmp/chatgpt-test.db"
os.environ["GROQ_API_KEY"] = ""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _clean_test_state():
    """Reset persisted DB + FAISS state so every test session starts deterministic
    and the FAISS index (rebuilt on every document delete) stays small/fast."""
    import shutil

    shutil.rmtree("/tmp/chatgpt-test-data", ignore_errors=True)
    for p in Path("/tmp").glob("chatgpt-test.db*"):
        p.unlink(missing_ok=True)
    yield


@pytest.fixture()
def client():
    from app.db.base import init_db

    init_db()
    from app.main import app as fastapi_app
    from app.agents.graph import build_graph

    build_graph()
    import app.tools.registry  # noqa: F401

    fastapi_app.router.lifespan_context = None
    return TestClient(fastapi_app)


@pytest.fixture()
def graph():
    from app.agents.graph import build_graph

    return build_graph()


@pytest.fixture()
def search_mock(monkeypatch):
    """Ensures search + page fetching are fully offline/deterministic."""
    from app.models.schemas import PageContent
    from app.services.search import MockSearchProvider, get_search_provider
    from app.tools.webpage import WebpageTool

    monkeypatch.setattr("app.services.search._search", MockSearchProvider())

    async def fake_fetch(self, url):
        return PageContent(
            url=url,
            title="Mock Source",
            text="This is deterministic mock content used by tests. LangGraph is an orchestration framework.",
            headings=["Overview"],
        )

    monkeypatch.setattr(WebpageTool, "__call__", fake_fetch)


@pytest.fixture()
def search_mock_fallback(monkeypatch):
    """Extreme fallback: even the web_search tool returns canned results."""
    from app.services.search import MockSearchProvider
    from app.tools.web_search import web_search_tool

    provider = MockSearchProvider()

    async def fake_call(self, query, limit=None):
        return await provider.search(query, limit or 8)

    monkeypatch.setattr(web_search_tool, "__call__", fake_call)