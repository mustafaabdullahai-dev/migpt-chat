"""Summarization tool — map-reduce summarization for large content sources.

Targeted kinds: webpage, pdf, document, search results, conversation, research
collection. Long inputs are chunked and summarized hierarchically instead of being
sent whole to the LLM.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.summarizer import summarize_text

KINDS = {"webpage", "pdf", "document", "search_results", "conversation", "research_collection", "generic"}


class SummarizeArgs(BaseModel):
    text: str = Field(..., description="The content to summarize (bounded: long inputs are chunked).")
    kind: str = Field(
        default="generic",
        description="One of: webpage | pdf | document | search_results | conversation | research_collection | generic.",
    )


class SummarizeTool:
    name = "summarizer"
    is_async = True

    async def __call__(self, text: str, kind: str = "generic") -> str:
        kind = kind if kind in KINDS else "generic"
        result = await summarize_text(text)
        if not result.summary:
            return "The content could not be summarized (it may be empty or unreadable)."
        return f"[{kind} summarized via {result.method}] {result.summary}"


summarize_tool = SummarizeTool()