"""Claim verification for web answers — HALLUCINATION REDUCTION spec.

Pipeline: Question -> Evidence -> Claim extraction -> Claim verification -> Final response.

For web answers with evidence, verify_and_revise() asks the model to keep only
evidence-supported claims, replacing anything unsupported with the spec's exact
"sorry, couldn't verify" sentence. It is only invoked when the deterministic
symptom of hallucination is present (sources exist but the answer cites none of
them); the common good path costs zero extra LLM calls.
"""

from __future__ import annotations

import re

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.core.logging import get_logger
from app.services.llm import call_with_retry, get_fast_llm

logger = get_logger("claim_check")

# The exact sentence the spec requires when evidence is insufficient.
CANT_VERIFY = "I couldn't verify this from the available sources."

_MARKER_RE = re.compile(r"\[\d+(?:[-,]\d+)*\]")

# Web-type intents whose important claims should be evidence-backed.
_VERIFY_INTENTS = {
    "WEB",
    "WEB_SEARCH",
    "DEEP_RESEARCH",
    "ACADEMIC_RESEARCH",
    "COMPARISON",
    "DECISION_ANALYSIS",
    "BACKGROUND_KNOWLEDGE",
    "MULTI_TOOL",
}

_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You verify an assistant's answer against numbered web sources.\n\n"
            "Sources:\n{sources}\n\n"
            "The assistant answer is between <ANSWER> tags. Rules:\n"
            '- Claims that match or are supported by the sources: keep them VERBATIM.\n'
            '- Claims that CONTRADICT the sources: replace just that claim with the '
            'sentence: "{phrase}"\n'
            '- A claim not present in the sources may be the model\'s own general '
            "knowledge — this is ALLOWED and it must NOT be replaced unless it "
            "contradicts a source or contains fabricated specifics (numbers, dates, "
            "quotes, URLs) that the sources disconfirm.\n"
            "- Never add new claims, never keep fabricated numbers/dates/quotes/URLs.\n"
            "- Keep the overall structure.\n"
            'Return ONLY JSON: {{"answer": "<the revised markdown>"}}',
        ),
        ("human", "<ANSWER>\n{answer}\n</ANSWER>"),
    ]
)


def has_citation_markers(text: str) -> bool:
    return bool(_MARKER_RE.search(text or ""))


def already_declares_insufficient(text: str) -> bool:
    return "couldn't verify" in (text or "").lower()


def should_verify(answer: str, evidence: list, intent: str) -> bool:
    """Deterministic gate: verify only plausible-unverified web answers.

    Fires when a web-type answer has evidence to lean on but cites none of it and
    does not already admit it couldn't verify — the signal that claims may be
    unsupported/invented.
    """
    if not answer or not evidence:
        return False
    if (intent or "").upper() not in _VERIFY_INTENTS:
        return False
    if has_citation_markers(answer):
        return False
    if already_declares_insufficient(answer):
        return False
    return True


def verify_and_revise(answer: str, evidence: list, phrase: str = CANT_VERIFY) -> str:
    """Returns the evidence-backed revision, or the original answer if anything is off.

    Never raises; a failed verification pass silently keeps the user's answer (the
    citation validators in _finalize still run as a second line of defense).
    """
    sources_block = "\n\n".join(
        f"[{i + 1}] {e.title}\nURL: {e.url}\n{(e.relevant_text or e.snippet)[:600]}"
        for i, e in enumerate(evidence[:6])
        if getattr(e, "url", None)
    )
    if not sources_block:
        return answer

    llm = get_fast_llm(temperature=0.0, max_tokens=600)
    chain = _PROMPT | llm | JsonOutputParser()
    try:
        raw = call_with_retry(
            lambda: chain.invoke({"sources": sources_block, "answer": answer, "phrase": phrase}),
            attempts=2,
            desc="claim verification",
        )
    except Exception as exc:
        logger.warning("claim verification skipped", extra={"event": "claim.verify_failed", "error": str(exc)})
        return answer

    revised = raw.get("answer") if isinstance(raw, dict) else raw
    if not isinstance(revised, str) or not revised.strip():
        return answer
    # sanity guard: a revision that wildly balloons or collapses is suspicious
    if len(revised) > max(len(answer) * 2, 4000):
        return answer
    return revised.strip()