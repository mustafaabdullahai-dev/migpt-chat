"""Search provider abstraction.

    SearchProvider
        ├── DuckDuckGoSearchProvider  (free, default)
        ├── TavilySearchProvider      (optional)
        ├── BraveSearchProvider       (optional)
        ├── SerperSearchProvider      (optional)
        └── MockSearchProvider        (demo mode)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from html.parser import HTMLParser
from urllib.parse import urlparse

from app.core.config import settings
from app.core.logging import get_logger
from app.models.schemas import SearchResult
from app.services.http import get_http_client

logger = get_logger("search")


def _dedupe(results: list[SearchResult], limit: int) -> list[SearchResult]:
    seen: set[str] = set()
    out: list[SearchResult] = []
    for r in results:
        if not r.url:
            continue
        norm = r.url.rstrip("/")
        if norm in seen:
            continue
        seen.add(norm)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc or ""
    except Exception:
        return ""


class SearchProvider(ABC):
    name = "base"

    @abstractmethod
    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        ...


class DuckDuckGoSearchProvider(SearchProvider):
    name = "duckduckgo"

    _UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        import asyncio

        # Run all tiers concurrently and return as soon as the FASTEST one yields
        # results (bounded overall). Waiting for every tier adds worst-case latency.
        loop = asyncio.get_running_loop()
        tasks = {
            asyncio.create_task(t, name=label)
            for label, t in (
                ("html", self._scrape("html", query, limit)),
                ("lite", self._scrape("lite", query, limit)),
                ("ddgs", asyncio.to_thread(self._ddgs, query, limit)),
                ("wiki", self._scrape("wiki", query, limit)),
            )
        }
        deadline = loop.time() + 18
        pending = tasks
        while pending:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    tier_results = task.result()
                except Exception:
                    continue
                results = _dedupe(tier_results, limit)
                if results:
                    for p in pending:
                        p.cancel()
                    return results
        for p in pending:
            p.cancel()
        return []

    def _ddgs(self, query: str, limit: int) -> list[SearchResult]:
        from ddgs import DDGS

        try:
            raw = DDGS(timeout=8).text(
                query,
                backend="duckduckgo",  # only this ddgs engine is reliable/undetectable
                region="us-en",
                safesearch="moderate",
                max_results=limit,
            )
        except Exception as exc:
            logger.warning(
                "DDG search failed", extra={"event": "search.error", "query": query, "error": str(exc)}
            )
            return []
        return [r for r in (self._result(i) for i in raw or []) if r]

    async def _scrape(self, mode: str, query: str, limit: int) -> list[SearchResult]:
        if mode == "wiki":
            return await self._wiki(query, limit)
        url = "https://html.duckduckgo.com/html/" if mode == "html" else "https://lite.duckduckgo.com/lite/"
        try:
            client = get_http_client()
            resp = await client.get(url, params={"q": query})
            resp.raise_for_status()
            rows = _parse_ddg_html(resp.text, mode)
        except Exception as exc:
            logger.warning(
                f"DDG {mode} scrape failed",
                extra={"event": "search.error", "query": query, "error": str(exc)},
            )
            return []
        return [
            SearchResult(title=t, url=u, snippet=s, source=_domain(u))
            for t, u, s in rows[:limit]
            if u
        ]

    async def _wiki(self, query: str, limit: int) -> list[SearchResult]:
        try:
            client = get_http_client()
            resp = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "format": "json",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": min(limit, 10),
                    "srprop": "snippet",
                    "origin": "*",
                },
            )
            resp.raise_for_status()
            hits = resp.json().get("query", {}).get("search", []) or []
        except Exception as exc:
            logger.warning(
                "Wikipedia search failed",
                extra={"event": "search.error", "query": query, "error": str(exc)},
            )
            return []
        import re as _re

        strip = _re.compile(r"<[^>]+>")
        results = []
        for h in hits[:limit]:
            url = "https://en.wikipedia.org/wiki/" + (h.get("title", "").replace(" ", "_"))
            snippet = strip.sub("", h.get("snippet", "") or "").strip()
            results.append(
                SearchResult(title=h.get("title", ""), url=url, snippet=snippet, source="en.wikipedia.org")
            )
        return results

    def _result(self, item: dict) -> SearchResult | None:
        url = (item.get("href") or "").strip()
        if not url:
            return None
        return SearchResult(
            title=item.get("title") or "",
            url=url,
            snippet=item.get("body") or "",
            source=_domain(url),
        )


class _DDGHtmlParser(HTMLParser):
    """Extracts (title, url, snippet) triples from DDG HTML/Lite pages."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, str, str]] = []
        self._in_link = False
        self._link_buf: list[str] = []
        self._in_snippet = False
        self._snippet_buf: list[str] = []
        self._link_url = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        classes = dict(attrs).get("class", "")
        if tag == "a" and ("result__a" in classes or "result-link" in classes):
            self._in_link = True
            self._link_class = classes
            self._link_buf = []
            self._link_url = dict(attrs).get("href", "")
            return
        if tag in ("div", "td", "span", "p") and (
            "result__snippet" in classes or "result-snippet" in classes or "result__snippet__ad" in classes
        ):
            self._in_snippet = True
            self._snippet_buf = []
            return

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            title = " ".join("".join(self._link_buf).split())
            self.rows.append((title, self._link_url, ""))
            self._in_link = False
        elif tag in ("div", "td", "span", "p") and self._in_snippet:
            if self.rows and not self.rows[-1][2]:
                title, url, _ = self.rows[-1]
                snip = " ".join("".join(self._snippet_buf).split())
                self.rows[-1] = (title, url, snip)
            self._in_snippet = False

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._link_buf.append(data)
        if self._in_snippet:
            self._snippet_buf.append(data)


