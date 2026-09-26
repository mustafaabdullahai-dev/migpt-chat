"""Research depth tiers: quick | standard | deep.

Maps the user's mode plus the classified intent/complexity onto a *bounded*
research budget (queries per pass, loop iterations, source cap), so the graph
runs controlled research loops instead of unbounded searching.
"""

from __future__ import annotations

from app.agents.state import AgentState
from app.core.config import settings

RESEARCH_DEPTHS = ("quick", "standard", "deep")

_OFFLINE_INTENTS = {"CALCULATION", "DATETIME", "DOCUMENT_QA", "DATA_ANALYSIS", "CODE"}


def research_depth(intent: str, complexity: str, mode: str) -> str:
    """Choose the depth tier determined by mode + classification.

    quick   -> simple auto-mode web lookups (bounded to a handful of searches)
    standard -> the default research loop (3-6 searches across passes)
    deep    -> deep research mode / deep-intent queries (decomposition + loops)
    """
    if mode == "deep" and intent not in _OFFLINE_INTENTS:
        return "deep"
    if intent in ("DEEP_RESEARCH", "ACADEMIC_RESEARCH"):
        return "deep"
    if (
        mode == "auto"
        and complexity == "low"
        and intent
        in ("WEB_SEARCH", "CURRENT_INFORMATION", "BACKGROUND_KNOWLEDGE",
            "COMPARISON", "DECISION_ANALYSIS", "SUMMARIZATION")
    ):
        return "quick"
    if mode == "no_web":
        return "standard"  # unused (no research loop), keeps a sane default
    return "standard"


def depth_budget(state: AgentState) -> dict:
    """Bounded research budget for the current depth tier (config-driven limits)."""
    depth = state.get("depth") or research_depth(
        state.get("intent", ""),
        state.get("complexity", "medium"),
        state.get("mode", "auto"),
    )
    if depth == "deep":
        return {
            "depth": "deep",
            "max_iterations": settings.research_deep_max_iterations,
            "max_sources": settings.research_deep_max_sources,
            "first_queries": settings.research_deep_first_queries,
            "followup_queries": settings.research_deep_followup_queries,
        }
    if depth == "quick":
        return {
            "depth": "quick",
            "max_iterations": settings.research_quick_max_iterations,
            "max_sources": settings.research_quick_max_sources,
            "first_queries": min(settings.research_quick_max_searches, settings.research_quick_max_sources),
            "followup_queries": 0,
        }
    first = min(3, settings.research_standard_max_searches)
    return {
        "depth": "standard",
        "max_iterations": settings.research_max_iterations,
        "max_sources": settings.research_max_sources,
        "first_queries": first,
        "followup_queries": max(0, settings.research_standard_max_searches - first),
    }