"""Security helpers: URL validation / SSRF guards, upload validation, rate limiting."""

from __future__ import annotations

import ipaddress
import socket
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

from app.core.logging import get_logger

logger = get_logger("security")

_ALLOWED_SCHEMES = {"http", "https"}
_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown", ".docx", ".csv"}
_MAX_BYTES_PEEK = 4096


def validate_url(url: str, require_resolution: bool = True) -> str:
    """Validates an http(s) URL and rejects connections to private/local hosts (SSRF guard).

    With require_resolution=False the scheme/host/private-net checks still run, but a
    temporary DNS failure is allowed through (used for citation validation, where the
    source already came from a search engine and the fetch step decides reachability).
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Disallowed URL scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")

    hostname = parsed.hostname.rstrip(".")

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:  # DNS failure
        if require_resolution:
            raise ValueError(f"Could not resolve host: {hostname}") from exc
        return url

    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if any(addr in net for net in _PRIVATE_NETS):
            raise ValueError(f"Blocked private/loopback address: {hostname}")
    return url


def sanitize_sse(data: str) -> str:
    return data.replace("\r", " ").replace("\n", " ")


def validate_filename(filename: str) -> str:
    name = Path(filename).name  # strip any directory traversal
    if name != filename:
        raise ValueError("Filename must not contain path separators")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError(f"File type not allowed: {ext}")
    return name


def sniff_file_signature(content: bytes, ext: str) -> bool:
    """Light magic-byte check for supported uploads."""
    if ext == ".pdf":
        return content.startswith(b"%PDF")
    if ext == ".docx":
        return b"PK\x03\x04" in content[:512]
    if ext in (".txt", ".md", ".markdown", ".csv"):
        return b"\x00" not in content[:4096]
    return True


class InMemoryRateLimiter:
    """Simple sliding-window rate limiter (process-local). Swap for Redis in production."""

    def __init__(self, max_requests: int = 30, window_seconds: int = 60) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._max = max_requests
        self._window = window_seconds

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and bucket[0] < now - self._window:
            bucket.popleft()
        if len(bucket) >= self._max:
            return False
        bucket.append(now)
        return True


class MemoryKeyValue:
    """A tiny thread/use-safe local cache primitive (placeholder for Redis)."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, object]] = {}
        self._lock = _thread_lock()
        self._ttl = 300.0

    def get(self, key: str) -> object | None:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            created, value = item
            if time.monotonic() - created > self._ttl:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: object, ttl: float = 300.0) -> None:
        with self._lock:
            self._store[key] = (time.monotonic(), value)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)


def _thread_lock():
    from threading import Lock

    return Lock()


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]