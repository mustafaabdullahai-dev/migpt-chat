"""LangGraph state-machine assembly.

Nodes are separate modules; conditional edges drive routing and the research loop.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.calculate import calculate
from app.agents.nodes.classify import classify_query
from app.agents.nodes.datetime import datetime_answer
from app.agents.nodes.documents import retrieve_documents
from app.agents.nodes.evidence import analyze_evidence, evidence_sufficient, fetch_sources
from app.agents.nodes.multi_tool import multi_tool_answer
from app.agents.nodes.plan import plan_research
from app.agents.nodes.remember import remember
from app.agents.nodes.search import search_web
from app.agents.nodes.select_tools import select_tools
from app.agents.nodes.summarize import summarize_answer
from app.agents.nodes.synthesize import synthesize_answer
from app.agents.nodes.wikipedia import background_research
from app.agents.routers.router import after_retrieve, route_query
from app.agents.state import AgentState
from app.core.logging import get_logger
import app.tools.registry  # noqa: F401  (populates the tool registry)

logger = get_logger("graph")

_graph_singleton = None


def build_graph():
    g = StateGraph(AgentState)

    g.add_node("classify", classify_query)
    g.add_node("select_tools", select_tools)
    g.add_node("calculate", calculate)
    g.add_node("datetime", datetime_answer)
    g.add_node("retrieve_documents", retrieve_documents)
    g.add_node("plan", plan_research)
    g.add_node("search", search_web)
    g.add_node("fetch_sources", fetch_sources)
    g.add_node("analyze_evidence", analyze_evidence)
    g.add_node("background_research", background_research)
    g.add_node("summarize", summarize_answer)
    g.add_node("multi_tool", multi_tool_answer)
    g.add_node("synthesize", synthesize_answer)
    g.add_node("remember", remember)

    g.add_edge(START, "classify")
    g.add_edge("classify", "select_tools")
    g.add_conditional_edges("select_tools", route_query, {
        "calculate": "calculate",
        "datetime": "datetime",
        "retrieve_documents": "retrieve_documents",
        "summarize": "summarize",
        "background_research": "background_research",
        "multi_tool": "multi_tool",
        "plan": "plan",
        "synthesize": "synthesize",
    })
    g.add_edge("calculate", "remember")
    g.add_edge("datetime", "synthesize")
    g.add_conditional_edges("retrieve_documents", after_retrieve, {
        "summarize": "summarize",
        "synthesize": "synthesize",
    })
    g.add_edge("summarize", "synthesize")
    g.add_edge("background_research", "synthesize")
    g.add_edge("plan", "search")
    g.add_edge("search", "fetch_sources")
    g.add_edge("fetch_sources", "analyze_evidence")
    g.add_conditional_edges("analyze_evidence", evidence_sufficient, {
        "synthesize": "synthesize",
        "multi_tool": "multi_tool",
        "plan": "plan",
    })
    g.add_edge("multi_tool", "synthesize")
    g.add_edge("synthesize", "remember")
    g.add_edge("remember", END)

    return g.compile()


def get_graph():
    global _graph_singleton
    if _graph_singleton is None:
        _graph_singleton = build_graph()
    return _graph_singleton