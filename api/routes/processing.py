"""Document processing routes - trigger OCR, summarization, and tagging."""
import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models.document import Document
from models.user import User
from auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


class ProcessRequest(BaseModel):
    document_ids: List[str] | None = None  # specific docs, or None for all unprocessed
    project_id: str | None = None


@router.post("/run")
async def process_documents(
    req: ProcessRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Trigger OCR + AI analysis on documents."""
    query = db.query(Document)

    if req.document_ids:
        query = query.filter(Document.id.in_(req.document_ids))
    elif req.project_id:
        query = query.filter(
            Document.project_id == req.project_id,
            Document.status.in_(["uploaded", "error"]),
        )
    else:
        from services.document_processor import pending_query
        query = pending_query(db)

    docs = query.limit(100).all()

    if not docs:
        return {"detail": "No documents to process", "count": 0}

    from services.document_processor import process_document

    count = 0
    for doc in docs:
        background_tasks.add_task(process_document, str(doc.id))
        count += 1

    return {"detail": f"Processing {count} documents", "count": count}


@router.post("/single/{document_id}")
async def process_single_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Process a single document."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    from services.document_processor import process_document
    background_tasks.add_task(process_document, str(doc.id))

    return {"detail": f"Processing {doc.filename}", "document_id": document_id}


@router.get("/stats")
def processing_stats(
    project_id: str = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get processing status counts."""
    query = db.query(Document)
    if project_id:
        query = query.filter(Document.project_id == project_id)

    total = query.count()
    processed = query.filter(Document.status == "processed").count()
    processing = query.filter(Document.status == "processing").count()
    uploaded = query.filter(Document.status == "uploaded").count()
    errors = query.filter(Document.status == "error").count()

    return {
        "total": total,
        "processed": processed,
        "processing": processing,
        "uploaded": uploaded,
        "errors": errors,
    }
