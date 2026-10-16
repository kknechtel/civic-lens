#!/usr/bin/env python3
"""
Drain the empty-text-PDF queue by fanning out OCR work to the
`ah-pdf-ocr` Lambda. Runs on EC2 (or anywhere with RDS access) and
just orchestrates: every doc that needs OCR is dispatched to Lambda;
when the Lambda returns markdown, this driver writes it back to RDS
and chains into chunking + embedding.

The Lambda does the CPU-heavy OCR; this driver only does I/O. So the
EC2 box can run it without contending with the API workers — a far cry
from the local-Tesseract approach that pinned both vCPUs.

Usage (run on EC2 or locally with the prod DATABASE_URL):
    /opt/atlantic-highlands/api/venv/bin/python \
        /opt/atlantic-highlands/api/scripts/run_ocr_lambda.py

Env vars:
    LAMBDA_FN     function name           (default: ah-pdf-ocr)
    AWS_REGION    region                  (default: us-east-1)
    OCR_PARALLEL  max in-flight Lambdas   (default: 30, lambda max is 50)
    OCR_LIMIT     stop after N docs       (default: unlimited)
    OCR_MAX_PAGES per-doc page cap        (default: 30)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

# Make `services.*` and `models.*` importable when run as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_API = os.path.dirname(_HERE)
if _API not in sys.path:
    sys.path.insert(0, _API)

import boto3
from botocore.config import Config

import models  # noqa  (force-load every model into Base.metadata)
from database import SessionLocal
from models.document import Document
from services.ingestion import ingest_document

LAMBDA_FN = os.environ.get("LAMBDA_FN", "ah-pdf-ocr")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
PARALLEL = int(os.environ.get("OCR_PARALLEL", "30"))
LIMIT = int(os.environ.get("OCR_LIMIT", "0"))   # 0 = no cap
MAX_PAGES = int(os.environ.get("OCR_MAX_PAGES", "30"))
EMPTY_TEXT_THRESHOLD = 100

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ocr-lambda")


# Generous client timeouts so we don't drop legitimate Lambda runs that
# happen to take 60+ seconds on a long PDF.
_lambda_client = boto3.client(
    "lambda",
    region_name=AWS_REGION,
    config=Config(read_timeout=360, connect_timeout=15, retries={"max_attempts": 2}),
)


def list_pending(session) -> list[tuple[str, str, str]]:
    """Return [(doc_id, filename, s3_key)] for PDFs that still need OCR."""
    from sqlalchemy import or_, func, literal
    q = (
        session.query(Document.id, Document.filename, Document.s3_key)
        .filter(Document.filename.ilike("%.pdf"))
        .filter(
            or_(
                Document.extracted_text.is_(None),
                func.length(Document.extracted_text) < literal(EMPTY_TEXT_THRESHOLD),
            )
        )
        .filter(Document.s3_key.isnot(None))
    )
    if LIMIT:
        q = q.limit(LIMIT)
    return [(str(d), f, k) for d, f, k in q.all()]


def invoke_one(doc_id: str, filename: str, s3_key: str) -> dict:
    """Send one PDF to Lambda. Synchronous boto3 call; runs in a thread."""
    t0 = time.time()
    try:
        resp = _lambda_client.invoke(
            FunctionName=LAMBDA_FN,
            InvocationType="RequestResponse",
            Payload=json.dumps({"pdf_key": s3_key, "max_pages": MAX_PAGES}).encode(),
        )
    except Exception as exc:
        return {"doc_id": doc_id, "filename": filename, "error": f"invoke_failed: {exc}",
                "elapsed_s": time.time() - t0}

    if resp.get("FunctionError"):
        return {"doc_id": doc_id, "filename": filename,
                "error": f"lambda_error: {resp['FunctionError']}",
                "elapsed_s": time.time() - t0}

    try:
        payload = json.loads(resp["Payload"].read())
    except Exception as exc:
        return {"doc_id": doc_id, "filename": filename, "error": f"bad_payload: {exc}",
                "elapsed_s": time.time() - t0}

    payload["doc_id"] = doc_id
    payload["filename"] = filename
    payload["elapsed_s"] = time.time() - t0
    return payload


def persist_and_ingest(payload: dict) -> dict:
    """Write the Lambda's markdown to documents.extracted_text + chunk+embed.
    Each call uses its own short-lived DB session."""
    md = (payload.get("markdown") or "").strip()
    pages = int(payload.get("pages_ocrd") or 0)
    if not md or len(md) < EMPTY_TEXT_THRESHOLD:
        return {"doc_id": payload["doc_id"], "filename": payload["filename"],
                "skipped": True, "reason": "no_meaningful_text",
                "char_count": len(md), "elapsed_s": payload.get("elapsed_s", 0)}

    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == payload["doc_id"]).first()
        if not doc:
            return {"doc_id": payload["doc_id"], "skipped": True, "reason": "vanished"}
        doc.extracted_text = md
        if pages and not doc.page_count:
            doc.page_count = pages
        db.commit()
        # Chunk + embed (synchronous; Voyage call inside)
        ingest_summary = ingest_document(db, doc)
    except Exception as exc:
        db.rollback()
        return {"doc_id": payload["doc_id"], "filename": payload["filename"],
                "error": f"persist_failed: {exc}",
                "char_count": len(md), "elapsed_s": payload.get("elapsed_s", 0)}
    finally:
        db.close()

    return {
        "doc_id": payload["doc_id"], "filename": payload["filename"],
        "tier": payload.get("tier"),
        "pages_ocrd": pages,
        "char_count": len(md),
        "chunks": ingest_summary.get("chunks", 0) if isinstance(ingest_summary, dict) else 0,
        "elapsed_s": payload.get("elapsed_s", 0),
    }


def process_doc(doc_id: str, filename: str, s3_key: str) -> dict:
    payload = invoke_one(doc_id, filename, s3_key)
    if "error" in payload:
        return payload
    return persist_and_ingest(payload)


def _smoke_test_invoke() -> Optional[str]:
    """Probe the Lambda once before fanning out 2k+ invocations. Returns
    None on success, or an error string we can use to fail-fast."""
    try:
        resp = _lambda_client.invoke(
            FunctionName=LAMBDA_FN,
            InvocationType="DryRun",  # checks IAM + function existence; no actual run
        )
        if resp.get("StatusCode") not in (200, 204):
            return f"DryRun returned StatusCode={resp.get('StatusCode')}"
        return None
    except Exception as exc:
        return str(exc)


def main() -> int:
    # Don't blow through 2,194 invocations with stale-IAM AccessDenied.
    # DryRun probes the IAM perm + function existence in <1s.
    err = _smoke_test_invoke()
    if err:
        log.error("Lambda smoke test failed (will not start drain): %s", err)
        log.error("If this is AccessDenied, IAM policy is still propagating — "
                  "wait 30-60s and retry.")
        return 2

    sess = SessionLocal()
    try:
        pending = list_pending(sess)
    finally:
        sess.close()

    log.info("starting: %d pending PDFs, parallel=%d, lambda=%s",
             len(pending), PARALLEL, LAMBDA_FN)
    if not pending:
        log.info("nothing to do")
        return 0

    started_at = time.time()
    ok = 0; skipped = 0; errors = 0
    total_chars = 0; total_chunks = 0; total_pages = 0

    def status_line(extra: str = ""):
        elapsed = time.time() - started_at
        rate = (ok + skipped + errors) / elapsed if elapsed > 0 else 0
        return (f"ok={ok} skipped={skipped} errors={errors}  "
                f"pages={total_pages} chars={total_chars} chunks={total_chunks}  "
                f"{rate:.1f} docs/s  elapsed={int(elapsed)}s{extra}")

    with ThreadPoolExecutor(max_workers=PARALLEL, thread_name_prefix="ocr") as ex:
        futures = {ex.submit(process_doc, d, f, k): (d, f) for d, f, k in pending}
        completed = 0
        for fut in as_completed(futures):
            completed += 1
            res = fut.result()
            if res.get("error"):
                errors += 1
                if errors <= 5:
                    log.warning("ERR %s: %s", res.get("filename", "?")[:60], res["error"][:120])
            elif res.get("skipped"):
                skipped += 1
            else:
                ok += 1
                total_chars += res.get("char_count", 0) or 0
                total_chunks += res.get("chunks", 0) or 0
                total_pages += res.get("pages_ocrd", 0) or 0

            if completed % 20 == 0 or completed == len(pending):
                log.info("[%d/%d] %s", completed, len(pending), status_line())

    elapsed = time.time() - started_at
    log.info("DONE in %ds  %s", int(elapsed), status_line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
