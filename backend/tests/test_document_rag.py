"""Document RAG ingestion tests for every supported format (PDF, TXT, MD, DOCX, CSV).

Exercises the full Upload -> Parse -> Clean -> Chunk -> Embed -> FAISS -> Retriever chain
for each format via the public upload API + the document_search tool.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

DIR = Path(__file__).resolve().parent


def _minimal_pdf(text: str) -> bytes:
    """Build a valid one-page PDF containing extractable text (no external dep)."""
    text = text.replace("(", "(").replace(")", ")")
    content = ("BT /F1 12 Tf 72 700 Td (" + text + ") Tj ET").encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)


def _minimal_docx(text: str) -> bytes:
    from docx import Document

    d = Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


PAPERS = [
    ("octowalrus_query. The embedding model maps text and queries into a shared vector space.",
     "the vector store retrieves chunks by cosine similarity and feeds them to the LLM."),
    ("octowalrus_chunking splits long documents into overlapping passages before embedding.",
     "chunk overlap preserves context across section boundaries."),
]


@pytest.fixture()
def uploaded_docs(client):
    """Upload one document per supported format; returns list of (filename, document_id, text)."""
    bodies = [
        ("paper.pdf", _minimal_pdf(" ".join(PAPERS[0])), "application/pdf"),
        ("notes.txt", ("octowalrus_txt " + " ".join(PAPERS[1])).encode(), "text/plain"),
        ("readme.md", ("# octowalrus_md\n\n" + " ".join(PAPERS[0])).encode(), "text/markdown"),
        ("report.docx", _minimal_docx("octowalrus_docx " + " ".join(PAPERS[1])), 
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("data.csv", ("topic,detail\nRAG,octowalrus_csv retrieval chunk\nscore," + "0.9").encode(), "text/csv"),
    ]
    uploaded = []
    for filename, content, _ct in bodies:
        resp = client.post("/api/documents/upload", files={"file": (filename, content, "application/octet-stream")})
        assert resp.status_code == 200, resp.text
        doc = resp.json()
        assert doc["chunks"] >= 1
        uploaded.append((filename, doc["document_id"]))
    yield uploaded
    for _filename, doc_id in uploaded:
        client.delete(f"/api/documents/{doc_id}")


@pytest.mark.parametrize(
    "needle",
    ["octowalrus_query", "octowalrus_txt", "octowalrus_md", "octowalrus_docx", "octowalrus_csv"],
)
def test_every_format_ingests_and_retrieves(client, uploaded_docs, needle):
    from app.rag.retriever import retrieve

    results = retrieve(needle, k=8, document_ids=[d for _, d in uploaded_docs])
    assert results, "no chunks retrieved for uploaded documents"
    hit = next((r for r in results if needle in r.text), None)
    assert hit is not None, f"expected a chunk containing {needle!r}, got {[r.text[:60] for r in results[:3]]}"
    assert hit.document_title in {name for name, _ in uploaded_docs}


@pytest.mark.asyncio
async def test_document_search_tool_over_uploads(client, uploaded_docs):
    from app.agents.nodes.helpers import run_tool

    doc_id = uploaded_docs[0][1]
    result, record = await run_tool("document_search", query="octowalrus_query embedding vector", document_ids=[doc_id])
    assert record.status == "success"
    assert result, "document_search returned nothing"
    assert all(r.document_id == doc_id for r in result)


def test_bad_extension_rejected(client):
    resp = client.post("/api/documents/upload", files={"file": ("evil.exe", b"MZ...", "application/octet-stream")})
    assert resp.status_code == 400