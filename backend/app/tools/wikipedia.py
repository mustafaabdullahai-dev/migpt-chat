"""Wikipedia search/retrieval tool (free public API).

Purpose-built for GENERAL BACKGROUND, HISTORICAL INFORMATION, DEFINITIONS and
INTRODUCTORY RESEARCH.

Wikipedia is an ENCYCLOPEDIC / SECONDARY source: use it for overviews and to
orient the answer, never as the highest-authority verification. Consumers
(see the background_research graph node and citations.py) mark every Wikipedia
result as secondary so the final answer distinguishes it from primary sources.

Two modes:
  - "search"  -> ranked article matches (title, url, extract snippet)
  - "page"    -> the best-match article's intro + key sections + categories +
                 last-updated timestamp, for deeper background retrieval.
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.models.schemas import SearchResult

logger = get_logger("tools.wikipedia")

API = "https://en.wikipedia.org/w/api.php"
UA = "AgenticSearchEngine/1.0 (educational; contact=none)"
TIMEOUT = 15.0

# Wikipedia is always a secondary / encyclopedic reference.
IS_SECONDARY = True
SOURCE_HOST = "en.wikipedia.org"


class WikipediaArgs(BaseModel):
    query: str = Field(..., description="Topic, title, or definitional question to look up.")
    mode: str = Field(
        default="search",
        description="'search' returns matching articles (background context); "
                    "'page' fetches the full intro + sections of the best-match article.",
    )
    limit: int = Field(default=5, ge=1, le=10)


def article_url(title: str) -> str:
    return f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"


def _clean_snippet(text: str | None, limit: int = 500) -> str:
    return " ".join((text or "").split())[:limit]


def _parse_search(data: dict, limit: int) -> list[SearchResult]:
    """Pure parser for the list=search/extracts API response (unit-testable)."""
    results: list[SearchResult] = []
    for p in data.get("query", {}).get("search", [])[:limit]:
        title = p.get("title", "")
        if not title:
            continue
        results.append(
            SearchResult(
                title=title,
                url=article_url(title),
                snippet=_clean_snippet(p.get("extract")),
                source=SOURCE_HOST,
            )
        )
    return results


def _parse_page(page: dict) -> dict:
    """Pure parser for a single prop=info|extracts|sections|categories page (unit-testable)."""
    title = page.get("title", "")
    intro = _clean_snippet(page.get("extract"), limit=6000)
    sections = [s.get("title", "") for s in page.get("sections", []) if s.get("title")]
    sections = [s for s in sections if s.strip()][:20]
    categories = [
        c.get("title", "").removeprefix("Category:")
        for c in page.get("categories", [])
        if c.get("title")
    ][:8]
    info = page.get("info", {})
    last_edited = info.get("touched", "")
    return {
        "title": title,
        "url": article_url(title),
        "intro": intro,
        "sections": sections,
        "categories": [c for c in categories if c.strip()],
        "last_edited": last_edited,
        "source": SOURCE_HOST,
        "is_secondary": IS_SECONDARY,
    }


class WikipediaTool:
    name = "wikipedia"
    is_async = True
    timeout = TIMEOUT

    async def __call__(self, query: str, mode: str = "search", limit: int = 5) -> object:
        """Search Wikipedia or retrieve a full page. Handles the standard API errors."""
        try:
            if mode == "page":
                return await asyncio.wait_for(self._page(query), timeout=self.timeout)
            return await asyncio.wait_for(self._search(query, limit), timeout=self.timeout)
        except Exception as exc:
            logger.warning("wikipedia failed", extra={"event": "tool.wiki.error", "error": str(exc)})
            return [] if mode != "page" else None

    async def _request(self, params: dict) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": UA},
                                     follow_redirects=True) as client:
            resp = await client.get(API, params=params)
            resp.raise_for_status()
            return resp.json()

    async def _search(self, query: str, limit: int) -> list[SearchResult]:
        data = await self._request(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
                "prop": "extracts",
                "exintro": "1",
                "explaintext": "1",
                "format": "json",
                "formatversion": "2",
            }
        )
        return _parse_search(data, limit)

    async def _best_title(self, query: str) -> str:
        results = await self._search(query, 1)
        return results[0].title if results else ""

    async def _page(self, query: str) -> dict | None:
        title = await self._best_title(query)
        if not title:
            return None
        data = await self._request(
            {
                "action": "query",
                "titles": title,
                "prop": "info|extracts|sections|categories",
                "inprop": "",
                "exintro": "1",
                "explaintext": "1",
                "redirects": "1",
                "cllimit": "8",
                "format": "json",
                "formatversion": "2",
            }
        )
        pages = data.get("query", {}).get("pages", [])
        return _parse_page(pages[0]) if pages else None


wikipedia_tool = WikipediaTool()