"""Structured logging configuration.

Never log secrets. Logs include request/conversation ids for traceability.
"""

from __future__ import annotations

import json
import logging
import sys

_RESERVED = {"password", "token", "secret", "key", "api_key", "authorization"}


def _sanitize(record: logging.LogRecord) -> logging.LogRecord:
    if not record.args:
        return record
    if isinstance(record.msg, str) and record.msg.count("%s") + record.msg.count("%d"):
        return record
    return record


class SafeJSONFormatter(logging.Formatter):
    """Emits a single line JSON structured log while dropping sensitive keys."""

    def format(self, record: logging.LogRecord) -> str:
        payload = record.__dict__.copy()
        payload["level"] = record.levelname
        payload["logger"] = record.name
        try:
            from app.services.observability import request_id_for_log

            rid = request_id_for_log()
        except Exception:
            rid = ""
        if rid:
            payload["request_id"] = rid
        for key in list(payload):
            if any(s in key.lower() for s in _RESERVED) and isinstance(payload[key], str):
                # Redact credential-*strings*; numeric metrics (token counts, latencies)
                # and nulls ("token usage not available") pass through.
                payload[key] = "[REDACTED]"
        payload = {k: v for k, v in payload.items() if isinstance(v, (str, int, float, bool, type(None), list, dict))}
        payload.pop("message", None)
        if isinstance(record.msg, str):
            payload["message"] = record.getMessage()
        return json.dumps(payload, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(SafeJSONFormatter())
    if root.handlers:
        root.handlers.clear()
    root.addHandler(handler)

    for noisy in ("httpx", "httpx2", "httpcore", "faiss", "asyncio", "ddgs", "primp", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("app").setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"app.{name}")


def log_event(logger: logging.Logger, event: str, **fields: object) -> None:
    logger.info("event=%s", event, extra=fields)  # noqa: G004