def _parse_ddg_html(text: str, mode: str) -> list[tuple[str, str, str]]:
    parser = _DDGHtmlParser()
    parser.feed(text)
    rows = [r for r in parser.rows if r[1]]
    if mode == "lite":
        return rows
    # html.ddg may set result links via /l/?uddg=<urlencoded> redirect
    from urllib.parse import parse_qs, urlparse

    out: list[tuple[str, str, str]] = []
    for title, url, snippet in rows:
        if url.startswith("//"):
            url = "https:" + url
        try:
            qs = parse_qs(urlparse(url).query)
            if "uddg" in qs and qs["uddg"]:
                url = qs["uddg"][0]
        except Exception:
            pass
        out.append((title, url, snippet))
    return out


class TavilySearchProvider(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str, base_url: str = "https://api.tavily.com/search") -> None:
        self._api_key = api_key
        self._base_url = base_url

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        client = get_http_client()
        resp = await client.post(
            self._base_url,
            json={"api_key": self._api_key, "query": query, "max_results": limit},
        )
        resp.raise_for_status()
        data = resp.json()
        results = [
            SearchResult(
                title=item.get("title") or "",
                url=item.get("url") or "",
                snippet=item.get("content") or "",
                source=_domain(item.get("url") or ""),
            )
            for item in data.get("results", [])
        ]
        return _dedupe(results, limit)


class BraveSearchProvider(SearchProvider):
    name = "brave"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        client = get_http_client()
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": limit},
            headers={"X-Subscription-Token": self._api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        results = [
            SearchResult(
                title=item.get("title") or "",
                url=item.get("url") or "",
                snippet=item.get("description") or "",
                source=_domain(item.get("url") or ""),
            )
            for item in data.get("web", {}).get("results", [])
        ]
        return _dedupe(results, limit)


class SerperSearchProvider(SearchProvider):
    name = "serper"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        client = get_http_client()
        resp = await client.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": limit},
            headers={"X-API-KEY": self._api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        results = [
            SearchResult(
                title=item.get("title") or "",
                url=item.get("link") or "",
                snippet=item.get("snippet") or "",
                source=_domain(item.get("link") or ""),
            )
            for item in data.get("organic", [])
        ]
        return _dedupe(results, limit)


class GoogleSearchProvider(SearchProvider):
    """Official Google Custom Search JSON API (needs api_key + a Programmable Search Engine cx)."""

    name = "google"

    def __init__(self, api_key: str, cx: str) -> None:
        self._api_key = api_key
        self._cx = cx

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        import httpx

        num = min(limit, 10)
        try:
            client = get_http_client()
            resp = await client.get(
                "https://customsearch.googleapis.com/customsearch/v1",
                params={
                    "key": self._api_key,
                    "cx": self._cx,
                    "q": query,
                    "num": num,
                    "safe": "active",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning(
                "Google search failed",
                extra={"event": "search.error", "query": query, "error": str(exc)},
            )
            return []
        results = [
            SearchResult(
                title=item.get("title") or "",
                url=item.get("link") or "",
                snippet=item.get("snippet") or "",
                source=_domain(item.get("link") or ""),
            )
            for item in data.get("items", []) or []
        ]
        return _dedupe(results, limit)


class MockSearchProvider(SearchProvider):
    name = "mock"

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        return [
            SearchResult(
                title=f"Demo result for: {query}",
                url="https://example.com/demo",
                snippet="Mock search result. Enable SEARCH_PROVIDER=duckduckgo for real results.",
                source="example.com",
            )
        ]


_search: SearchProvider | None = None


def get_search_provider() -> SearchProvider:
    global _search
    if _search is not None:
        return _search
    if settings.is_demo:
        _search = MockSearchProvider()
        return _search
    name = settings.search_provider.lower()
    if name == "google" and settings.google_search_api_key and settings.google_search_cx:
        _search = GoogleSearchProvider(settings.google_search_api_key, settings.google_search_cx)
    elif name == "tavily" and settings.tavily_api_key:
        _search = TavilySearchProvider(settings.tavily_api_key)
    elif name == "brave" and settings.brave_api_key:
        _search = BraveSearchProvider(settings.brave_api_key)
    elif name == "serper" and settings.serper_api_key:
        _search = SerperSearchProvider(settings.serper_api_key)
    elif name == "duckduckgo" or not settings.enable_web_search:
        _search = DuckDuckGoSearchProvider()
    else:
        _search = DuckDuckGoSearchProvider()
    return _search


def get_search_provider_chain() -> list[SearchProvider]:
    """Primary provider first, then lightweight fallbacks.

    Resilience: if the configured provider is missing keys, rate-limited or
    failing, we keep serving results by falling back to DuckDuckGo (free, no
    key). The chain is ordered by preference; callers try each in turn.
    """
    primary = get_search_provider()
    chain = [primary]
    if primary.name not in ("duckduckgo", "mock"):
        chain.append(DuckDuckGoSearchProvider())
    return chain