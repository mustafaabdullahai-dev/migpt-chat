"""Assembles the global tool registry from feature toggles."""

from __future__ import annotations

from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import get_logger
from app.tools.base import ToolSpec, registry
from app.tools.calculator import CalculatorArgs, evaluate as _calc
from app.tools.comparison import ComparisonArgs, ComparisonTool
from app.tools.datetime_tool import TimeArgs, current_date, current_time
from app.tools.decision import DecisionArgs, DecisionTool
from app.tools.documents import DocumentSearchArgs, DocumentSearchTool
from app.tools.units import UnitConverterArgs, convert as _convert
from app.tools.summarize import SummarizeArgs, SummarizeTool
from app.tools.web_search import WebSearchArgs, WebSearchTool
from app.tools.webpage import FetchWebpageArgs, WebpageTool
from app.tools.deep_research import DeepResearchArgs, DeepResearchTool
from app.tools.python_analysis import PythonAnalysisArgs, PythonAnalysisTool
from app.tools.wikipedia import WikipediaArgs, WikipediaTool
from app.tools.arxiv import ArxivArgs, ArxivTool

logger = get_logger("tools.registry")


def build_registry() -> None:
    specs: list[ToolSpec] = [
        ToolSpec(
            name="calculator",
            description="Safely evaluates arithmetic, percentages, powers, roots, logarithms and "
                        "trigonometry. Use for ALL arithmetic instead of mental math.",
            handler=lambda **kw: _calc(kw["expression"]),
            args_schema=CalculatorArgs,
            category="calculation",
            enabled=settings.enable_calculator,
        ),
        ToolSpec(
            name="unit_converter",
            description="Converts between units: length, mass, temperature, area, volume, speed, time, data, energy, pressure.",
            handler=lambda **kw: _convert(kw["value"], kw["from_unit"], kw["to_unit"]),
            args_schema=UnitConverterArgs,
            category="calculation",
        ),
        ToolSpec(
            name="web_search",
            description="Searches the web (DuckDuckGo by default) and returns titles, URLs and snippets.",
            handler=WebSearchTool(),
            args_schema=WebSearchArgs,
            is_async=True,
            category="search",
            enabled=settings.enable_web_search,
        ),
        ToolSpec(
            name="python_analysis",
            description="Runs a Python snippet in an isolated sandbox for data/CSV analysis, "
                        "statistics, numerical and scientific computation, and chart generation. "
                        "Call result(x) to return a value; matplotlib figures become PNG charts. "
                        "Purpose-built for analyzing tabular/CSV data and computations.",
            handler=PythonAnalysisTool(),
            args_schema=PythonAnalysisArgs,
            is_async=True,
            category="analysis",
            enabled=settings.enable_python_analysis,
        ),
        ToolSpec(
            name="deep_research",
            description="Runs a bounded, multi-step web research workflow on a topic: decomposes the "
                        "question into queries, searches, re-searches gaps, and returns cited evidence "
                        "plus a synthesized answer. Prefer for complex research topics.",
            handler=DeepResearchTool(),
            args_schema=DeepResearchArgs,
            is_async=True,
            category="research",
            enabled=settings.enable_web_search,
        ),
        ToolSpec(
            name="fetch_webpage",
            description="Fetches a URL and extracts readable text, headings and metadata with SSRF protection.",
            handler=WebpageTool(),
            args_schema=FetchWebpageArgs,
            is_async=True,
            category="search",
            enabled=settings.enable_web_search,
        ),
        ToolSpec(
            name="wikipedia",
            description="Encyclopedic search/retrieval for general background, historical information, "
                        "definitions and introductory research. 'mode: page' returns a full article intro + "
                        "sections. Wikipedia is a SECONDARY source — use for overviews, never as the "
                        "highest-authority verification.",
            handler=WikipediaTool(),
            args_schema=WikipediaArgs,
            is_async=True,
            category="search",
            enabled=settings.enable_wikipedia,
        ),
        ToolSpec(
            name="arxiv",
            description="Searches arXiv for academic papers (AI/ML/CS). Prioritize for technical research.",
            handler=ArxivTool(),
            args_schema=ArxivArgs,
            is_async=True,
            category="academic",
            enabled=settings.enable_arxiv,
        ),
        ToolSpec(
            name="get_current_time",
            description="Returns the current server time in an IANA timezone (e.g. 'Asia/Karachi'). "
                        "Always derive the time from this tool, never from the model's own knowledge.",
            handler=lambda **kw: current_time(kw.get("timezone", "UTC")),
            args_schema=TimeArgs,
            category="utility",
        ),
        ToolSpec(
            name="get_current_date",
            description="Returns the current server date (YYYY-MM-DD) in an IANA timezone. "
                        "Always derive the date from this tool, never from the model's own knowledge.",
            handler=lambda **kw: current_date(kw.get("timezone", "UTC")),
            args_schema=TimeArgs,
            category="utility",
        ),
        ToolSpec(
            name="decision_analysis",
            description="Structures a decision into options, criteria, risks, trade-offs, unknowns.",
            handler=DecisionTool(),
            args_schema=DecisionArgs,
            category="reasoning",
        ),
        ToolSpec(
            name="comparison",
            description="Builds a structured comparison scaffold across options and criteria.",
            handler=ComparisonTool(),
            args_schema=ComparisonArgs,
            category="reasoning",
        ),
        ToolSpec(
            name="document_search",
            description="Retrieves relevant chunks from uploaded documents (RAG). Use for questions about the user's files.",
            handler=DocumentSearchTool(),
            args_schema=DocumentSearchArgs,
            category="rag",
            enabled=settings.enable_document_rag,
        ),
        ToolSpec(
            name="summarizer",
            description="Map-reduce summarization of large content (webpages, PDFs, documents, "
                        "search results, conversations, research collections). Long inputs are "
                        "chunked and summarized hierarchically, never sent whole to the model.",
            handler=SummarizeTool(),
            args_schema=SummarizeArgs,
            is_async=True,
            category="analysis",
        ),
    ]

    for spec in specs:
        registry.register(spec)


