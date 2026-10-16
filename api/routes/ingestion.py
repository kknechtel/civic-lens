"""
RAG ingestion endpoints. Admin-only.

POST /api/ingestion/run        -- ingest all pending documents
POST /api/ingestion/run/{id}   -- re-ingest one document
GET  /api/ingestion/status     -- chunk + embedding counts
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db
from auth import get_current_user
from models.user import User
from services.ingestion import ingest_all_pending, ingest_one

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_admin(user: User):
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("/status")
def ingestion_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    docs = db.execute(text("SELECT count(*) FROM documents")).scalar() or 0
    docs_with_text = db.execute(text(
        "SELECT count(*) FROM documents WHERE extracted_text IS NOT NULL AND length(extracted_text) > 100"
    )).scalar() or 0
    chunks = db.execute(text("SELECT count(*) FROM document_chunks")).scalar() or 0
    docs_chunked = db.execute(text(
        "SELECT count(DISTINCT document_id) FROM document_chunks"
    )).scalar() or 0

    # Embedding coverage — only meaningful if pgvector is installed
    embedded_docs = 0
    embedded_chunks = 0
    try:
        embedded_docs = db.execute(text(
            "SELECT count(*) FROM documents WHERE embedding IS NOT NULL"
        )).scalar() or 0
        embedded_chunks = db.execute(text(
            "SELECT count(*) FROM document_chunks WHERE embedding IS NOT NULL"
        )).scalar() or 0
    except Exception:
        pass

    return {
        "documents_total": docs,
        "documents_with_text": docs_with_text,
        "documents_chunked": docs_chunked,
        "chunks_total": chunks,
        "documents_embedded": embedded_docs,
        "chunks_embedded": embedded_chunks,
    }


@router.post("/run")
def run_ingestion(
    limit: int = Query(50, ge=1, le=2000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Ingest the next `limit` un-chunked documents. Run repeatedly until clear."""
    _require_admin(user)
    return ingest_all_pending(db, limit=limit)


@router.post("/run/{document_id}")
def run_one(
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Re-ingest a single document (deletes & recreates its chunks)."""
    _require_admin(user)
    return ingest_one(db, document_id, force=True)
