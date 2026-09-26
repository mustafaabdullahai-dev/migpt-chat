"""Graph node: select the tools relevant to the current task (deterministic).

Runs right after classify_query, before routing. It resolves intent/classifier hints
into the minimal enabled tool subset and stores it as state["selected_tools"], so the
agent is only ever given the tools relevant to the task at hand.
"""

from __future__ import annotations

from app.agents.state import AgentState
from app.tools.registry import relevant_tools


def select_tools(state: AgentState) -> dict:
    tools = relevant_tools(
        intent=state.get("intent") or "",
        requires_tools=state.get("requires_tools") or [],
        mode=str(state.get("mode") or "auto"),
    )
    return {"selected_tools": tools}