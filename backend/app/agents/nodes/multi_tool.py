"""Graph node: multi-tool orchestration (item 26).

Runs independent computation + reasoning steps as one parallel group and hands
the combined results to the synthesize node:
  - calculator / unit_converter for the numeric part of the query
  - comparison scaffold when named options are being compared
Web evidence (gathered earlier in the research loop) is attached to the scaffold.
"""

from __future__ import annotations

import json
import re

from app.agents.nodes.calculate import plan_calculation
from app.agents.nodes.helpers import run_tool_plan
from app.agents.state import AgentState
from app.models.schemas import ToolRun
from app.services.stream import emit_status
from app.tools.comparison import parse_options

_PCT_OF = re.compile(r"([\d.,]+)\s*%\s*of\s*([\d.,]+)")


def _pct_calc_steps(query: str) -> list[dict]:
    """Independent 'X% of Y' calculator steps discovered directly in the text."""
    steps: list[dict] = []
    seen: set[str] = set()
    for m in _PCT_OF.finditer(query):
        pct, base = m.group(1), m.group(2)
        expr = f"({pct.replace(',', '')}/100)*{base.replace(',', '')}"
        if expr in seen:
            continue
        seen.add(expr)
        steps.append({"name": "calculator", "expression": expr})
    return steps


def _named_options(items: list[str]) -> list[str]:
    """Options that are text labels (not pure numbers) — comparison needs names."""
    named = [i for i in items if re.search(r"[a-zA-Z]", i)]
    return named[:4] if len(named) >= 2 else []


def _scaffold_output(payload, evidence: list) -> dict:
    payload = payload or ""
    try:
        scaffold = json.loads(payload)
    except (TypeError, ValueError):
        return {"reasoning_scaffold": payload}
    if not isinstance(scaffold, dict):
        return {"reasoning_scaffold": payload}
    scaffold["evidence"] = [
        {"title": e.title, "url": e.url, "snippet": (e.snippet or e.relevant_text or "")[:300]}
        for e in evidence[:8]
    ]
    return {"reasoning_scaffold": json.dumps(scaffold, indent=2)}


async def multi_tool_answer(state: AgentState) -> dict:
    query = state.get("query", "").strip()
    if not query:
        return {}

    emit_status("Running combined tools…")

    calc_steps = _pct_calc_steps(query)
    plan = plan_calculation(query) if not calc_steps else None
    if plan and plan.get("kind") in ("math", "formula", "units"):
        if plan["kind"] == "units":
            calc_steps.append(
                {
                    "name": "unit_converter",
                    "value": plan["value"],
                    "from_unit": plan["from_unit"],
                    "to_unit": plan["to_unit"],
                }
            )
        else:
            calc_steps.append({"name": "calculator", "expression": plan["expression"]})

    items = parse_options(query)
    scaffold_step = None
    named = _named_options(items)
    if not state.get("reasoning_scaffold") and named:
        scaffold_step = {"name": "comparison", "items": named, "criteria": []}

    parallel = calc_steps + ([scaffold_step] if scaffold_step else [])
    if not parallel:
        return {}

    results = await run_tool_plan([parallel])
    records: list[ToolRun] = []
    calc_payloads: list = []
    comparison_payloads: list = []
    for r in results:
        if r.status != "success":
            continue
        records.append(ToolRun(name=r.name, status="success", detail={}))
        if r.name in ("calculator", "unit_converter"):
            calc_payloads.append(r.payload)
        elif r.name == "comparison":
            comparison_payloads.append(r.payload)

    out: dict = {"tool_runs": records}

    if plan and plan.get("kind") == "units" and calc_payloads:
        from app.models.schemas import CalculationResult

        first = calc_payloads[0]
        if isinstance(first, CalculationResult):
            first.expression = plan.get("label") or first.expression
    if calc_payloads:
        out["calculations"] = calc_payloads
        out["calculation"] = calc_payloads[0]

    if comparison_payloads and not state.get("reasoning_scaffold"):
        out.update(_scaffold_output(comparison_payloads[0], state.get("evidence", [])))

    return out