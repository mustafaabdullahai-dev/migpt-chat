"""Graph node: retrieve relevant chunks from uploaded documents (RAG)."""

from __future__ import annotations

from app.agents.nodes.helpers import run_tool
from app.agents.state import AgentState
from app.models.schemas import RetrievedChunk
from app.services.stream import emit_status


async def retrieve_documents(state: AgentState) -> dict:
    query = state.get("query", "")
    doc_ids = state.get("documents", [])

    emit_status("Searching your documents…")
    result, record = await run_tool("document_search", query=query, document_ids=doc_ids)

    retrieved: list[RetrievedChunk] = []
    if isinstance(result, list):
        retrieved = [r for r in result if isinstance(r, RetrievedChunk)]

    if not retrieved:
        emit_status("No relevant document content found — answering from general knowledge.")
        return {
            "retrieved": [],
            "confidence": 0.5,
            "tool_runs": [record] if record.status == "success" else [],
        }

    emit_status(f"Found context in {len(retrieved)} document section(s).")
    return {
        "retrieved": retrieved,
        "confidence": 0.9,
        "tool_runs": [record] if record.status == "success" else [],
    }