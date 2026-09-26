"""Shared node helpers: safe tool invocation + tool-run bookkeeping."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.models.schemas import ToolRun
from app.services.stream import emit
from app.tools.base import registry, run_spec


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ToolCallResult:
    """Outcome of a single tool invocation in a plan (name + status + payload)."""

    name: str
    status: str
    payload: Any


async def run_tool_plan(steps: list[dict | list[dict]]) -> list[ToolCallResult]:
    """Executes a tool plan sequentially; a step given as a *list* of independent
    tool dicts runs its members in parallel via asyncio.gather.

    steps:  [ {"name": "calculator", "expression": ...},      # sequential step
              [ {"name":"web_search", ...}, {"name":"arxiv", ...} ],  # parallel group ]
    """
    out: list[ToolCallResult] = []
    for step in steps:
        if isinstance(step, list) and step:
            done = await asyncio.gather(*[_call_one(s) for s in step])
            out.extend(done)
        elif isinstance(step, dict) and step.get("name"):
            out.append(await _call_one(step))
    return out


async def _call_one(step: dict) -> ToolCallResult:
    name = str(step["name"])
    kwargs = {k: v for k, v in step.items() if k != "name"}
    payload, record = await run_tool(name, **kwargs)
    return ToolCallResult(name=name, status=record.status, payload=payload)


async def run_tool(
    name: str,
    **kwargs: Any,
) -> tuple[Any, ToolRun]:
    """Invokes a named tool, emitting SSE progress and returning (result, record)."""
    spec = registry.get(name)
    if spec is None or not spec.enabled:
        return None, ToolRun(name=name, status="error", detail={"error": "tool disabled"})

    started = _now_iso()
    emit("tool_start", {"name": name, "detail": _tool_verbose(name, kwargs)})
    try:
        result, latency = await run_spec(spec, kwargs)
        record = ToolRun(
            name=name,
            status="success",
            started_at=None,
            finished_at=None,
            latency_ms=int(latency),
            detail=kwargs,
        )
        emit("tool_result", {"name": name, "status": "success", "latency_ms": int(latency)})
        return result, record
    except Exception as exc:
        emit("tool_result", {"name": name, "status": "error", "error": str(exc)})
        record = ToolRun(
            name=name,
            status="error",
            started_at=None,
            finished_at=None,
            latency_ms=0,
            detail={"error": str(exc)},
        )
        return {"error": str(exc), "tool": name}, record


def _tool_verbose(name: str, kwargs: dict) -> str:
    if name in ("web_search", "wikipedia", "arxiv"):
        return kwargs.get("query", "")
    if name in ("get_current_time", "get_current_date"):
        return kwargs.get("timezone", "UTC")
    if name == "fetch_webpage":
        return kwargs.get("url", "")
    if name == "calculator":
        return kwargs.get("expression", "")
    if name == "unit_converter":
        return f"{kwargs.get('value')} {kwargs.get('from_unit')} → {kwargs.get('to_unit')}"
    if name == "comparison":
        return " vs ".join(kwargs.get("items") or []) or kwargs.get("topic", "")
    if name == "summarizer":
        return kwargs.get("kind", "generic")
    return ""