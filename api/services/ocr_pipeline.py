"""
PDF → markdown, page by page, at zero API cost.

For each page:
  1. PyMuPDF text layer (instant, free).
  2. If the page has almost no text, or its text layer is garbled
     (see services/text_quality.py), OCR just that page with Tesseract.

No paid Vision fallback: pages Tesseract can't read are marked
"[page N: unreadable]" so the gap is visible instead of silently empty.
Output uses "## Page N" headers, which chunking parses for page numbers.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from services.text_quality import text_is_readable

logger = logging.getLogger(__name__)

PROGRESS_CB = Callable[[int, int], Awaitable[None]]

# A page with fewer chars than this in its text layer is treated as scanned.
MIN_PAGE_CHARS = 100
# Cap on pages sent to Tesseract per document. Text-layer pages are uncapped.
MAX_OCR_PAGES = int(os.environ.get("MAX_OCR_PAGES", "60"))
# PDFs larger than this skip OCR entirely (text layer only) — almost always
# scanned plan sets or photo dumps that would monopolise the box.
MAX_OCR_PDF_BYTES = int(os.environ.get("MAX_OCR_PDF_MB", "150")) * 1024 * 1024


@dataclass
class OCRResult:
    success: bool
    markdown: str = ""
    page_count: int = 0
    tier: str = ""              # "text_layer" | "tesseract" | "mixed" | "none"
    processing_time_ms: float = 0
    estimated_cost: float = 0.0
    error: Optional[str] = None
    ocr_pages: int = 0
    unreadable_pages: list[int] = field(default_factory=list)
    truncated: bool = False     # some pages needed OCR beyond MAX_OCR_PAGES


def _extract_sync(pdf_bytes: bytes, filename: str) -> OCRResult:
    import fitz  # PyMuPDF

    start = time.time()
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page_count = len(doc)
        texts = [doc[i].get_text() for i in range(page_count)]

    needs_ocr = [i for i, t in enumerate(texts)
                 if len(t.strip()) < MIN_PAGE_CHARS or not text_is_readable(t)]
    truncated = len(needs_ocr) > MAX_OCR_PAGES
    to_ocr = needs_ocr[:MAX_OCR_PAGES]
    if to_ocr and len(pdf_bytes) > MAX_OCR_PDF_BYTES:
        logger.warning("[OCR:%s] %.0f MB PDF exceeds MAX_OCR_PDF_MB — text layer only",
                       filename, len(pdf_bytes) / 1e6)
        truncated, to_ocr = True, []

    ocr_text: dict[int, str] = {}
    if to_ocr:
        from services.tesseract_ocr import is_tesseract_available, ocr_pages
        if is_tesseract_available():
            ocr_text = ocr_pages(pdf_bytes, to_ocr)
        else:
            logger.warning("[OCR:%s] %d pages need OCR but tesseract is not installed",
                           filename, len(to_ocr))

    parts: list[str] = []
    unreadable: list[int] = []
    for i, layer in enumerate(texts):
        text = layer.strip()
        # Prefer OCR when it recovered more than the (readable) text layer;
        # a short but clean page (e.g. a cover) keeps its layer if OCR fails.
        if i in ocr_text and len(ocr_text[i].strip()) > len(text if text_is_readable(text) else ""):
            text = ocr_text[i].strip()
        elif i in needs_ocr and not text_is_readable(text):
            text = ""
        if not text:
            unreadable.append(i + 1)
            text = f"[page {i + 1}: unreadable]"
        parts.append(f"## Page {i + 1}\n\n{text}")

    readable_pages = page_count - len(unreadable)
    tier = ("none" if readable_pages == 0 else
            "text_layer" if not ocr_text else
            "tesseract" if len(ocr_text) == page_count else "mixed")
    elapsed = (time.time() - start) * 1000
    logger.info("[OCR:%s] %d pages, %d OCR'd, %d unreadable%s, %.0fms",
                filename, page_count, len(ocr_text), len(unreadable),
                " (truncated)" if truncated else "", elapsed)
    return OCRResult(
        success=readable_pages > 0,
        markdown="\n\n".join(parts) if readable_pages else "",
        page_count=page_count,
        tier=tier,
        processing_time_ms=elapsed,
        ocr_pages=len(ocr_text),
        unreadable_pages=unreadable,
        truncated=truncated,
        error=None if readable_pages else "no readable text",
    )


async def extract_pdf_to_markdown(
    pdf_bytes: bytes,
    filename: str = "",
    progress_callback: Optional[PROGRESS_CB] = None,
) -> OCRResult:
    """Extract a PDF to "## Page N" markdown using the text layer + Tesseract."""
    try:
        return await asyncio.to_thread(_extract_sync, pdf_bytes, filename)
    except Exception as exc:
        logger.error("[OCR:%s] extraction failed: %s", filename, exc, exc_info=True)
        return OCRResult(success=False, tier="none", error=str(exc))
