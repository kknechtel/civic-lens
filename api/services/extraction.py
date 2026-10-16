"""
Document re-extraction service.

For documents whose extracted_text is empty / too short to be useful
(typically scanned PDFs that never got OCR'd), this module:

  1. Fetches the file from S3 by its s3_key.
  2. Runs the OCR cascade (pdfplumber -> Gemini Vision).
  3. Updates Document.extracted_text + Document.page_count.
  4. (Optional) chains into the chunking + embedding pipeline so the
     document immediately becomes searchable in chat without a second
     re-ingestion pass.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlalchemy.orm import Session

from models.document import Document

logger = logging.getLogger(__name__)


# Documents below this many chars in extracted_text are candidates for
# re-extraction. Trivially short text (whitespace-only, nav stubs) is
# common when a previous extraction step ran but failed silently.
EMPTY_TEXT_THRESHOLD = 100


def list_empty_text_documents(db: Session, limit: Optional[int] = None) -> list[Document]:
    """Documents that need OCR / re-extraction."""
    q = (
        db.query(Document)
        .filter(
            (Document.extracted_text.is_(None))
            | (Document.extracted_text == "")
            # Length check via SQL avoids loading the column into Python:
        )
    )
    # Add a length check too — covers stub text like just a filename
    from sqlalchemy import or_, func, literal
    q = (
        db.query(Document)
        .filter(
            or_(
                Document.extracted_text.is_(None),
                func.length(Document.extracted_text) < literal(EMPTY_TEXT_THRESHOLD),
            )
        )
        # Only PDFs — xlsx/csv etc. are handled separately and OCR won't help
        .filter(Document.filename.ilike("%.pdf"))
    )
    if limit:
        q = q.limit(limit)
    return q.all()


async def reextract_one(
    db: Session,
    doc: Document,
    *,
    auto_chunk: bool = True,
) -> dict:
    """Re-OCR a single document. Returns a small summary."""
    from services.s3_service import S3Service
    from services.ocr_pipeline import extract_pdf_to_markdown

    if not doc.s3_key:
        return {"document_id": str(doc.id), "skipped": True, "reason": "no_s3_key"}
    if not (doc.filename or "").lower().endswith(".pdf"):
        return {"document_id": str(doc.id), "skipped": True, "reason": "not_pdf"}

    # Fetch bytes from S3 (sync; small docs fine, large will block briefly).
    try:
        s3 = S3Service()
        pdf_bytes = await asyncio.to_thread(s3.download_file, doc.s3_key)
    except Exception as exc:
        logger.exception("S3 fetch failed for %s", doc.filename)
        return {"document_id": str(doc.id), "error": f"s3_fetch_failed: {exc}"}

    if not pdf_bytes:
        return {"document_id": str(doc.id), "error": "empty_s3_object"}

    # Run the cascade.
    result = await extract_pdf_to_markdown(pdf_bytes, filename=doc.filename or "")
    if not result.success or not result.markdown:
        return {
            "document_id": str(doc.id), "filename": doc.filename,
            "error": result.error or "ocr_failed", "tier": result.tier,
        }

    # Persist the new extracted_text + page_count.
    doc.extracted_text = result.markdown
    doc.page_count = result.page_count or doc.page_count
    db.commit()
    db.refresh(doc)

    # OCR is free (text layer + Tesseract) but still tracked at zero so the
    # admin sees coverage of the corpus.
    try:
        from services.usage import record_usage
        record_usage(
            db,
            source="ocr",
            model=result.tier or "unknown",
            estimated_cost_usd=float(result.estimated_cost or 0.0),
            resource_type="document",
            resource_id=str(doc.id),
            metadata={
                "tier": result.tier,
                "page_count": result.page_count,
                "chars": len(result.markdown),
                "elapsed_ms": int(result.processing_time_ms),
            },
        )
    except Exception:
        logger.warning("usage record skipped for ocr/%s", doc.filename, exc_info=True)

    summary = {
        "document_id": str(doc.id),
        "filename": doc.filename,
        "tier": result.tier,
        "chars": len(result.markdown),
        "page_count": result.page_count,
        "estimated_cost": round(result.estimated_cost, 5),
        "elapsed_ms": int(result.processing_time_ms),
    }

    # Chain into chunking + embedding so the doc becomes immediately
    # available for chat / search without a second ingestion pass.
    if auto_chunk:
        try:
            from services.ingestion import ingest_document
            ingest_summary = await asyncio.to_thread(ingest_document, db, doc)
            summary["ingest"] = ingest_summary
        except Exception as exc:
            logger.exception("ingest after OCR failed for %s", doc.filename)
            summary["ingest_error"] = str(exc)[:200]

    return summary


async def reextract_batch(
    db: Session,
    limit: int = 20,
    auto_chunk: bool = True,
    concurrency: int = 4,
) -> dict:
    """Re-OCR up to `limit` empty-text documents in parallel.

    Each doc gets its own short-lived DB session inside reextract_one_isolated
    so they don't fight over the shared session. Concurrency capped at 4 to
    stay sane with Tesseract CPU + Voyage rate limits.
    """
    docs = list_empty_text_documents(db, limit=limit)
    out: list[dict] = []
    ok = 0; skipped = 0; errors = 0

    sem = asyncio.Semaphore(concurrency)

    async def run_one(doc_id: str) -> dict:
        from database import SessionLocal as _SL
        async with sem:
            local_db = _SL()
            try:
                doc = local_db.query(Document).filter(Document.id == doc_id).first()
                if not doc:
                    return {"document_id": doc_id, "skipped": True, "reason": "vanished"}
                return await reextract_one(local_db, doc, auto_chunk=auto_chunk)
            finally:
                local_db.close()

    # Capture ids up-front so we don't hold the parent session's rows after closing.
    doc_ids = [str(d.id) for d in docs]

    results = await asyncio.gather(*[run_one(did) for did in doc_ids],
                                   return_exceptions=True)
    for res in results:
        if isinstance(res, Exception):
            errors += 1
            out.append({"error": str(res)[:200]})
            continue
        if res.get("skipped"):
            skipped += 1
        elif res.get("error"):
            errors += 1
        else:
            ok += 1
        out.append(res)

    return {
        "total": len(doc_ids),
        "ok": ok,
        "skipped": skipped,
        "errors": errors,
        "details": out,
    }


def extraction_status(db: Session) -> dict:
    """High-level counts: how many docs need OCR vs. have text."""
    from sqlalchemy import func, literal

    total = db.query(func.count(Document.id)).scalar() or 0
    pdfs = db.query(func.count(Document.id)).filter(
        Document.filename.ilike("%.pdf")
    ).scalar() or 0
    empty_pdfs = db.query(func.count(Document.id)).filter(
        Document.filename.ilike("%.pdf"),
        (Document.extracted_text.is_(None))
        | (func.length(Document.extracted_text) < literal(EMPTY_TEXT_THRESHOLD)),
    ).scalar() or 0
    has_text_pdfs = pdfs - empty_pdfs
    return {
        "documents_total": total,
        "pdfs_total": pdfs,
        "pdfs_with_text": has_text_pdfs,
        "pdfs_needing_ocr": empty_pdfs,
    }
