"""
AHNJ PDF OCR Lambda — adapted from bank-processor/lambda-pdf-ocr.

Receives an S3 key, downloads the PDF, OCRs it page-by-page with
Tesseract, returns markdown text. Caller (the EC2 driver) is
responsible for writing the result back to RDS — keeping this Lambda
stateless means we don't need VPC config, RDS credentials, or
Secrets Manager access.

Input event:
    {
        "bucket": "atlantic-highlands-documents-738265942536",  # optional, env default
        "pdf_key": "documents/.../foo.pdf",                      # required
        "max_pages": 30,                                          # optional, default 30
        "dpi": 150                                                # optional, default 150
    }

Output:
    {
        "pdf_key": "...",
        "markdown": "## Page 1\\n\\n<text>\\n\\n## Page 2\\n\\n<text>...",
        "pages_ocrd": 7,
        "char_count": 12345,
        "tier": "tesseract"
    }

Crash-safe: a tesseract or pymupdf failure on one page is logged and the
loop continues; a fatal lambda crash kills only this invocation, not
the calling driver.
"""
from __future__ import annotations

import io
import logging
import os

import boto3
import pymupdf
import pytesseract
from PIL import Image

log = logging.getLogger()
log.setLevel(logging.INFO)

s3 = boto3.client("s3")

DEFAULT_BUCKET = os.environ.get("S3_BUCKET", "atlantic-highlands-documents-738265942536")
HARD_PAGE_CAP = 50  # absolute upper bound regardless of input — protects timeout


def handler(event, context):
    bucket = event.get("bucket", DEFAULT_BUCKET)
    pdf_key = event["pdf_key"]
    max_pages = min(int(event.get("max_pages", 30)), HARD_PAGE_CAP)
    dpi = int(event.get("dpi", 150))

    log.info("OCR start: bucket=%s key=%s max_pages=%d dpi=%d",
             bucket, pdf_key, max_pages, dpi)

    # 1. Fetch PDF from S3
    try:
        resp = s3.get_object(Bucket=bucket, Key=pdf_key)
        pdf_bytes = resp["Body"].read()
    except s3.exceptions.NoSuchKey:
        return {"pdf_key": pdf_key, "markdown": "", "pages_ocrd": 0,
                "char_count": 0, "tier": "tesseract", "error": "s3_no_such_key"}
    except Exception as exc:
        log.error("S3 fetch failed: %s", exc)
        return {"pdf_key": pdf_key, "markdown": "", "pages_ocrd": 0,
                "char_count": 0, "tier": "tesseract", "error": f"s3_fetch_failed: {exc}"}

    if not pdf_bytes:
        return {"pdf_key": pdf_key, "markdown": "", "pages_ocrd": 0,
                "char_count": 0, "tier": "tesseract", "error": "empty_pdf"}

    # 2. Try the native text layer first — many municipal PDFs already have one.
    #    This is a lot faster than OCR when it works.
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        native_pages = []
        try:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                text = page.get_text() or ""
                if text.strip():
                    native_pages.append(f"## Page {i + 1}\n\n{text.strip()}")
            native_md = "\n\n".join(native_pages)
        finally:
            doc.close()

        if len(native_md) >= 300:
            log.info("native text: %d pages, %d chars", len(native_pages), len(native_md))
            return {
                "pdf_key": pdf_key,
                "markdown": native_md,
                "pages_ocrd": len(native_pages),
                "char_count": len(native_md),
                "tier": "pdfplumber",  # naming match w/ EC2 cascade for parity
            }
    except Exception as exc:
        log.warning("native text extract failed: %s — falling through to OCR", exc)

    # 3. OCR each rendered page
    pages_md = []
    pages_ocrd = 0
    pages_failed = 0
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        log.error("pymupdf open failed: %s", exc)
        return {"pdf_key": pdf_key, "markdown": "", "pages_ocrd": 0,
                "char_count": 0, "tier": "tesseract", "error": f"pymupdf_open: {exc}"}

    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            try:
                pix = page.get_pixmap(dpi=dpi)
                png_bytes = pix.tobytes("png")
                pix = None  # release memory before tesseract subprocess fork
                img = Image.open(io.BytesIO(png_bytes))
                try:
                    page_text = pytesseract.image_to_string(img, config="--oem 1 --psm 6")
                finally:
                    img.close()
                if page_text and page_text.strip():
                    pages_md.append(f"## Page {i + 1}\n\n{page_text.strip()}")
                    pages_ocrd += 1
            except Exception as page_exc:
                # Don't let one malformed page kill the whole doc.
                pages_failed += 1
                log.warning("page %d OCR failed: %s", i + 1, page_exc)
                continue
    finally:
        doc.close()

    md = "\n\n".join(pages_md) if pages_md else ""
    log.info("OCR done: pages_ocrd=%d failed=%d chars=%d", pages_ocrd, pages_failed, len(md))
    return {
        "pdf_key": pdf_key,
        "markdown": md,
        "pages_ocrd": pages_ocrd,
        "pages_failed": pages_failed,
        "char_count": len(md),
        "tier": "tesseract",
    }
