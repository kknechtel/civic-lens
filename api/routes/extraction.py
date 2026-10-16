"""
Document re-extraction (OCR) endpoints. Admin-only.

  GET  /api/extraction/status         counts by extraction state
  POST /api/extraction/run            re-OCR a batch of empty-text docs
  POST /api/extraction/run/{doc_id}   re-OCR a single document
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models.document import Document
from models.user import User
from services.extraction import (
    extraction_status,
    reextract_batch,
    reextract_one,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_admin(user: User):
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("/status")
def status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return extraction_status(db)


@router.post("/run")
async def run(
    limit: int = Query(20, ge=1, le=200),
    auto_chunk: bool = Query(True, description="Chunk + embed each doc immediately after OCR succeeds."),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """OCR the next `limit` documents that have no extracted text. Returns
    per-document summaries. Caller drives the loop — keep calling until
    /status reports pdfs_needing_ocr == 0."""
    _require_admin(user)
    return await reextract_batch(db, limit=limit, auto_chunk=auto_chunk)


@router.post("/run/{document_id}")
async def run_one(
    document_id: str,
    auto_chunk: bool = Query(True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_admin(user)
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return await reextract_one(db, doc, auto_chunk=auto_chunk)
