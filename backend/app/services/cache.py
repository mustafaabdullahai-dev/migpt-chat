"""Cache abstraction.

    CacheBackend
        ├── InMemoryCache
        └── RedisCache (optional, enabled via CACHE_BACKEND=redis)
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("cache")

T = TypeVar("T")


class _FriendlyEncoder(json.JSONEncoder):
    """JSON-encodes the values the pipeline caches (pydantic models, datetime…)."""

    def default(self, o: Any) -> Any:
        if isinstance(o, BaseModel):
            return o.model_dump(mode="json")
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, (set, tuple)):
            return list(o)
        if isinstance(o, bytes):
            return o.decode("utf-8", "replace")
        return str(o)


class CacheBackend(ABC):
    @abstractmethod
    def get(self, key: str) -> object | None:
        ...

    @abstractmethod
    def set(self, key: str, value: object, ttl: int = 300) -> None:
        ...


class InMemoryCache(CacheBackend):
    def __init__(self, capacity: int = 2048, default_ttl: int = 300) -> None:
        self._store: dict[str, tuple[float, object, float]] = {}
        self._capacity = capacity
        self._default_ttl = default_ttl

    def get(self, key: str) -> object | None:
        item = self._store.get(key)
        if item is None:
            return None
        created, value, expires = item
        if time.monotonic() >= expires:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object, ttl: int = 300) -> None:
        if len(self._store) >= self._capacity and key not in self._store:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
        self._store[key] = (time.monotonic(), value, time.monotonic() + (ttl or self._default_ttl))


class RedisCache(CacheBackend):
    """Redis backend. Values are JSON-serialized so arbitrary payloads (lists of
    pydantic models, dicts, floats) round-trip; pure strings stay exact."""

    def __init__(self, url: str) -> None:
        import redis

        # blocking client: the CacheBackend interface is synchronous
        self._client = redis.Redis.from_url(url)

    @staticmethod
    def _pack(value: object) -> str:
        if isinstance(value, str):
            return "s:" + value
        return "j:" + json.dumps(value, cls=_FriendlyEncoder)

    @staticmethod
    def _unpack(raw: str) -> object | None:
        if raw.startswith("j:"):
            try:
                return json.loads(raw[2:])
            except Exception:
                return None
        if raw.startswith("s:"):
            return raw[2:]
        return raw

    def get(self, key: str) -> object | None:
        value = self._client.get(key)
        if isinstance(value, bytes):
            return self._unpack(value.decode("utf-8", "replace"))
        if isinstance(value, str):
            return self._unpack(value)
        return value

    def set(self, key: str, value: object, ttl: int = 300) -> None:
        self._client.set(key, self._pack(value), ex=int(ttl or 300))


_cache: CacheBackend | None = None


def reset_cache() -> None:
    """Drop the memoized backend so the next get_cache() call re-reads settings."""
    global _cache
    _cache = None


def get_cache() -> CacheBackend | None:
    global _cache
    if not settings.enable_cache:
        return None
    if _cache is not None:
        return _cache
    if settings.redis_url:
        try:
            _cache = RedisCache(settings.redis_url)
            logger.info("Redis cache enabled", extra={"event": "cache.init"})
            return _cache
        except Exception:  # pragma: no cover - depends on env
            logger.warning("Redis unavailable; falling back to in-memory cache", extra={"event": "cache.redis_failed"})
    _cache = InMemoryCache()
    return _cache


def cache_key(*parts: object) -> str:
    """Deterministic hashed cache key.

    Structural parts (strings) are normalized — trimmed, whitespace-collapsed and
    lowercased — so logically-identical inputs share one entry. Non-string parts
    (ints, floats) are passed through verbatim to keep them distinct.
    """
    def _norm(p: object) -> str:
        if isinstance(p, str):
            return " ".join(p.strip().split()).lower()
        return str(p)

    raw = "|".join(_norm(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def reply_cache_key(query: str, mode: str) -> str:
    """Deterministic key for idempotent (offline, LLM-free) replies."""
    norm = " ".join(str(query).lower().split())
    return cache_key("reply", mode, norm)


async def cached_call(
    cache: CacheBackend | None,
    key: str,
    fetch: Callable[[], T],
    ttl: int = 600,
    decode: Callable[[object], object] | None = None,
    cache_empty: bool = True,
    empty_ttl: int = 60,
) -> T:
    """Fetch-with-cache. Optional `decode` revives values that a Redis backend
    returns as plain JSON (idempotent for in-memory objects).

    `cache_empty=False` skips caching falsy results (transient failures must be
    retried, not pinned). If `cache_empty=True`, empty results are cached with the
    shorter `empty_ttl` so a permanently-dead primary fails through instantly
    without occupying the main TTL window.
    """
    if cache is None:
        return await fetch()
    hit = cache.get(key)
    if hit is not None:
        return decode(hit) if decode else hit  # type: ignore[return-value]
    value = await fetch()
    if value is not None:
        effective_ttl = ttl if value else (empty_ttl if cache_empty else None)
        if effective_ttl:
            cache.set(key, value, effective_ttl)
    return value