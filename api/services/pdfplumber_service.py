"""
PdfPlumber Text Extraction Service

Tier 0 of the OCR cascade — fast, free, local extraction for PDFs that
already have a text layer. If the PDF is scanned/image-based, returns
is_text_based=False so the caller can fall through to OCR.

Adapted from bank-processor/api/services/pdfplumber_service.py.
"""
import io
import logging
import time
from dataclasses import dataclass
from typing import Optional

import pdfplumber

logger = logging.getLogger(__name__)

# If ANY sampled page has at least this many characters, the PDF is
# treated as text-based. Some municipal docs have blank cover pages or
# page-1 boilerplate, so we sample a few pages — not just the first.
MIN_CHARS_ANY_PAGE = 200


@dataclass
class PdfPlumberResult:
    success: bool
    markdown: str = ""
    page_count: int = 0
    is_text_based: bool = False
    processing_time_seconds: float = 0
    error: Optional[str] = None
    chars_extracted: int = 0
    tables_found: int = 0


def _format_table_as_markdown(table: list[list]) -> str:
    """Convert a pdfplumber table (list of rows) to a markdown table."""
    if not table or not table[0]:
        return ""
    rows = []
    for row in table:
        cleaned = []
        for cell in row:
            val = str(cell).strip() if cell is not None else ""
            cleaned.append(val.replace("\n", " "))
        rows.append(cleaned)

    col_count = max(len(r) for r in rows)
    lines = []
    for i, row in enumerate(rows):
        while len(row) < col_count:
            row.append("")
        lines.append("| " + " | ".join(row) + " |")
        if i == 0:
            lines.append("| " + " | ".join(["---"] * col_count) + " |")
    return "\n".join(lines)


def _extract_page_text(page) -> str:
    """Extract text from a page, splitting tables and prose so tables are
    rendered as markdown rather than collapsed into spaghetti text."""
    parts = []
    tables = page.find_tables()
    table_bboxes = [t.bbox for t in tables]

    if table_bboxes:
        filtered = page
        for bbox in table_bboxes:
            filtered = filtered.outside_bbox(bbox)
        non_table = filtered.extract_text() or ""
    else:
        non_table = page.extract_text() or ""

    if non_table.strip():
        parts.append(non_table.strip())

    for table in tables:
        extracted = table.extract()
        if extracted:
            md = _format_table_as_markdown(extracted)
            if md:
                parts.append(md)

    if not tables:
        raw = page.extract_text() or ""
        if raw.strip():
            return raw.strip()
    return "\n\n".join(parts)


def is_text_based_pdf(pdf_bytes: bytes) -> bool:
    """Quick check: does this PDF have any extractable text?"""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if not pdf.pages:
                return False
            sample_pages = min(3, len(pdf.pages))
            for i in range(sample_pages):
                text = pdf.pages[i].extract_text() or ""
                if len(text.strip()) >= MIN_CHARS_ANY_PAGE:
                    return True
            return False
    except Exception as exc:
        logger.debug("PDF text check failed: %s", exc)
        return False


def extract_text(pdf_bytes: bytes) -> PdfPlumberResult:
    """Extract markdown text from a text-based PDF.

    Returns is_text_based=False if no sampled page has substantial text;
    callers should fall back to OCR in that case.
    """
    start = time.time()
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page_count = len(pdf.pages)
            if page_count == 0:
                return PdfPlumberResult(
                    success=False,
                    error="PDF has no pages",
                    processing_time_seconds=time.time() - start,
                )

            # Probe first 3 pages
            sample_size = min(3, page_count)
            has_text = False
            for i in range(sample_size):
                text = pdf.pages[i].extract_text() or ""
                if len(text.strip()) >= MIN_CHARS_ANY_PAGE:
                    has_text = True
                    break
            if not has_text:
                return PdfPlumberResult(
                    success=False,
                    is_text_based=False,
                    page_count=page_count,
                    processing_time_seconds=time.time() - start,
                    error="PDF appears to be scanned/image-based",
                )

            all_pages = []
            total_tables = 0
            for i, page in enumerate(pdf.pages):
                page_text = _extract_page_text(page)
                if page_text:
                    all_pages.append(f"## Page {i + 1}\n\n{page_text}")
                total_tables += len(page.find_tables())

            markdown = "\n\n---\n\n".join(all_pages)
            elapsed = time.time() - start
            logger.info(
                "pdfplumber extracted %d pages, %d chars, %d tables in %.2fs",
                page_count, len(markdown), total_tables, elapsed,
            )
            return PdfPlumberResult(
                success=True,
                markdown=markdown,
                page_count=page_count,
                is_text_based=True,
                processing_time_seconds=elapsed,
                chars_extracted=len(markdown),
                tables_found=total_tables,
            )
    except Exception as exc:
        logger.error("pdfplumber extraction failed: %s", exc, exc_info=True)
        return PdfPlumberResult(
            success=False,
            error=str(exc),
            processing_time_seconds=time.time() - start,
        )
