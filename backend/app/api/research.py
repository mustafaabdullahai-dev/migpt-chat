"""Direct search + research endpoints (bypassing the chat graph)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agents.graph import get_graph
from app.api.deps import resolve_user
from app.db.base import get_db
from app.models.schemas import ResearchOut, ResearchRequest, SearchRequest, SearchResponse
from app.services.observability import begin, end
from app.services.search import get_search_provider

router = APIRouter(tags=["search"])


@router.post("/api/search", response_model=SearchResponse)
async def search_web(req: SearchRequest):
    results = await get_search_provider().search(req.query, limit=req.limit)
    return SearchResponse(query=req.query, results=results)


@router.post("/api/research", response_model=ResearchOut)
async def research(req: ResearchRequest, user_id: str = Depends(resolve_user), db: Session = Depends(get_db)):
    graph = get_graph()
    # a fresh internal uuid per research call keeps chat-scoped memory isolated
    research_conv_id = str(uuid.uuid4())
    span = begin(
        request_type="research",
        conversation_id=research_conv_id,
        user_id=user_id,
        query=req.query,
        mode="deep" if req.depth in ("deep", "standard") else "web",
    )
    state: dict | None = None
    try:
        state = await graph.ainvoke(
            {
                "query": req.query,
                "conversation_id": research_conv_id,
                "user_id": user_id,
                "mode": "deep" if req.depth in ("deep", "standard") else "web",
                "documents": [],
                "history": [],
                "intent": "DEEP_RESEARCH",
                "tool_runs": [],
                "iterations": 0,
            }
        )
    except Exception as exc:
        span.status = "error"
        span.errors.append(str(exc))
        raise
    finally:
        if state:
            span.intent = "DEEP_RESEARCH"
            span.depth = state.get("depth") or span.depth
            span.sources_count = len(state.get("evidence") or [])
            for run in state.get("tool_runs", []) or []:
                span.record_tool(
                    str(getattr(run, "name", "") or ""),
                    str(getattr(run, "status", "success") or "success"),
                    int(getattr(run, "latency_ms", 0) or 0),
                )
        end()
    return ResearchOut(
        query=req.query,
        answer=state.get("answer", ""),
        searches=[s.url or s.title for s in state.get("search_results", [])],
        sources=[
            {"index": i + 1, "url": s.url, "title": s.title, "source": "web", "quality": s.source_quality}
            for i, s in enumerate(state.get("evidence", []))
        ],
        tool_runs=state.get("tool_runs", []),
    )