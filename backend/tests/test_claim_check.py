"""Spec tests: EVIDENCE MODEL + HALLUCINATION REDUCTION (claim verification)."""

from __future__ import annotations

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.models.schemas import Evidence
from app.services.claim_check import (
    CANT_VERIFY,
    already_declares_insufficient,
    has_citation_markers,
    should_verify,
    verify_and_revise,
)


# ------------------------- EVIDENCE MODEL -------------------------


def test_evidence_model_matches_spec():
    ev = Evidence(
        source_id="s1",
        title="A paper",
        url="https://arxiv.org/abs/1",
        snippet="short",
        relevant_text="longer excerpt",
        source_type="academic",
        confidence=0.9,
    )
    assert ev.source_id == "s1"
    assert ev.title and ev.url and ev.snippet and ev.relevant_text
    assert ev.source_type == "academic"
    assert isinstance(ev.confidence, float) and ev.confidence == 0.9


def test_evidence_defaults_are_safe():
    ev = Evidence(source_id="s2")
    assert ev.source_type == "web"
    assert ev.confidence == 0.5 and isinstance(ev.confidence, float)


# ------------------------- claim-verification gating -------------------------


def _evidence(url="https://x.com/a"):
    return [Evidence(source_id="a", title="t", url=url, snippet="s", relevant_text="long text")]


def test_has_citation_markers():
    assert has_citation_markers("[1] claim")
    assert has_citation_markers("[1,3] and [2-4]")
    assert not has_citation_markers("no markers here")
    assert not has_citation_markers("const a = [1, 2]")  # code-looking text with spaces


def test_cant_verify_exact_sentence():
    assert CANT_VERIFY == "I couldn't verify this from the available sources."


def test_should_verify_matrix():
    # evidenced web answer with NO citations -> suspicious, verify
    assert should_verify("long answer no citations", _evidence(), "WEB")
    assert should_verify("answer", _evidence(), "DEEP_RESEARCH")
    # already-verified / self-aware answers are skipped
    assert not should_verify("answer [1]", _evidence(), "WEB")
    assert not should_verify(f"{CANT_VERIFY} because of X", _evidence(), "WEB")
    # no evidence or non-web intents -> skip
    assert not should_verify("answer", [], "WEB")
    assert not should_verify("answer", _evidence(), "CALCULATION")
    assert not should_verify("answer", _evidence(), "GENERAL_CHAT")


def test_already_declares_insufficient():
    assert already_declares_insufficient("I couldn't verify any of that.")
    assert already_declares_insufficient("I COULDN'T VERIFY this.")


# ------------------------- verify_and_revise -------------------------


class _FakeClaimModel(BaseChatModel):
    """Returns canned claim-verification JSON."""

    model: str = "claim-fake"

    @property
    def _llm_type(self) -> str:
        return "claim-fake"

    def __init__(self, *, response: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._response = response

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self._response))])


def test_verify_and_revise_returns_revision(monkeypatch):
    from app.services import claim_check

    monkeypatch.setattr(
        claim_check,
        "get_fast_llm",
        lambda **kw: _FakeClaimModel(response='{"answer": "supported claims only"}'),
    )
    out = verify_and_revise("unsupported claims", _evidence())
    assert out == "supported claims only"


def test_verify_and_revise_keeps_original_on_failure(monkeypatch):
    from app.services import claim_check

    # JSON parse fails -> chain raises -> original kept
    monkeypatch.setattr(
        claim_check, "get_fast_llm", lambda **kw: _FakeClaimModel(response="sorry, no json here")
    )
    answer = "important answer"
    assert verify_and_revise(answer, _evidence()) == answer


def test_verify_and_revise_guards_ballooned_revision(monkeypatch):
    from app.services import claim_check

    response = '{"answer": "' + ("X" * 9000) + '"}'
    monkeypatch.setattr(claim_check, "get_fast_llm", lambda **kw: _FakeClaimModel(response=response))
    answer = "short"
    assert verify_and_revise(answer, _evidence()) == answer


def test_verify_and_revise_empty_evidence_is_noop():
    assert verify_and_revise("answer", []) == "answer"


# ------------------------- wiring into synthesize -------------------------


@pytest.mark.asyncio
async def test_synthesize_wires_claim_verification(monkeypatch):
    from app.agents.nodes.synthesize import synthesize_answer
    from app.core.config import settings

    calls = {"n": 0}

    def fake_verify(answer, evidence):
        calls["n"] += 1
        return "revised verified answer"

    monkeypatch.setattr("app.agents.nodes.synthesize.verify_and_revise", fake_verify)
    monkeypatch.setattr(settings, "enable_claim_verification", True)

    result = await synthesize_answer({
        "query": "tell me about X",
        "intent": "WEB",
        "evidence": _evidence(),
        "history": [],
        "mode": "auto",
    })
    assert calls["n"] == 1
    assert "revised verified answer" in result["answer"]


@pytest.mark.asyncio
async def test_synthesize_skips_verification_when_disabled(monkeypatch):
    from app.agents.nodes.synthesize import synthesize_answer
    from app.core.config import settings

    calls = {"n": 0}

    def fake_verify(*args, **kwargs):
        calls["n"] += 1
        return "revised"

    monkeypatch.setattr("app.agents.nodes.synthesize.verify_and_revise", fake_verify)
    monkeypatch.setattr(settings, "enable_claim_verification", False)

    await synthesize_answer({
        "query": "tell me about X",
        "intent": "WEB",
        "evidence": _evidence(),
        "history": [],
        "mode": "auto",
    })
    assert calls["n"] == 0