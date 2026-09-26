"""Document-search (RAG) tool used inside the agent graph."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.schemas import RetrievedChunk
from app.rag.retriever import retrieve


class DocumentSearchArgs(BaseModel):
    query: str = Field(..., description="Question to answer from uploaded documents.")
    document_ids: list[str] = Field(default_factory=list, description="Restrict to these documents.")


class DocumentSearchTool:
    name = "document_search"
    is_async = False

    def __call__(self, query: str, document_ids: list[str] | None = None) -> list[RetrievedChunk]:
        return retrieve(query, document_ids=document_ids or None)


document_search_tool = DocumentSearchTool()