"""deep_research — a higher-level research workflow tool.

Runs the full research pipeline (query decomposition → multiple searches →
dedupe → rank → fetch → extract evidence → gap-driven follow-up → synthesize)
with a hard bound on iterations and sources. Returns structured, citable output.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import validate_url
from app.models.schemas import Evidence, PageContent, SearchResult
from app.services.citations import dedupe_evidence, infer_quality, rank_evidence
from app.services.llm import get_llm
from app.tools.web_search import WebSearchTool
from app.tools.webpage import webpage_tool

logger = get_logger("tools.deep_research")

FETCH_TOP = 4
MAX_TEXT_PER_SOURCE = 1800
_PLAN_PROMPT = (
    "Rewrite the user's topic into {max_q} distinct search-engine queries, each targeting a "
    "different angle (definitions, official docs, comparisons, recent developments, examples). "
    "If prior_results is non-empty, focus on gaps the previous search missed. "
    "Return ONLY a JSON array of strings, 3-10 words each. No explanations.\n\n"
    "Topic: {query}\nPrior results found: {prior}"
)


class DeepResearchArgs(BaseModel):
    query: str = Field(..., description="Topic to research in depth.")
    max_iterations: int = Field(settings.research_max_iterations, ge=1, le=4)
    max_sources: int | None = Field(None, ge=1, le=12, description="Stop once enough quality sources are collected.")


class DeepResearchTool:
    name = "deep_research"
    is_async = True
    timeout = 120.0

    def __init__(self, search=None, fetch=None, plan=None, answer_llm=None) -> None:
        self._search = search or WebSearchTool()
        self._fetch = fetch or webpage_tool
        self._plan = plan
        self._answer_llm = answer_llm or get_llm

    async def __call__(
        self,
        query: str,
        max_iterations: int = settings.research_max_iterations,
        max_sources: int | None = None,
    ) -> dict:
        budget = max_sources or settings.research_max_sources
        fetch_tool = self._fetch
        llm = self._answer_llm

        evidence: list[Evidence] = []
        decomposed: list[str] = []
        seen_queries: set[str] = set()
        errors: list[str] = []
        runs = 0

        for iteration in range(max(1, max_iterations)):
            runs += 1
            # 1) query decomposition (gap-aware on later passes)
            queries = await self._plan_queries(query, [e.title for e in evidence], llm)
            queries = [q for q in queries if q and q.lower() not in seen_queries]
            if not queries:
                queries = [query]
            decomposed.extend(queries)
            seen_queries.update(q.lower() for q in queries)

            # 2) search multiple queries, 3) dedupe/merge
            merged: list[SearchResult] = []
            for q in queries:
                try:
                    merged.extend(await self._search(q, limit=settings.max_search_results))
                except Exception as exc:
                    errors.append(str(exc))
            merged = WebSearchTool._merge(merged, settings.max_search_results, set())

            # 4) fetch important pages, 5) extract evidence
            for candidate in merged[:FETCH_TOP]:
                if any(e.url == candidate.url for e in evidence):
                    continue
                page, weak = None, None
                if fetch_tool is not None:
                    try:
                        page = await fetch_tool(candidate.url)
                    except Exception as exc:
                        errors.append(str(exc))
                if page is None:
                    weak = PageContent(url=candidate.url, title=candidate.title, text=candidate.snippet)
                page = page or weak
                text = page.text or candidate.snippet
                if not text:
                    continue
                evidence.append(
                    Evidence(
                        source_id=page.url,
                        title=page.title or candidate.title,
                        url=page.url,
                        snippet=(candidate.snippet or "")[:400],
                        relevant_text=text[:MAX_TEXT_PER_SOURCE],
                        source_type="webpage",
                        source_quality=infer_quality(page.url, page.title or candidate.title),
                        confidence=0.7,
                    )
                )
            evidence = dedupe_evidence(evidence)
            evidence = rank_evidence(evidence)
            # stop early once enough quality sources exist
            if len(evidence) >= budget:
                break

        # 6) validate citations (drop blocked/broken hosts)
        valid = [e for e in evidence if _valid_source(e.url)]
        answer = await self._synthesize(query, valid, llm) if valid else ""
        sources = [
            {"title": e.title, "url": e.url, "source": e.url.split("/")[2] if e.url[:5] == "http" else "", "quality": e.source_quality}
            for e in valid[:budget]
        ]
        return {
            "query": query,
            "decomposed_queries": decomposed,
            "iterations": runs,
            "sources": sources,
            "evidence": [
                {"title": e.title, "url": e.url, "snippet": e.snippet, "relevant_text": e.relevant_text[:400]}
                for e in valid[:budget]
            ],
            "answer": answer,
            "errors": errors[:5],
        }

    async def _plan_queries(self, query: str, prior: list[str], llm) -> list[str]:
        from inspect import isawaitable

        if self._plan is not None:
            result = self._plan(query, prior)
            if isawaitable(result):
                result = await result
            return result
        from langchain_core.output_parsers import JsonOutputParser

        max_q = 3 if (len(prior) or 0) == 0 else 2
        try:
            raw = await llm().ainvoke(_PLAN_PROMPT.format(query=query[:1500], max_q=max_q, prior=", ".join(prior[:4])))
            parsed = JsonOutputParser().parse(raw.content if hasattr(raw, "content") else "")
            if isinstance(parsed, list):
                return [str(x)[:120] for x in parsed if isinstance(x, str)][:max_q]
        except Exception as exc:
            logger.warning("deep_research plan fallback", extra={"error": str(exc)})
        return [query, f"{query} comparison best practices"] if prior else [query]

    async def _synthesize(self, query: str, evidence: list[Evidence], llm) -> str:
        src_lines = "\n".join(f"[{i}] {e.title}\n    {e.url}" for i, e in enumerate(evidence, 1))
        snippets = "\n\n".join(f"[{i}] ({e.title})\n{e.relevant_text[:700]}" for i, e in enumerate(evidence, 1))
        prompt = (
            "Synthesize a concise, structured answer to the user's question using ONLY these "
            "sources. If the sources cannot support something, say so instead of guessing. "
            "Use markdown headings/lists where useful. Do NOT add inline citation numbers "
            "like [1], [2] or a Sources section — the app renders source links separately.\n\n"
            f"Question: {query}\n\nSOURCES:\n{src_lines}\n\nCONTENT:\n{snippets}"
        )
        try:
            resp = await llm().ainvoke(prompt)
            return (resp.content if hasattr(resp, "content") else str(resp)).strip()
        except Exception as exc:
            logger.warning("deep_research synthesis failed", extra={"error": str(exc)})
            return ""


def _valid_source(url: str) -> bool:
    try:
        validate_url(url, require_resolution=False)
        return True
    except ValueError:
        return False


deep_research_tool = DeepResearchTool()