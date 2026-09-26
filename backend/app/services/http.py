"""Shared outbound HTTP client — one connection pool for search + page fetch.

Reusing a single httpx.AsyncClient keeps TCP/TLS connections alive across requests
(connection reuse) instead of opening a fresh client per call.
"""

from __future__ import annotations

import httpx

from app.core.logging import get_logger

logger = get_logger("http")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Lazily builds a shared, connection-reusing AsyncClient."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15, connect=5),
            follow_redirects=True,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=32),
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # pragma: no cover - best-effort shutdown
            pass
        _client = None