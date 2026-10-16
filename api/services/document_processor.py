"""
Document processing pipeline: extract → enrich → chunk + embed.

  1. services.text_extract — local only (PDF text layer + Tesseract, docx, xlsx…)
  2. services.doc_enrich   — one flash-lite call: title, summary, classification
  3. services.ingestion    — chunks + embeddings for search and chat

Runs automatically after the nightly scrape (scripts.scheduled_scrape) and
for backfills (scripts.enrich_all). Statuses: processed | no_text | error.
A doc that errors is retried on later runs up to MAX_ATTEMPTS.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MIN_TEXT_CHARS = 50
# Docs the meeting pipeline owns (transcribe → summarize) — skip them here.
RECORDING_PREFIX = "recording"


@dataclass
class ProcessResult:
    document_id: str
    status: str
    method: str = ""
    chars: int = 0
    cost_usd: float = 0.0
    diff: dict = field(default_factory=dict)
    error: Optional[str] = None


async def process_one(db, doc, *, enrich: bool = True, reextract: bool = False,
                      dry_run: bool = False) -> ProcessResult:
    """Process one Document using the caller's session. Commits unless dry_run."""
    from services import doc_enrich
    from services.ingestion import ingest_document
    from services.s3_service import S3Service
    from services.text_extract import extract
    from services.ai import pin

    meta = dict(doc.metadata_ or {})
    res = ProcessResult(str(doc.id), doc.status or "")
    if (doc.doc_type or "").startswith(RECORDING_PREFIX) or (doc.content_type or "").startswith("video/youtube"):
        res.status, res.method = doc.status or "", "recording_skipped"
        return res

    try:
        # 1. Text — reuse what's stored unless asked to re-extract.
        text = doc.extracted_text or ""
        if reextract or len(text.strip()) < MIN_TEXT_CHARS:
            content = await asyncio.to_thread(S3Service().download_file, doc.s3_key)
            ex = await extract(doc.filename or "", content or b"")
            res.method = ex.method
            if len(ex.text.strip()) >= len(text.strip()):
                text = ex.text
                if not dry_run:
                    doc.extracted_text = text
                    if ex.page_count:
                        doc.page_count = ex.page_count
            meta.update({"extract_method": ex.method, **ex.meta})
        res.chars = len(text)

        if len(text.strip()) < MIN_TEXT_CHARS:
            res.status = "no_text"
            if not dry_run:
                doc.status, doc.metadata_ = "no_text", meta
                db.commit()
            return res

        # 2. Enrich (title / summary / classification). Optional: without a
        # a Gemini key the doc is still indexed with scraper-derived metadata.
        if enrich and pin("enrichment", "gemini") is None:
            enrich = False
            meta["enrich_skipped"] = "No gemini key configured (Admin → AI)"
        if enrich:
            er = await asyncio.to_thread(doc_enrich.analyze, doc, text)
            res.cost_usd = er.cost_usd
            if er.ok:
                if dry_run:
                    res.diff = {k: (getattr(doc, k, None) if k != "body" else meta.get("body"), v)
                                for k, v in er.changes.items()}
                    res.diff["notes"] = (doc.notes, er.summary)
                else:
                    doc.metadata_ = meta
                    res.diff = doc_enrich.apply(doc, er)
                    meta = dict(doc.metadata_)
            else:
                # Fail the doc so it's retried (up to MAX_ATTEMPTS) rather than
                # sitting "processed" with no title or summary.
                raise RuntimeError(f"enrichment failed: {er.error}")

        if dry_run:
            res.status = "processed"
            return res

        # 3. Chunk + embed. ingest_document commits.
        meta.pop("process_error", None)
        doc.metadata_ = meta
        doc.status = "processed"
        db.commit()
        await asyncio.to_thread(ingest_document, db, doc, True)
        res.status = "processed"
        return res

    except Exception as exc:
        db.rollback()  # first: the failed flush leaves the session unusable
        logger.error("process %s failed: %s", doc.filename, exc, exc_info=True)
        meta = dict(doc.metadata_ or {})
        meta["process_attempts"] = int(meta.get("process_attempts") or 0) + 1
        meta["process_error"] = str(exc)[:500]
        if not dry_run:
            doc.status, doc.metadata_ = "error", meta
            db.commit()
        res.status, res.error = "error", str(exc)
        return res


def pending_query(db):
    """Docs that still need processing: new, or errored with attempts left."""
    from sqlalchemy import or_, cast, Integer
    from models.document import Document
    attempts = cast(Document.metadata_["process_attempts"].astext, Integer)
    return (db.query(Document)
            .filter(or_(Document.status == "uploaded",
                        (Document.status == "error") & (or_(attempts.is_(None), attempts < MAX_ATTEMPTS))))
            .filter(~Document.doc_type.like(f"{RECORDING_PREFIX}%") | Document.doc_type.is_(None))
            .order_by(Document.created_at.desc()))


async def process_pending(limit: Optional[int] = None, concurrency: int | None = None) -> dict:
    """Process every pending doc (newest first). Used after the nightly scrape."""
    from database import SessionLocal
    from models.document import Document

    with SessionLocal() as db:
        q = pending_query(db).with_entities(Document.id)
        ids = [str(r[0]) for r in (q.limit(limit) if limit else q).all()]

    # Docs in flight at once. OCR is separately capped process-wide
    # (services.tesseract_ocr._OCR_SLOTS), so this mainly bounds memory held
    # by downloaded files and in-progress text.
    concurrency = concurrency or int(os.environ.get("PROCESS_CONCURRENCY", "2"))
    sem = asyncio.Semaphore(concurrency)
    totals = {"processed": 0, "no_text": 0, "error": 0, "skipped": 0, "cost_usd": 0.0}

    async def run(doc_id: str):
        async with sem:
            with SessionLocal() as db:
                doc = db.get(Document, doc_id)
                if not doc:
                    return
                r = await process_one(db, doc)
                totals[r.status if r.status in totals else "skipped"] += 1
                totals["cost_usd"] += r.cost_usd

    await asyncio.gather(*(run(i) for i in ids))
    totals["cost_usd"] = round(totals["cost_usd"], 4)
    logger.info("process_pending: %d docs → %s", len(ids), totals)
    return totals


async def process_document(document_id: str):
    """Process a single document by id (used by /api/processing routes)."""
    from database import SessionLocal
    from models.document import Document

    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if not doc:
            logger.error("Document %s not found", document_id)
            return
        doc.status = "processing"
        db.commit()
        await process_one(db, doc)
