"""Comparison tool — builds a structured comparison scaffold (rows/columns) filled by evidence.

The scaffold drives the final answer into the canonical table shape:
    Criterion | Option A | Option B | Option C
    Architecture · Performance · Ecosystem · Learning curve · Production use · Community · Best use case
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

_DEFAULT_RULES = [
    "architecture",
    "performance",
    "ecosystem",
    "learning curve",
    "production use",
    "community",
    "best use case",
]

_VS_RE = re.compile(r"\s+(?:vs\.?|versus)\s+", re.IGNORECASE)


def parse_options(query: str) -> list[str]:
    """Extract compared options from 'Compare A vs B vs C' (or commas)."""
    text = query.strip().rstrip("?.!,;:")
    parts = _VS_RE.split(text)
    if len(parts) < 2:
        # fall back to comma-separated enumeration inside the query
        candidate = parts[0] if parts else text
        segments = [s.strip(" \t\u2013—()") for s in candidate.split(",")]
        parts = [s for s in segments if len(s) > 1] or parts
    if len(parts) < 2:
        return []
    # drop lead-in words for the first option ("Compare/LangChain vs Flask" -> LangChain)
    first = _strip_lead_in(parts[0])
    return [first] + [_strip_lead_in(p) for p in parts[1:]][:3]


def _strip_lead_in(option: str) -> str:
    lowered = option.lower()
    for prefix in ("compare", "comparison between", "difference between", "which is better", "consider"):
        if lowered.startswith(prefix):
            return option[len(prefix):].strip().lstrip(":, \t")
    return option


class ComparisonArgs(BaseModel):
    items: list[str] = Field(
        default_factory=list,
        description="Options to compare, e.g. ['LangChain', 'LlamaIndex', 'Haystack']. "
                    "If empty, derive them from the query.",
    )
    criteria: list[str] = Field(default_factory=list, description="Optional custom criteria rows.")


class ComparisonTool:
    name = "comparison"
    is_async = False

    def __call__(self, items: list[str] | None = None, criteria: list[str] | None = None) -> str:
        items = list(items or [])
        rows = list(criteria or _DEFAULT_RULES)
        return json.dumps(
            {
                "type": "comparison",
                "items": items,
                "criteria": rows,
                "instruction": (
                    "Produce a markdown table. Columns are the OPTIONS (one per option, labeled by "
                    "name). Rows are these criteria in this exact order: " + ", ".join(_DEFAULT_RULES) + "."
                    " Fill one evidence-backed cell per criterion per option; mark unverifiable cells "
                    "as 'Not verified'. Add a short recommendation paragraph reflecting the user's "
                    "likely priorities. Every time-sensitive claim must cite a source from the "
                    "NUMBERED SOURCES list; never invent facts or URLs."
                ),
            },
            indent=2,
        )


comparison_tool = ComparisonTool()