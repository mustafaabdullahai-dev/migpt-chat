"""Time/date tool tests: server-clock formats, timezone extraction, DATETIME routing."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agents.nodes.datetime import _extract_timezone
from app.agents.routers.classifier import classify
from app.agents.routers.router import route_query
from app.agents.state import AgentState
from app.tools.datetime_tool import current_date, current_time


def test_current_time_and_date_use_server_clock():
    now = datetime.now(ZoneInfo("UTC"))
    assert current_date("UTC") == now.strftime("%Y-%m-%d")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [A-Za-z]+ \([+-]\d{4}\)", current_time("UTC"))


def test_timezone_specific():
    karachi = datetime.now(ZoneInfo("Asia/Karachi")).strftime("%Y-%m-%d %H:%M:%S")
    assert current_time("Asia/Karachi")[:19] == karachi
    assert current_date("Asia/Karachi") == datetime.now(ZoneInfo("Asia/Karachi")).strftime("%Y-%m-%d")


def test_unknown_timezone_raises():
    with pytest.raises(ValueError, match="Unknown timezone"):
        current_time("Mars/Olympus")


def test_timezone_extraction():
    assert _extract_timezone("what time is it in Karachi?") == "Asia/Karachi"
    assert _extract_timezone("what time is it in New York?") == "America/New_York"
    assert _extract_timezone("current time in Tokyo now") == "Asia/Tokyo"
    assert _extract_timezone("time in Pacific/Auckland") == "Pacific/Auckland"
    assert _extract_timezone("hello") == "UTC"


def test_classify_date_time_intents():
    r = classify("What is today's date?")
    assert r.intent == "DATETIME"
    assert "get_current_time" in r.tools and "get_current_date" in r.tools
    assert classify("what time is it in Asia/Karachi?").intent == "DATETIME"
    assert classify("what's today's date in London").intent == "DATETIME"
    assert classify("what day is it?").intent == "DATETIME"


def test_classify_not_time_when_duration():
    r = classify("How much time does it take to read a 400 page book?")
    assert r.intent != "DATETIME"


def test_router_routes_datetime_to_node():
    assert route_query(AgentState(intent="DATETIME")) == "datetime"


@pytest.mark.asyncio
async def test_graph_datetime_path(graph):
    state = await graph.ainvoke({
        "query": "What is today's date?",
        "conversation_id": "test-conv",
        "user_id": "tester",
        "mode": "auto",
        "documents": [],
        "history": [],
        "intent": "",
        "confidence": 1.0,
        "errors": [],
        "tool_runs": [],
        "iterations": 0,
    })
    assert state.get("intent") == "DATETIME"
    result = state.get("datetime_result", "")
    assert "Timezone:" in result and "Local date:" in result
    assert datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d") in result
    assert state.get("answer")