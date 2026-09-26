"""Speculative (guess-ahead) web search.

The research loop's first search always runs on the raw user query (planning adds
refinements, but the raw query itself is searched). Starting that search while
intent-classification and planning run removes a full round-trip from first-token
latency: when the search node later asks for the same query it hits the warm
cache instead of the network.

This is intentionally a *warm-cache* trick: results for the guessed query are
written into the shared cache under the exact key the search tool computes, so
the research loop reuses them with zero duplicate work. Race-safe: searches run
concurrently with classification; if the warmer finishes after the search node,
the search node simply performs its own search (no correctness impact).
"""

from __future__ import annotations

from app.agents.routers.classifier import _fast_classify
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("speculative")


def is_web_plausible(query: str, mode: str) -> bool:
    """Cheap deterministic gate (no LLM): would this query plausibly hit the web
    research loop? If not, skip guessing to save quota.

    Delegates to the same heuristic the classifier uses: warm whenever the query
    is ambiguous (classifier would default to WEB_SEARCH) or deterministically
    wants web evidence.
    """
    if not settings.enable_web_search:
        return False
    if not settings.enable_cache:
        return False  # no shared cache to warm — skip to avoid burning quota
    if mode == "no_web":
        return False
    q = (query or "").strip()
    if len(q) < 3:
        return False
    fast = _fast_classify(q.lower(), False, mode)
    if fast is None:
        return True  # ambiguous -> heuristic defaults to live web research
    return bool(fast.needs_web)


async def warm_search(query: str, mode: str, limit: int | None = None) -> None:
    """Fire the raw-query search without awaiting it; results land in the shared
    search cache (same key the tool computes), so the research loop's first query
    is an instant cache hit instead of a fresh network round-trip. No SSE is
    emitted — this is a silent pre-warm, not a recorded tool run."""
    if not is_web_plausible(query or "", mode):
        return
    from app.tools.web_search import web_search_tool

    try:
        await web_search_tool(query=query or "", limit=limit)
    except Exception as exc:
        logger.debug("speculative search failed", extra={"event": "spec_search.error", "error": str(exc)})