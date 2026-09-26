"""Decision-analysis tool.

Builds a structured decision framework:
    Decision · Options · Criteria · Constraints · Evidence · Trade-offs ·
    Risks · Unknowns · Recommendation rationale (conditional, never a blind pick).

The final answer generation reasons over this framework plus gathered web evidence.
It NEVER declares a single winner — instead it maps each explicit user priority to
the option that performs best under it ("If your priority is X → Option A…").
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

_DEFAULT_CRITERIA = ["cost", "performance", "ecosystem", "learning curve", "production readiness"]

_INSTRUCTION = (
    "NEVER declare a single winner. For EACH explicit user priority/criterion, explain "
    "which option is stronger and why. Only give a unified recommendation when the user "
    "states explicit weighted criteria. Keep constraints, risks and unknowns honest — "
    "if the evidence is thin, say so instead of guessing."
)


class DecisionArgs(BaseModel):
    question: str = Field(..., description="The decision question, e.g. 'Should I use PostgreSQL or MongoDB?'")
    options: list[str] = Field(default_factory=list, description="Candidate options. If empty, the agent infers common options.")
    criteria: list[str] = Field(default_factory=list, description="Stated decision criteria/constraints from the user.")


def build_decision_framework(question: str, options: list[str], criteria: list[str]) -> dict:
    criteria = criteria or _DEFAULT_CRITERIA
    return {
        "type": "decision_analysis",
        "decision": question,
        "options": options,
        "criteria": criteria,
        "constraints": [],           # filled from the query + evidence by the answer step
        "evidence": [],              # gathered sources hang here once research completes
        "trade_offs": {},            # {option_label: [per-option trade-offs]}
        "risks": [],                 # per-option risks
        "unknowns": [],              # what the evidence still cannot tell us
        "conditional_recommendations": [
            f"If \"{c}\" is your priority, identify the option that is strongest on it and explain why."
            for c in criteria
        ],
        "recommendation_rationale": (
            "No blind pick. If a user priority is explicit (e.g. cost >> everything), "
            "recommend the option that wins on that priority and show the trade-off accepted."
        ),
        "instruction": _INSTRUCTION,
    }


class DecisionTool:
    name = "decision_analysis"
    is_async = False

    def __call__(
        self,
        question: str,
        options: list[str] | None = None,
        criteria: list[str] | None = None,
    ) -> str:
        fw = build_decision_framework(question, options or [], criteria or [])
        return json.dumps(fw, indent=2)


decision_tool = DecisionTool()