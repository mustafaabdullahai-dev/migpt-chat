"""arXiv research-paper search tool via the arXiv API.

The canonical entry point is :func:`search_arxiv`, which returns one dict per
paper exactly matching the documented shape:

    {"title", "authors", "abstract", "published", "url"}

``ArxivTool`` wraps it for the registered tool registry, producing
``SearchResult`` objects (title/url/snippet/source) for the research graph.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.models.schemas import SearchResult

logger = get_logger("tools.arxiv")
API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom"}


class ArxivArgs(BaseModel):
    query: str = Field(..., description="Technical/academic query to search on arXiv.")
    limit: int = Field(5, ge=1, le=50, description="Maximum number of papers to return.")


def clean_query(query: str, limit: int = 400) -> str:
    s = re.sub(r"[^\w\s.-]", " ", query)
    return re.sub(r"\s+", " ", s).strip()[:limit]


def parse_entries(xml_text: str) -> list[dict[str, Any]]:
    """Parse an arXiv Atom feed into spec-shaped paper dicts (pure, testable)."""
    root = ET.fromstring(xml_text)
    entries = []
    for entry in root.findall("a:entry", NS):
        authors = [a.findtext("a:name", default="", namespaces=NS) for a in entry.findall("a:author", NS)]
        published = entry.findtext("a:published", default="", namespaces=NS)
        title = (entry.findtext("a:title", default="", namespaces=NS) or "").strip().replace("\n", " ")
        abstract = (entry.findtext("a:summary", default="", namespaces=NS) or "").strip()
        link = entry.findtext("a:id", default="", namespaces=NS)
        entries.append({
            "title": title,
            "authors": [a for a in authors if a],
            "abstract": abstract,
            "published": published[:10] if published else "",
            "url": link,
        })
    return entries


async def search_arxiv(query: str, limit: int = 5, sort_by: str = "relevance") -> list[dict[str, Any]]:
    """Search arXiv and return papers as {"title","authors","abstract","published","url"} dicts."""
    search = clean_query(query)
    if not search:
        return []
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            API,
            params={"search_query": f"all:{search}", "start": 0, "max_results": limit, "sortBy": sort_by},
        )
        resp.raise_for_status()
    entries = parse_entries(resp.text)
    return entries[:limit]


class ArxivTool:
    name = "arxiv"
    is_async = True
    timeout = 20.0

    async def __call__(self, query: str, limit: int = 5) -> list[SearchResult]:
        try:
            raw = await asyncio.wait_for(search_arxiv(query, limit=limit), timeout=self.timeout)
        except Exception as exc:
            logger.warning("arxiv failed", extra={"event": "tool.arxiv.error", "error": str(exc)})
            return []
        return [self._parse(item) for item in raw]

    @staticmethod
    def _parse(item: dict[str, Any]) -> SearchResult:
        snippet = f"{item['abstract'][:600]} — Published {item['published']} — Authors: {', '.join(item['authors'][:4])}"
        return SearchResult(title=item["title"], url=item["url"], snippet=snippet, source="arxiv.org")


arxiv_tool = ArxivTool()