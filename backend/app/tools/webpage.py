"""Webpage reader — safe fetching, readable-text extraction, size limits, SSRF guard."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import validate_url
from app.models.schemas import PageContent
from app.services.cache import cache_key, cached_call, get_cache
from app.services.http import get_http_client

logger = get_logger("tools.webpage")

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_REDIRECT_CODES = (301, 302, 303, 307, 308)
_MAX_REDIRECTS = 5
_MAX_RAW_BYTES = 4 * 1024 * 1024  # abort the download itself beyond this
_MAX_LINKS = 12
_NAV_HOSTS = {
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "pinterest.com", "reddit.com", "youtube.com", "tiktok.com",
}


class FetchWebpageArgs(BaseModel):
    url: str = Field(..., description="Absolute http(s) URL of the webpage to read.")


def _revive_page(raw: object) -> object:
    """Revive a cached PageContent — a model in-memory, a dict from Redis."""
    if isinstance(raw, PageContent):
        return raw
    if isinstance(raw, dict):
        try:
            return PageContent(**raw)
        except Exception:
            return raw
    return raw


class WebpageTool:
    name = "fetch_webpage"
    is_async = True
    timeout = 25.0

    def __init__(self, use_trafilatura: bool = True) -> None:
        self._use_trafilatura = use_trafilatura

    @staticmethod
    def _trafilatura_lock() -> asyncio.Lock:
        # lxml (used by trafilatura) can segfault when parses run on several
        # threads at once, so serialize the C-lib extraction.
        lock_holder = getattr(WebpageTool, "_lock", None)
        if lock_holder is None:
            lock_holder = asyncio.Lock()
            WebpageTool._lock = lock_holder
        return lock_holder

    async def __call__(self, url: str) -> PageContent:
        url = validate_url(url)
        cache = get_cache()
        key = cache_key("page", url)
        return await cached_call(
            cache,
            key,
            lambda: self._fetch(url),
            ttl=300,
            decode=_revive_page,
        )

    async def _fetch(self, url: str) -> PageContent:
        last: Exception | None = None
        for attempt in range(2):
            try:
                html = await asyncio.wait_for(self._download(url), timeout=self.timeout)
                break
            except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as exc:
                last = exc
                if attempt == 0:
                    await asyncio.sleep(0.6)
        else:  # both attempts failed
            raise RuntimeError(f"Failed to fetch {url}: {last}")

        text: str = ""
        title, headings, meta, links = "", [], {}, []
        try:
            if self._use_trafilatura:
                import trafilatura  # heavy import: defer

                async with self._trafilatura_lock():
                    extracted = await asyncio.to_thread(trafilatura.extract, html)
                if extracted:
                    text = extracted
                else:
                    text = _bs4_text(html)
            else:
                text = _bs4_text(html)

            soup = BeautifulSoup(html, "html.parser")
            if soup.title and soup.title.string:
                title = soup.title.string.strip()
            headings = [
                h.get_text(strip=True)
                for tag in _HEADING_TAGS
                for h in soup.find_all(tag)
            ][:20]
            meta = {
                (m.get("name") or m.get("property") or ""): m.get("content", "")
                for m in soup.find_all("meta")
            }
            meta = {k: v for k, v in meta.items() if k in ("description", "author", "og:title")}
            links = _extract_links(soup, url)
        except Exception as exc:
            logger.warning("page extraction failed", extra={"event": "tool.page.extract_error", "url": url, "error": str(exc)})

        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        truncated = False
        if len(text) > settings.max_page_size:
            text = text[: settings.max_page_size]
            truncated = True
        if len(text) < 100 and not headings:
            raise RuntimeError(f"No readable content found at {url}")
        return PageContent(
            url=url, title=title, text=text, headings=headings, metadata=meta,
            links=links, truncated=truncated,
        )

    async def _download(self, url: str) -> str:
        """Manual redirect loop that re-validates every hop (blocks redirects to private hosts)."""
        client = get_http_client()
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            if current != url:
                current = validate_url(current)  # SSRF guard on the new hop
            async with client.stream("GET", current, follow_redirects=False) as resp:
                if resp.status_code in _REDIRECT_CODES:
                    location = resp.headers.get("location")
                    if not location:
                        raise RuntimeError(f"Redirect without location from {current}")
                    current = urljoin(current, location)
                    continue
                resp.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                capped = False
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > _MAX_RAW_BYTES:
                        capped = True
                        break
                    chunks.append(chunk)
                if capped:
                    logger.warning(
                        "page download size capped",
                        extra={"event": "tool.page.size_capped", "url": current},
                    )
                return b"".join(chunks).decode("utf-8", "replace")
        raise RuntimeError(f"Too many redirects from {url}")


def _extract_links(soup: BeautifulSoup, base: str) -> list[dict]:
    """Preserve the most useful outbound links found in the page body (not nav/social)."""
    from urllib.parse import urlparse

    scope = soup.find("article") or soup.find("main") or soup.body
    if scope is None:
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for a in scope.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base, href)
        host = (urlparse(url).netloc or "").lower()
        host = host[4:] if host.startswith("www.") else host
        if not host or host in _NAV_HOSTS:
            continue
        label = " ".join(a.get_text(" ", strip=True).split())
        if not label or len(label) < 4 or len(label) > 80:
            continue
        norm = url.rstrip("/")
        if norm in seen:
            continue
        seen.add(norm)
        out.append({"title": label[:80], "url": url})
        if len(out) >= _MAX_LINKS:
            break
    return out


def _bs4_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe", "svg"]):
        tag.decompose()
    return "\n".join(block.strip() for block in soup.get_text("\n").splitlines() if block.strip())


webpage_tool = WebpageTool()