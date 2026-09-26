"""Document upload / management (RAG)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import resolve_user
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import sniff_file_signature, validate_filename
from app.db import crud
from app.db.base import get_db
from app.models.schemas import DocumentMeta
from app.rag.ingestion import ingest_bytes, remove_document_vectors

logger = get_logger("api.documents")
router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentMeta)
async def upload_document(
    file: UploadFile = File(...),
    user_id: str = Depends(resolve_user),
    db: Session = Depends(get_db),
):
    filename = file.filename or "upload"
    try:
        filename = validate_filename(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_size_mb} MB limit")
    ext = filename.rsplit(".", 1)[-1].lower()
    if not sniff_file_signature(content, "." + ext):
        raise HTTPException(status_code=400, detail="File content does not match its extension")

    try:
        result = ingest_bytes(db, user_id, filename, content)
    except Exception as exc:
        logger.warning(
            "document ingest failed",
            extra={"event": "upload.error", "file_name": filename, "error": str(exc)},
        )
        raise HTTPException(status_code=400, detail="Could not process this file. Please check the format and try again.")

    doc = crud.get_document(db, result["document_id"], user_id)
    return DocumentMeta(
        document_id=result["document_id"],
        filename=doc.filename,
        content_type=doc.content_type,
        size_bytes=doc.size_bytes,
        chunks=result["chunks"],
        uploaded_at=doc.uploaded_at,
    )


@router.get("", response_model=list[DocumentMeta])
def list_documents(user_id: str = Depends(resolve_user), db: Session = Depends(get_db)):
    return [
        DocumentMeta(
            document_id=d.document_id,
            filename=d.filename,
            content_type=d.content_type,
            size_bytes=d.size_bytes,
            chunks=d.chunks,
            uploaded_at=d.uploaded_at,
        )
        for d in crud.list_documents(db, user_id)
    ]


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: str, user_id: str = Depends(resolve_user), db: Session = Depends(get_db)):
    if not crud.delete_document(db, document_id, user_id):
        raise HTTPException(status_code=404, detail="Document not found")
    remove_document_vectors([document_id])
    return None