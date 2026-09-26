"""Tests: typo awareness (Tier-1 deterministic + Tier-2 LLM repair)."""

from __future__ import annotations

from typing import Any

from app.services import typos


# ------------------------- Tier-1: lexicon edit-distance -------------------------


def test_corrects_confident_domain_typos():
    text, fixes = typos.correct_lexicon_typos("langchian vs langgraph")
    assert text == "langchain vs langgraph"
    assert [f.typo for f in fixes] == ["langchian"]


def test_corrects_medium_length_typo():
    text, fixes = typos.correct_lexicon_typos("transfomer architecture")
    assert text == "transformer architecture"
    assert fixes[0].target == "transformer"


def test_leaves_known_word_and_punctuation_untouched():
    text, fixes = typos.correct_lexicon_typos("What is Python? It's a language!")
    assert text == "What is Python? It's a language!"
    assert fixes == []


def test_no_fix_for_unique_name_or_ambiguous_token():
    # a plausible brand coined uniquely shouldn't be rewritten to a keyword
    text, fixes = typos.correct_lexicon_typos("Mycroft application")
    assert fixes == []


def test_fixes_isolated_typo_keeps_rest_of_sentence():
    text, fixes = typos.correct_lexicon_typos("tell me about pytorch and tensorflow")
    assert text == "tell me about pytorch and tensorflow"
    assert fixes == []


def test_python_typo_corrected():
    text, fixes = typos.correct_lexicon_typos("latest pythyon version")
    assert fixes and fixes[0].target == "python"


# ------------------------- Tier-1 through the classifier -------------------------


def test_classifier_routes_typoed_tech_to_academic(monkeypatch):
    """A stray letter in 'transformers' must still land in ACADEMIC_RESEARCH."""
    from types import SimpleNamespace

    from app.agents.routers import classifier as classifier_mod

    monkeypatch.setattr(
        "app.agents.routers.classifier.settings",
        SimpleNamespace(is_demo=True, llm_provider="mock", groq_api_key=""),
    )
    result = classifier_mod.classify("how do transfomers work", force_mode="auto")
    assert result.intent == "ACADEMIC_RESEARCH"
    assert "arxiv" in result.tools


# ------------------------- Tier-1 through the search tool -------------------------


def test_search_rewrite_applies_typo_fix():
    from app.tools.web_search import WebSearchTool

    assert WebSearchTool._rewrite("what is langchian langgraph") == "langchain langgraph"


# ------------------------- Tier-2: LLM repair -------------------------


def _demo_settings(**kw):
    from types import SimpleNamespace
    base = dict(is_demo=True, llm_provider="mock", groq_api_key="")
    base.update(kw)
    return SimpleNamespace(**base)


def test_repair_query_returns_original_in_demo_mode(monkeypatch):
    monkeypatch.setattr("app.services.typos.settings", _demo_settings())
    assert typos.repair_query("what is gpt?") == "what is gpt?"


def test_repair_query_short_circuits_for_trivial_query():
    assert typos.repair_query("hi") == "hi"


def test_repair_query_uses_llm_and_accepts_only_different_text(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.services.typos.settings",
        SimpleNamespace(is_demo=False, llm_provider="groq", groq_api_key="k"),
    )

    class _FakeLLM:
        def invoke(self, messages: Any) -> SimpleNamespace:
            return SimpleNamespace(content='langchian agent')

    captured: dict[str, Any] = {}

    def _get_fast_llm(**kwargs):
        captured.update(kwargs)
        return _FakeLLM()

    monkeypatch.setattr("app.services.typos.get_fast_llm", _get_fast_llm)
    assert typos.repair_query("langchian agent") == "langchian agent"
    assert captured.get("temperature") == 0.0


def test_repair_query_keeps_original_when_nothing_to_fix(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.services.typos.settings",
        SimpleNamespace(is_demo=False, llm_provider="groq", groq_api_key="k"),
    )

    class _FakeLLM:
        def invoke(self, messages: Any) -> SimpleNamespace:
            return SimpleNamespace(content='What is Python?')

    monkeypatch.setattr("app.services.typos.get_fast_llm", lambda **kw: _FakeLLM())
    # same text (ignoring case) -> original returned unchanged
    assert typos.repair_query("what is python?") == "what is python?"


def test_repair_query_swallows_llm_errors(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.services.typos.settings",
        SimpleNamespace(is_demo=False, llm_provider="groq", groq_api_key="k"),
    )

    def _boom(**kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.services.typos.get_fast_llm", _boom)
    assert typos.repair_query("langchian agent") == "langchian agent"


# ------------------------- gating -------------------------


def test_needs_repair_gates_on_empty_and_plausible_text():
    assert typos.needs_repair(results_empty=True, text="what is a langchian agent") is True
    assert typos.needs_repair(results_empty=True, text="hi") is False
    assert typos.needs_repair(results_empty=False, text="what is a langchian agent") is False