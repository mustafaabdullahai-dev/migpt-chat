"""Tool registry — modular, name-addressable tools with schemas, timeouts and error handling."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from app.core.logging import get_logger

logger = get_logger("tools")


@dataclass
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., Any]
    args_schema: type[BaseModel] | None = None
    is_async: bool = False
    timeout: float = 30.0
    enabled: bool = True
    category: str = "utility"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        return [t.name for t in self._tools.values() if t.enabled]

    def specs(self) -> list[ToolSpec]:
        return [t for t in self._tools.values() if t.enabled]

    def filter(self, names: list[str]) -> list[ToolSpec]:
        return [t for n in names if (t := self._tools.get(n)) and t.enabled]


async def run_spec(spec: ToolSpec, kwargs: dict) -> tuple[Any, float]:
    """Invokes a ToolSpec handler with latency tracking; lets exceptions propagate for the caller to handle."""
    start = time.monotonic()
    try:
        if spec.is_async:
            value = await asyncio.wait_for(spec.handler(**kwargs), timeout=spec.timeout)
        else:
            value = await asyncio.to_thread(spec.handler, **kwargs)
        return value, (time.monotonic() - start) * 1000
    except asyncio.TimeoutError:
        logger.warning("tool timeout", extra={"event": "tool.timeout", "tool": spec.name})
        raise
    except Exception as exc:
        logger.warning("tool failed", extra={"event": "tool.error", "tool": spec.name, "error": str(exc)})
        raise


registry = ToolRegistry()