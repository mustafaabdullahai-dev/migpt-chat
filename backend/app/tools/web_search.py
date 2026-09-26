"""Web search tool — wraps the configured SearchProvider with rewriting, retries and caching."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.models.schemas import SearchResult
from app.services.cache import cache_key, cached_call, get_cache
from app.services.search import get_search_provider_chain

logger = get_logger("tools.search")


class WebSearchArgs(BaseModel):
    query: str = Field("", description="Search query for the web.")
    queries: list[str] = Field(
        default_factory=list,
        description="Alternative to `query`: run several searches and merge the deduplicated results.",
    )
    limit: int | None = Field(None, description="Max results per query.")
    exclude_domains: list[str] = Field(
        default_factory=list,
        description="Domains to drop from results (e.g. competitor/spam sites).")


def _revive_search_results(raw: object) -> object:
    """Revive a cached search payload — already a list[SearchResult] in-memory,
    plain dicts when it came from Redis."""
    if not isinstance(raw, list):
        return raw
    out: list[SearchResult] = []
    for item in raw:
        if isinstance(item, SearchResult):
            out.append(item)
        elif isinstance(item, dict):
            out.append(SearchResult(**item))
        else:
            return raw  # unexpected shape: hand back whatever was stored
    return out


class WebSearchTool:
    name = "web_search"
    is_async = True
    timeout = 20.0

    _LEADING_FILLER = [
        "please can you", "please", "can you", "could you", "can u", "could u",
        "would you please", "would you", "what is the best", "what is the",
        "what is", "what are the", "what are", "whats the", "whats", "what's the",
        "what's", "tell me about", "tell me about the", "tell me", "explain what",
        "explain", "i want to know about", "i want to know", "i'd like to know",
        "i would like to know", "do you know", "do u know", "find me", "find out",
        "find", "search for", "look up", "give me", "show me", "i need to know",
        "i need", "more about", "research",
    ]

    async def __call__(
        self,
        query: str = "",
        queries: list[str] | None = None,
        limit: int | None = None,
        exclude_domains: list[str] | None = None,
    ) -> list[SearchResult]:
        """Search one or several (rewritten) queries; results are merged and deduplicated.

        Providers are tried in preference order (configured primary first, then the
        free DuckDuckGo fallback) so a keyless/rate-limited provider never leaves the
        user without results.
        """
        queries = list(queries or [])
        if query:
            queries.append(query)
        if not queries:
            return []
        limit = limit or settings.max_search_results
        exclude = set(d.strip().lower().lstrip("www.") for d in (exclude_domains or []) if d.strip())

        providers = get_search_provider_chain()
        cache = get_cache()
        merged: list[SearchResult] = []
        for q in queries:
            rewritten = self._rewrite(q)
            for p in providers:
                key = cache_key("search", p.name, rewritten or q, limit)
                is_fallback = p.name != providers[0].name
                # cache the *provider-specific* result: each provider cache entry is
                # independent, so fallback results aren't poisoned by a primary miss.
                # Primary empties cache briefly (fast fail-through to the fallback);
                # last-resort empties are never cached so they're retried fresh.
                per_provider = await cached_call(
                    cache,
                    key,
                    lambda prov=p, rw=rewritten or q: self._search_with_escalation(prov.search, rw, limit),
                    ttl=600,
                    empty_ttl=60,
                    cache_empty=not is_fallback,
                    decode=_revive_search_results,
                )
                if isinstance(per_provider, list) and per_provider:
                    merged.extend(per_provider)
                    break
        return self._merge(merged, limit, exclude)

    @staticmethod
    def _base_host(url: str) -> str:
        from urllib.parse import urlparse

        host = (urlparse(url).netloc or "").lower()
        return host[4:] if host.startswith("www.") else host

    @classmethod
    def _merge(cls, results: list[SearchResult], limit: int, exclude: set[str]) -> list[SearchResult]:
        seen: set[str] = set()
        out: list[SearchResult] = []
        for r in results:
            if not r.url:
                continue
            host = cls._base_host(r.url)
            if not host or host in exclude:
                continue
            for dom in exclude:
                if dom and host.endswith("." + dom):
                    break
            else:
                norm = r.url.rstrip("/")
                if norm in seen:
                    continue
                seen.add(norm)
                out.append(r)
                if len(out) >= limit:
                    break
        return out

    @staticmethod
    def _rewrite(query: str) -> str:
        """Strip conversational filler so engines get deterministic keyword queries."""
        import re

        q = " ".join(query.split()).strip()
        lower = q.lower()
        for phrase in WebSearchTool._LEADING_FILLER:
            prefix = phrase + " "
            if lower.startswith(prefix) and len(q) > len(phrase) + 1:
                q = q[len(phrase) + 1 :]
                break
        q = q.strip().strip("?.")
        # Tier-1 typo awareness: fix confidently-misspelled domain keywords before
        # the query hits the engine (kept in the same place so cache keys stay
        # deterministic on the corrected form).
        from app.services.typos import correct_lexicon_typos

        corrected, _ = correct_lexicon_typos(q)
        return corrected.strip() or q

    @staticmethod
    def _deescalate(q: str, attempt: int) -> str:
        """Broader fallback queries when a specific one returns nothing."""
        import re

        q = q.strip()
        if attempt <= 1 or not q:
            return q
        if attempt == 2:
            if '"' in q:
                return re.sub(r'"[^"]*"', "", q).strip()
            return " ".join(q.split()[:-1]).strip()
        return " ".join(q.split()[:4]).strip()

    async def _search_with_escalation(self, fn, query: str, limit: int) -> list[SearchResult]:
        for attempt in range(1, 4):
            q = self._deescalate(query, attempt)
            if not q or (attempt > 1 and q == self._deescalate(query, attempt - 1)):
                continue
            try:
                results = await asyncio.wait_for(fn(q, limit), timeout=self.timeout)
            except asyncio.TimeoutError as exc:
                logger.warning("search timeout", extra={"event": "tool.search.timeout", "query": q, "error": str(exc)})
                results = []
            except Exception as exc:
                logger.warning("search error", extra={"event": "tool.search.error", "query": q, "error": str(exc)})
                results = []
            if results:
                return results
            if attempt < 3:
                await asyncio.sleep(0.4 * attempt)
        return []


web_search_tool = WebSearchTool()