"""
Tesseract local OCR — fastest tier of the cascade.

Renders PDF pages to PIL images via PyMuPDF, OCRs them in parallel via
pytesseract. No network round-trips, no API costs, no rate limits.
Quality is "good enough" for keyword/semantic search on municipal docs;
not bank-statement-grade column alignment.

Requires the `tesseract` system binary (apt: tesseract-ocr).
"""
from __future__ import annotations

import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

# Cap most municipal docs — first N pages have the substantive content.
DEFAULT_MAX_PAGES = 30
# 200 DPI: noticeably better on small-print scans than 150, and we only
# OCR pages whose text layer is missing or garbled.
DEFAULT_DPI = 200

# ── Resource limits ─────────────────────────────────────────────────────────
# OCR used to render every page that needed OCR to PNG *before* starting, and
# process_pending ran 4 docs at once with 4 Tesseract threads each. A 120-page
# scan or one large-format site plan (36x48in at 200 DPI ≈ 200 megapixels)
# was enough to exhaust a 2 GB box and take the API down with it. Now:
#   - pages are rendered and OCR'd one at a time per worker (bounded memory);
#   - each render is capped at OCR_MAX_PIXELS (DPI is lowered for huge pages);
#   - a process-wide semaphore caps concurrent Tesseract runs across ALL docs;
#   - Tesseract runs single-threaded (OMP_THREAD_LIMIT=1) with a per-page timeout.
import os
import threading

DEFAULT_WORKERS = int(os.environ.get("TESSERACT_WORKERS", str(max(1, (os.cpu_count() or 2) - 1))))
OCR_MAX_PIXELS = int(os.environ.get("OCR_MAX_PIXELS", str(12_000_000)))  # ~ letter page at 400 DPI
OCR_PAGE_TIMEOUT = int(os.environ.get("OCR_PAGE_TIMEOUT", "90"))  # seconds per page
os.environ.setdefault("OMP_THREAD_LIMIT", "1")  # inherited by the tesseract subprocess

_OCR_SLOTS = threading.BoundedSemaphore(DEFAULT_WORKERS)


@dataclass
class TesseractResult:
    success: bool
    markdown: str = ""
    page_count: int = 0
    processing_time_seconds: float = 0
    error: Optional[str] = None
    estimated_cost: float = 0.0  # always 0 for Tesseract (free)
    pages_ocr_failed: int = 0


def is_tesseract_available() -> bool:
    """Check both the binary and the Python wrapper are present."""
    if not shutil.which("tesseract"):
        return False
    try:
        import pytesseract  # noqa: F401
        return True
    except ImportError:
        return False


def page_zoom(width_pt: float, height_pt: float, dpi: int, max_pixels: int = OCR_MAX_PIXELS) -> float:
    """Zoom factor for rendering: `dpi`, reduced so the bitmap stays under max_pixels."""
    zoom = dpi / 72
    pixels = width_pt * zoom * height_pt * zoom
    if pixels > max_pixels > 0:
        zoom *= (max_pixels / pixels) ** 0.5
    return zoom


def _render_and_ocr(pdf_bytes: bytes, page_num: int, dpi: int) -> tuple[int, str]:
    """Render ONE page and OCR it. Holds at most one bitmap in memory."""
    import fitz  # PyMuPDF
    import pytesseract
    from PIL import Image

    with _OCR_SLOTS:
        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                if page_num >= len(doc):
                    return page_num, ""
                page = doc[page_num]
                zoom = page_zoom(page.rect.width, page.rect.height, dpi)
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
                img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
                del pix
            # `--oem 1` = LSTM only (fast, accurate); `--psm 6` = uniform block of text
            text = pytesseract.image_to_string(img, config="--oem 1 --psm 6", timeout=OCR_PAGE_TIMEOUT)
            return page_num, text
        except RuntimeError as exc:  # pytesseract raises RuntimeError on timeout
            logger.warning("Tesseract page %d timed out/failed: %s", page_num + 1, exc)
            return page_num, ""
        except Exception as exc:
            logger.warning("Tesseract page %d failed: %s", page_num + 1, exc)
            return page_num, ""


def ocr_pages(
    pdf_bytes: bytes,
    indexes: List[int],
    *,
    dpi: int = DEFAULT_DPI,
    workers: int = DEFAULT_WORKERS,
) -> dict[int, str]:
    """OCR specific pages. Returns {page_index: text}; failed pages map to "".

    Concurrency is bounded twice: `workers` threads for this document, and the
    process-wide _OCR_SLOTS semaphore across every document being processed.
    """
    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="tesseract") as ex:
        futures = [ex.submit(_render_and_ocr, pdf_bytes, p, dpi) for p in indexes]
        for fut in as_completed(futures):
            page_num, text = fut.result()
            results[page_num] = text
    return results


def extract_from_pdf(
    pdf_bytes: bytes,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    dpi: int = DEFAULT_DPI,
    workers: int = DEFAULT_WORKERS,
) -> TesseractResult:
    """OCR the first max_pages pages of a PDF. Synchronous (caller can run via to_thread)."""
    if not is_tesseract_available():
        return TesseractResult(
            success=False,
            error="tesseract binary or pytesseract package not installed",
        )

    start = time.time()
    try:
        import fitz
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            n = min(max_pages, len(doc))
        results = ocr_pages(pdf_bytes, list(range(n)), dpi=dpi, workers=workers)
    except Exception as exc:
        logger.error("PDF render failed: %s", exc)
        return TesseractResult(success=False, error=f"render_failed: {exc}",
                               processing_time_seconds=time.time() - start)

    if not results:
        return TesseractResult(success=False, error="no_pages_rendered",
                               processing_time_seconds=time.time() - start)

    failed = sum(1 for t in results.values() if not t.strip())
    parts = [f"## Page {p + 1}\n\n{results[p].strip()}"
             for p in sorted(results) if results[p].strip()]
    markdown = "\n\n---\n\n".join(parts)
    elapsed = time.time() - start

    logger.info(
        "Tesseract: %d pages, %d chars, %d failed, %.2fs",
        len(results), len(markdown), failed, elapsed,
    )

    return TesseractResult(
        success=bool(markdown),
        markdown=markdown,
        page_count=len(results),
        processing_time_seconds=elapsed,
        pages_ocr_failed=failed,
    )
