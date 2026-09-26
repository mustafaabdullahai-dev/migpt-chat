"""Structured observability per request (SPEC §59).

Every chat/research request gets a RequestSpan; end() emits one structured JSON log
line with ids, query, intent, tools used + latencies, search/sources counts, model,
total latency and errors. Secrets are masked and the context is cleared on end().
"""

from app.services.observability import begin, current, end, mask_secrets


def test_mask_secrets_removes_credentials():
    assert "abc123" not in mask_secrets("api_key=abc123-xyz").lower()
    assert "hunter2" not in mask_secrets("password=hunter2")
    assert "tok123" not in mask_secrets("token = tok123 ")
    assert "xyz123abc" not in mask_secrets("Authorization: Bearer xyz123abc").lower()


def test_mask_secrets_masks_and_marks():
    masked = mask_secrets("api_key=sk-mysecretkey123")
    assert "[REDACTED]" in masked
    actual_token = ("password= hunter2" and "hunter2")
    assert actual_token not in mask_secrets("password= hunter2")


def test_mask_secrets_keeps_plain_text():
    text = "What is FastAPI and how does it compare to Flask?"
    assert mask_secrets(text) == text


def test_span_records_tools_counts_and_clears_context():
    span = begin(
        request_type="chat",
        conversation_id="conv-1",
        user_id="user-1",
        query="tell me about X",
        mode="web",
    )
    assert current() is span
    span.record_tool("web_search", "success", latency_ms=220)
    span.record_tool("web_search", "success", latency_ms=180)
    span.record_tool("fetch_webpage", "success", latency_ms=150)
    span.sources_count = 3

    summary = end()
    assert current() is None  # context cleared

    assert summary["request_id"]
    assert summary["request_type"] == "chat"
    assert summary["conversation_id"] == "conv-1"
    assert summary["user_id"] == "user-1"
    assert summary["query"] == "tell me about X"
    assert summary["mode"] == "web"
    assert summary["tools_used"] == ["web_search", "web_search", "fetch_webpage"]
    assert summary["tool_latency_ms"] == [220, 180, 150]
    assert summary["search_count"] == 2
    assert summary["sources_count"] == 3
    assert summary["total_latency_ms"] >= 0
    assert summary["status"] == "ok"
    assert summary["model"]


def test_span_error_status_records_errors():
    span = begin(query="boom query")
    span.status = "error"
    span.errors.append("graph exploded")
    summary = end()
    assert summary["status"] == "error"
    assert summary["errors"] == ["graph exploded"]


def test_query_secret_is_masked_in_summary():
    span = begin(query="what is my password=hunter2 friend")
    summary = end()
    assert "hunter2" not in summary["query"]
    assert summary["query"].startswith("what is my passwo[REDACTED]")


def test_end_without_span_is_safe():
    assert end() is None
    assert current() is None