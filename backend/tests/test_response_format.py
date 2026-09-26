"""ADAPTIVE RESPONSE LENGTH + RESPONSE FORMAT.

Depth tier (quick/standard/deep) drives verbosity: simple → 2-5 sentences, normal →
moderate explanation, research → detailed structured template. The synthesis prompt
carries a per-request VERBOSITY TARGET directive chosen from the classified depth.
"""

from app.agents.depth import research_depth
from app.agents.nodes.synthesize import _build_messages


def _system(state: dict) -> str:
    msgs = _build_messages(state)
    assert msgs and msgs[0].type == "system"
    return str(msgs[0].content)


def test_simple_questions_resolve_to_quick_depth():
    assert research_depth("BACKGROUND_KNOWLEDGE", "low", "auto") == "quick"
    assert research_depth("WEB_SEARCH", "low", "auto") == "quick"


def test_research_intents_resolve_to_deep():
    assert research_depth("DEEP_RESEARCH", "medium", "auto") == "deep"
    assert research_depth("ACADEMIC_RESEARCH", "low", "auto") == "deep"
    assert research_depth("COMPARISON", "high", "deep") == "deep"


def test_queries_outside_research_resolve_to_standard():
    assert research_depth("GENERAL_CHAT", "low", "auto") == "standard"
    assert research_depth("CALCULATION", "low", "auto") == "standard"


def test_quick_depth_forces_short_answer():
    sys = _system({"query": "What is FastAPI?", "depth": "quick", "intent": "BACKGROUND_KNOWLEDGE"})
    assert "VERBOSITY TARGET: quick" in sys
    assert "2-5 sentences" in sys
    assert "bold key answer" in sys  # short answers still render as proper markdown
    assert "markdown" in sys


def test_standard_depth_forces_moderate_explanation():
    sys = _system({"query": "How does Docker networking work?", "depth": "standard"})
    assert "VERBOSITY TARGET: moderate" in sys
    assert "focused explanation" in sys


def test_deep_depth_forces_structured_template():
    sys = _system({"query": "Compare PostgreSQL and MySQL in depth", "depth": "deep", "intent": "COMPARISON"})
    assert "VERBOSITY TARGET: comprehensive" in sys
    assert "RESPONSE FORMAT" in sys
    assert "## Key Findings" in sys
    assert "## Analysis" in sys


def test_response_format_headings_always_present_for_research():
    sys = _system({"query": "a research question", "depth": "deep"})
    assert "## Answer" in sys
    assert "## Key Findings" in sys
    assert "## Analysis" in sys
    assert "## Comparison" in sys
    # Sources are rendered separately by the app UI, so the model must not write its own.
    assert "Do NOT write a \"Sources\", \"References\" or URL list" in sys


def test_no_depth_state_falls_back_without_crashing():
    sys = _system({"query": "plain query"})
    assert "VERBOSITY TARGET" not in sys  # untouched default system prompt