build_registry()


# Canonical, spec-shaped registry map (TOOL_REGISTRY) with the free-first tool set.
# Extra tools (get_current_date, comparison, python_analysis, summarizer) are also in
# the ToolRegistry; this map keeps the spec's canonical names (incl. the "time" alias).
def _require(name: str) -> ToolSpec:
    spec = registry.get(name)
    if spec is None:
        raise RuntimeError(f"TOOL_REGISTRY references missing tool: {name}")
    return spec


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "web_search": _require("web_search"),
    "fetch_webpage": _require("fetch_webpage"),
    "calculator": _require("calculator"),
    "wikipedia": _require("wikipedia"),
    "arxiv": _require("arxiv"),
    "deep_research": _require("deep_research"),
    "document_search": _require("document_search"),
    "unit_converter": _require("unit_converter"),
    "time": _require("get_current_time"),  # spec calls it "time"
    "decision_analysis": _require("decision_analysis"),
}

# Intent -> minimum relevant tool set (plus anything the classifier put in requires_tools).
_INTENT_TOOLS: dict[str, set[str]] = {
    "CALCULATION": {"calculator", "unit_converter"},
    "MULTI_TOOL": {"calculator", "unit_converter", "comparison"},
    "COMPARISON": {"decision_analysis", "comparison", "web_search", "fetch_webpage"},
    "DECISION_ANALYSIS": {"decision_analysis", "web_search", "fetch_webpage"},
    "DEEP_RESEARCH": {"deep_research", "web_search", "fetch_webpage", "arxiv"},
    "ACADEMIC_RESEARCH": {"web_search", "fetch_webpage", "arxiv"},
    "WEB_SEARCH": {"web_search", "fetch_webpage"},
    "BACKGROUND_KNOWLEDGE": {"wikipedia", "web_search"},
    "DOCUMENT_QA": {"document_search", "python_analysis"},
    "DATA_ANALYSIS": {"document_search", "python_analysis"},
    "DATETIME": {"time"},
    "SUMMARIZATION": {"fetch_webpage", "summarizer", "document_search"},
    "GENERAL_CHAT": set(),
}


def relevant_tools(
    intent: str,
    requires_tools: list[str] | None = None,
    mode: str = "auto",
) -> list[str]:
    """Deterministically selects the tools relevant to the current task.

    The agent is handed ONLY this subset (plus the classifier's requires_tools), so
    task-irrelevant tools are never presented. `mode` web/deep force search tooling;
    `no_web` strips web search/fetch. Tools that are disabled (feature toggles) drop out.
    """
    names: set[str] = set(requires_tools or [])
    names |= _INTENT_TOOLS.get((intent or "").upper(), set())
    if mode in ("web", "deep"):
        names |= {"web_search", "fetch_webpage"}
    if mode == "no_web":
        names -= {"web_search", "fetch_webpage"}

    selected: set[str] = set()
    for n in names:
        canonical = "get_current_time" if n == "time" else n
        spec = registry.get(canonical)
        if spec is not None and spec.enabled:
            selected.add(spec.name)
    return sorted(selected)