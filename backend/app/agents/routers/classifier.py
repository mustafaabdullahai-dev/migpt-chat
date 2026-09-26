"""Lightweight query intent classifier — LLM-backed with a deterministic heuristic fallback."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.core.config import settings
from app.services.llm import get_fast_llm

logger = get_logger("router.classifier")

_CALC_WORDS = (
    "calculate", "computation", "math", "arithmetic", "compute",
    "how much is", "what is the sum", "what is the total", "percent", "%",
    "percentage", "divided by", "multiplied by", "plus", "minus", "times",
    "area of", "volume of", "circumference", "perimeter", "interest on",
    "square root", "factorial", "discount on", "increase by", "decrease by",
    "amortiz", "mortgage", "monthly payment", "emi", "schedule of",
)
_CALC_SYMBOLS = ("+", "-", "*", "/", "**", "%", "sqrt", "log", "^")
_CONVERT_WORDS = ("convert", "conversion", "in feet", "in pounds", "in celsius", "in fahrenheit", "to kg", "to m", "to miles", "in kilometers", "in meters", "in inches", "in gallons", "in liters", "in hours", "in days")
_COMPARE_WORDS = ("compare", "vs", "vs.", "versus", "difference between", "comparison")
_DECISION_WORDS = ("should i", "which should", "should we", "what should", "is it better to", "worth it")
_ACADEMIC_WORDS = ("paper", "arxiv", "research paper", "latent ", "architecture paper", "conference", "journal")
# Strong technical terminology: research-agent should prioritize arXiv papers here.
_TECH_WORDS = (
    "transformer", "attention mechanism", "language model", "llm", "llms",
    "machine learning", "deep learning", "neural network", "neural networks",
    "reinforcement learning", "diffusion model", "diffusion models",
    "rag", "retrieval-augmented", "vector database", "vector databases",
    "embeddings", "embedding model", "fine-tun", "multi-agent", "agentic",
    "autonomous agents", "nlp", "computer vision", "graph neural network",
    "convolutional", "state-of-the-art", "survey of", "literature review",
    "sota", "large language model", "alignment", "prompt engineering",
    "semantic search", "chunk", "knowledge graph", "fine-tuning",
)
_TECH_EXCLUDE = (
    "best ", "which ", "latest", " vs", "versus", "compare", "how to ", "price",
    "recommend", "should i", "worth it", "news",
)
_DOC_WORDS = ("my pdf", "uploaded", "this document", "this file", "my document", "the attached", "this paper", "summarize this file")
_DATA_WORDS = (
    "analyze this csv", "analyze the csv", "analyze this excel", "analyze the data",
    "data analysis", "analyze the file", "the data in this file", "analyze this data",
    "calculate stats", "statistics for", "correlation in", "plot the data", "make a chart",
    "trends in this data", "summarize the rows", "average from this", "does this csv",
)
_SUMMARY_WORDS = ("summarize", "summary", "summarise", "tl;dr", "key points", "briefly summar")
_CURRENT_WORDS = ("latest", "latest version", "current version", "as of ", "in 202", "in 2026", "this year", "recent", "newest", "what's new", "news")
# Explicit date/time questions: route to the server-clock tool so the answer never
# relies on the LLM's training data for the current moment.
_TIME_WORDS = (
    "what time is it", "what's the time", "whats the time", "current time in ", "time in ",
    "what time is it in", "what's today's date", "whats today's date", "today's date",
    "what is today's date", "what's the date", "whats the date", "what date is it",
    "current date", "what day is it", "what day is today", "what is the current date",
    "what's today", "what time is it right now", "what day", "tell me the time",
    "tell me the date", "what is the date today",
)
_TIME_EXCLUDE = ("how much time", "how long", "time it takes", "what time is the", "timeout", "time zone conversions")
_DEEP_WORDS = ("deep research", "exhaustive", "comprehensive research", "do a deep dive", "full research", "research the latest", "research current", "research developments", "thoroughly research", "research in depth")
_RESEARCH_WORDS = ("research", "investigate", "find out", "what is the best", "best practice", "how do i choose")
_STRONG_CALC_WORDS = (
    "amortiz", "mortgage", "monthly payment", "emi", "schedule of",
    "factorial", "square root", "divided by", "percent of", "what is the sum",
    "interest on", "how much is",
)
# Multi-tool: an arithmetic/computation request combined with comparison/decision/
# research framing — the system should run independent tools (calc + comparison)
# in parallel, not just one node.
_MULTI_FRAMING = ("compare", "vs", "versus", "comparison", "difference between",
                  "which should", "should i", "research", "investigate", "cost",
                  "price of", "which is better")
_CODE_WORDS = ("write code", "function", "script", "snippet", "implement", "refactor", "fix bug", "error in", "python code", "react component", "debug")
# Stable-knowledge background questions: definitions, history, general intro.
# Wikipedia is perfect here — it is NOT treated as the highest-authority source
# (the answer labels it secondary and cites primary sources when available).
_BACKGROUND_WORDS = (
    "define ", "definition of", "what is ", "what are ", "who was ", "who is ",
    "history of", "meaning of", "overview of", "introduction to", "background on",
    "what does ", "what did ", "in simple terms", "explain",
)
# Questions that LOOK definitional but are really current/recommendation research.
_BACKGROUND_EXCLUDE = (
    "best ", "latest", "version", " vs", "vs ", "compare", "difference between",
    "recommend", "how do i", "how to ", "in 202", "news", "price", "which is better",
    "worth it", "your ", "are you", "can you", "weather", "today", "tonight",
    "time", "now", "my name", "i am", "score", "match", "election",
)


class QueryIntent(BaseModel):
    intent: str = Field(description="One of: GENERAL_CHAT, WEB_SEARCH, DEEP_RESEARCH, CALCULATION, DOCUMENT_QA, CODE, COMPARISON, DECISION_ANALYSIS, SUMMARIZATION, CURRENT_INFORMATION, ACADEMIC_RESEARCH, BACKGROUND_KNOWLEDGE, DATA_ANALYSIS, MULTI_TOOL, DATETIME")
    complexity: str = Field(description="low | medium | high")
    needs_web: bool = Field(description="Whether web search is required to answer well")
    tools: list[str] = Field(default_factory=list, description="tools to use: calculator, web_search, fetch_webpage, document_search, wikipedia, arxiv, unit_converter, decision_analysis, comparison")


_SYSTEM = (
    "You classify user messages for an agentic AI assistant. Return structured JSON. "
    "Rules:\n"
    "- Arithmetic/math/unit conversion -> CALCULATION (never web).\n"
    "- Computation combined with comparison/decision/research framing "
    "(e.g. 'Calculate A and compare with B', 'research cost of X vs Y, compute monthly fee') "
    "-> MULTI_TOOL (needs web unless it is pure arithmetic+compare with no external topic).\n"
    "- Ask what time/date it is (or time/date in a place) -> DATETIME (no web; use "
    "get_current_time/get_current_date with the timezone — never guess from training data).\n"
    "- 'Compare X vs Y' -> COMPARISON (needs web unless asked from files).\n"
    "- 'Should I choose X or Y' -> DECISION_ANALYSIS (needs web for evidence).\n"
    "- Versions/news/time-sensitive -> CURRENT_INFORMATION (needs web).\n"
    "- Papers/arxiv -> ACADEMIC_RESEARCH (needs web+arxiv).\n"
    "- Technical queries about AI/ML/NLP/LLMs/RAG/agentic AI/CS (surveys, "
    "architectures, methods, state-of-the-art) -> ACADEMIC_RESEARCH; prioritize "
    "arXiv papers before general web results.\n"
    "- Upbeat about a document/uploaded file -> DOCUMENT_QA (no web unless asked).\n"
    "- 'Summarize X' -> SUMMARIZATION.\n"
    "- 'Analyze this CSV/data/file' (with an uploaded file) -> DATA_ANALYSIS "
    "(document/python skills, no web unless asked).\n"
    "- Requesting code -> CODE (no web unless version-specific).\n"
    "- Deep research attempts -> DEEP_RESEARCH.\n"
    "- Definitions, 'what is X', history, meaning, overview/intro of a stable topic "
    "-> BACKGROUND_KNOWLEDGE (needs web via wikipedia for encyclopedic background; "
    "Wikipedia is secondary, primary sources preferred for verification).\n"
    "- Trivial chit-chat/greetings ('hi', 'thanks', 'who are you', 'what can you do') "
    "-> GENERAL_CHAT (no web).\n"
    "- Almost ANY other general question ('tell me about X', 'what's happening with Y', "
    "'how does Z work now') benefits from current web data -> WEB_SEARCH (needs web).\n"
    "- Everything ambiguous/time-sensitive -> WEB_SEARCH.\n"
    "- Bias toward needs_web=true for any topical question: answers must reflect "
    "current, running events across the internet, not only the LLM's training data.\n"
    "Return the single best intent."
)


def classify(query: str, has_documents: bool = False, force_mode: str = "") -> QueryIntent:
    # Tier-1 typo awareness: fix confidently-loop-tweaked domain keywords first so a
    # stray letter ("langchian", "transfomer") doesn't silently drop the query into
    # the wrong intent bucket. The user-facing query is left unchanged; only the
    # string used for *routing* is de-typo'd.
    from app.services.typos import correct_lexicon_typos

    routed_q, _ = correct_lexicon_typos(query)
    query_l = routed_q.lower()

    # Offline, deterministic intent detection runs FIRST and skips the LLM call
    # entirely (fast first-token). The LLM is only consulted for ambiguous queries.
    fast = _fast_classify(query_l, has_documents, force_mode)
    if fast is not None:
        return fast
    # ---- LLM path (when a real provider is configured) ----
    if not settings.is_demo and not (settings.llm_provider == "groq" and not settings.groq_api_key):
        try:
            llm = get_fast_llm(temperature=0.0, max_tokens=300)
            structured = llm.with_structured_output(QueryIntent)
            result: QueryIntent = structured.invoke(
                [
                    ("system", _SYSTEM),
                    ("human", f"Has uploaded documents: {has_documents}\nUser message: {query[:2000]}"),
                ]
            )
            if isinstance(result, dict):
                result = QueryIntent(**result)
            return result
        except Exception as exc:
            logger.warning("LLM classifier failed; using heuristic", extra={"event": "router.llm_fail", "error": str(exc)})

    return _heuristic(query_l, has_documents, force_mode)


def _fast_classify(q: str, has_documents: bool, force_mode: str) -> QueryIntent | None:
    """Deterministic intent detection (no LLM call). Returns None when ambiguous,
    letting the caller consult the LLM / heuristic fallback."""
    q = q.lower()
    # Mode overrides
    if force_mode == "no_web":
        if _is_calc(q):
            return _make("CALCULATION", "low", False, ["calculator"])
        return _make("GENERAL_CHAT", "low", False, [])
    if _is_calcish(q) and _any(q, _MULTI_FRAMING):
        return _make(
            "MULTI_TOOL",
            "high",
            _multi_needs_web(q),
            ["calculator", "comparison", "web_search", "fetch_webpage"],
        )
    # An exact arithmetic expression always beats the background/wiki frame
    if _is_calc(q):
        return _make("CALCULATION", "medium", False, ["calculator"])
    # ---- deterministic background signal (definitions/history/intro) ----
    if _is_background(q):
        return _make("BACKGROUND_KNOWLEDGE", "low", True, ["wikipedia", "web_search", "fetch_webpage"])
    if _any(q, _CALC_SYMBOLS) and _is_calc(q):
        return _make("CALCULATION", "medium", False, ["calculator"])
    if _has_numberish(q) and (_any(q, _CALC_WORDS) or _is_calc(q)):
        return _make("CALCULATION", "low", False, ["calculator"])
    if _any(q, _STRONG_CALC_WORDS) or (("calculate" in q or "compute" in q) and "Variable A" in q):
        return _make("CALCULATION", "medium", False, ["calculator"])
    if _has_numberish(q) and _any(q, _CONVERT_WORDS):
        return _make("CALCULATION", "low", False, ["unit_converter"])
    if _any(q, _TIME_WORDS) and not _any(q, _TIME_EXCLUDE):
        return _make("DATETIME", "low", False, ["get_current_time", "get_current_date"])
    if _any(q, _COMPARE_WORDS):
        return _make("COMPARISON", "medium", not has_documents, ["web_search", "fetch_webpage", "comparison"])
    if _any(q, _DECISION_WORDS):
        return _make("DECISION_ANALYSIS", "high", True, ["web_search", "fetch_webpage", "decision_analysis"])
    # A query that clearly points at an uploaded file ("this paper/document/file")
    # must favor the attachment over generic academic/paper wording.
    _doc_referenced = has_documents and _references_doc(q)
    if (not _doc_referenced) and (_any(q, _ACADEMIC_WORDS) or (_any(q, _TECH_WORDS) and not _any(q, _TECH_EXCLUDE))):
        return _make("ACADEMIC_RESEARCH", "high", True, ["arxiv", "web_search", "fetch_webpage"])
    if _any(q, _SUMMARY_WORDS):
        return _make("SUMMARIZATION", "medium", False, ["document_search", "fetch_webpage", "summarizer"])
    if has_documents and _any(q, _DATA_WORDS):
        return _make("DATA_ANALYSIS", "high", False, ["document_search", "python_analysis"])
    if has_documents and (_any(q, _DOC_WORDS) or _doc_referenced):
        return _make("DOCUMENT_QA", "medium", False, ["document_search"])
    if _any(q, _DEEP_WORDS) or force_mode == "deep":
        return _make("DEEP_RESEARCH", "high", True, ["web_search", "fetch_webpage", "wikipedia", "arxiv"])
    if _any(q, _CURRENT_WORDS):
        return _make("CURRENT_INFORMATION", "medium", True, ["web_search", "fetch_webpage"])
    if _any(q, _BACKGROUND_WORDS) and not _any(q, _BACKGROUND_EXCLUDE):
        return _make("BACKGROUND_KNOWLEDGE", "low", True, ["wikipedia", "web_search", "fetch_webpage"])
    if _any(q, _CODE_WORDS):
        return _make("CODE", "low", False, [])
    if _any(q, _RESEARCH_WORDS) or force_mode == "web":
        return _make("WEB_SEARCH", "medium", True, ["web_search", "wikipedia"])
    # Trivial chit-chat (greetings / identity) → immediate direct answer, no LLM.
    if _is_trivial_smalltalk(q):
        return _make("GENERAL_CHAT", "low", False, [])
    return None


_SMALLTALK_TOKENS = (
    "hi", "hello", "hey", "yo ", "howdy", "hiya", "thanks", "thank you", "ty ",
    "bye", "goodbye", "good morning", "good afternoon", "good evening",
    "good night", "ok", "okay", "nice", "cool", "what are you", "who are you",
    "what can you do", "help me", "help",
)


def _is_trivial_smalltalk(q: str) -> bool:
    """Conservative: only clearly-trivial direct chatter (never topical questions)."""
    if len(q) > 60 or re.search(r"\d", q) or "http" in q:
        return False
    return any(re.match(rf"^\s*{re.escape(tok)}[\s!?.,]*", q) for tok in _SMALLTALK_TOKENS)


def _heuristic(q: str, has_documents: bool, force_mode: str) -> QueryIntent:
    fast = _fast_classify(q, has_documents, force_mode)
    if fast is not None:
        return fast
    # Any topical/ambiguous question defaults to a live web research loop so the
    # answer reflects current, running information across the internet.
    return _make("WEB_SEARCH", "medium", True, ["web_search", "fetch_webpage"])


def _make(intent: str, complexity: str, needs_web: bool, tools: list[str]) -> QueryIntent:
    return QueryIntent(intent=intent, complexity=complexity, needs_web=needs_web, tools=tools)


def _any(q: str, words: tuple[str, ...]) -> bool:
    return any(w in q for w in words)


def _is_background(q: str) -> bool:
    """Deterministic signal for definitions/history/intro so these never miss the
    Wikipedia path (the LLM sometimes falls back to plain chat for them)."""
    if len(q.split()) < 3:
        return False
    if not _any(q, _BACKGROUND_WORDS):
        return False
    return not _any(q, _BACKGROUND_EXCLUDE)


def _is_calc(q: str) -> bool:
    return bool(re.search(r"\d[\d ,.]*(\s*[\+\-\*/\^%]|\s(?:of|by|times|divided)|[-+*/^%]\s*\d)", q)) or "% of" in q


def _is_calcish(q: str) -> bool:
    """Computation signal for MULTI_TOOL: stricter than plain _is_calc so a bare
    arithmetic query stays CALCULATION while a compute+compare combo goes multi-tool."""
    return (
        _is_calc(q)
        or "%" in q
        or "calculate" in q
        or "compute" in q
        or any(w in q for w in _STRONG_CALC_WORDS)
    )


def _multi_needs_web(q: str) -> bool:
    """Arithmetic+compare combos of pure numbers compute offline; anything named
    (product options) or research/decision-framed needs live evidence."""
    if _any(q, (_RESEARCH_WORDS + _DEEP_WORDS + _DECISION_WORDS)):
        return True
    from app.tools.comparison import parse_options

    items = parse_options(q)
    if any(re.search(r"[a-zA-Z]", i) for i in items):
        return True  # named options are products/topics -> need evidence
    return not (re.search(r"\d", q) and ("%" in q or _is_calc(q)))


def _has_numberish(q: str) -> bool:
    return bool(re.search(r"\d", q))


def _references_doc(q: str) -> bool:
    return any(w in q for w in ("summarize this", "give me the methodology", "methodology used in this paper",
                                "analyze this", "key findings", "this document", "the file"))