"""Graph node: plan web searches (query decomposition)."""

from __future__ import annotations

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.agents.depth import depth_budget
from app.agents.state import AgentState
from app.core.config import settings
from app.services.llm import get_fast_llm
from app.services.stream import emit_status

_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Rewrite the user's topic into {max_q} distinct search-engine queries, each targeting a "
            "different angle (definitions, official docs, comparisons, recent developments, examples). "
            "If prior_results is non-empty, focus on gaps the previous search missed. "
            "Return ONLY a JSON array of strings, 3-10 words each. No explanations.",
        ),
        ("human", "Topic: {query}\nPrior results found: {prior}"),
    ]
)


def _query_count(state: AgentState) -> int:
    budget = depth_budget(state)
    if budget["depth"] == "deep":
        return budget["first_queries"] if state.get("iterations", 0) == 0 else budget["followup_queries"]
    if budget["depth"] == "quick":
        return budget["first_queries"]
    # standard: modest first pass (within 3-6 searches budget), narrower gap fill
    if state.get("iterations", 0) > 0 and state.get("evidence"):
        return budget["followup_queries"]
    return budget["first_queries"]


def _needs_llm_plan(state: AgentState) -> bool:
    """An extra LLM round-trip before searching only pays off for deep/academic or
    high-complexity research; simple research uses fast deterministic queries."""
    if settings.is_demo or (settings.llm_provider == "groq" and not settings.groq_api_key):
        return False
    intent = state.get("intent", "")
    if state.get("mode") == "deep" or intent in ("DEEP_RESEARCH", "ACADEMIC_RESEARCH"):
        return True
    if state.get("complexity", "low") == "high":
        return True
    if state.get("iterations", 0) > 0 and state.get("evidence"):
        return True  # gap-filling benefits from a targeted follow-up plan
    return False


async def plan_research(state: AgentState) -> dict:
    query = state.get("query", "")
    max_q = _query_count(state)
    emit_status("Planning the research…")

    queries: list[str] = []
    if _needs_llm_plan(state):
        prior_titles = ", ".join(e.title for e in state.get("evidence", [])[:4])
        try:
            llm = get_fast_llm(temperature=0.3, max_tokens=200)
            raw = await llm.ainvoke(
                _PROMPT.format_messages(query=query[:1500], max_q=max_q, prior=prior_titles[:300])
            )
            parsed = JsonOutputParser().parse(raw.content if hasattr(raw, "content") else "")
            if isinstance(parsed, list):
                queries = [str(x)[:200] for x in parsed if isinstance(x, str)][:max_q]
        except Exception:
            queries = []

    if not queries:
        if state.get("iterations", 0) > 0:
            queries = [f"{query} comparison", f"{query} limitations best practices"]
        elif not ("2026" in query.lower() or "2025" in query.lower()):
            queries = [query, f"{query} 2026"]
        else:
            queries = [query]

    queries = list({q for q in queries if q})[:max_q]
    return {"search_queries": queries, "plan": [f"search: {q}" for q in queries]}