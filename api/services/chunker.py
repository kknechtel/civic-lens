"""
Document chunking. Splits extracted text into ~1500-char windows with
~200-char overlap on paragraph boundaries when possible, keeping track of
which "## Page N" sections each chunk came from. Cheap, sync, no LLM.

Smaller chunks than the old 4000-char windows make reranking and
citations land on the passage that actually answers the question.
"""
import re
from dataclasses import dataclass
from typing import Optional

TARGET_CHARS = 1500
OVERLAP_CHARS = 200
MIN_CHUNK_CHARS = 120    # discard tiny tail chunks

_PAGE_MARK = re.compile(r"^## Page (\d+)\s*$", re.M)
_PARA_SPLIT = re.compile(r"\n\s*\n")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_SKIP = re.compile(r"^(---|\[page \d+: unreadable\])$")


@dataclass
class Chunk:
    content: str
    page_start: Optional[int]
    page_end: Optional[int]


def _sections(text: str) -> list[tuple[Optional[int], str]]:
    """Split on "## Page N" markers → [(page, text)]. No markers → one section."""
    marks = list(_PAGE_MARK.finditer(text))
    if not marks:
        return [(None, text)]
    out = []
    if marks[0].start() > 0 and text[:marks[0].start()].strip():
        out.append((None, text[:marks[0].start()]))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((int(m.group(1)), text[m.end():end]))
    return out


def _paragraphs(text: str) -> list[str]:
    paras = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip() and not _SKIP.match(p.strip())]
    # Raw OCR often has no blank lines; fall back to sentences for long blocks.
    out: list[str] = []
    for p in paras:
        if len(p) > TARGET_CHARS:
            out.extend(s.strip() for s in _SENT_SPLIT.split(p) if s.strip())
        else:
            out.append(p)
    return out


def chunk_pages(text: str) -> list[Chunk]:
    if not text:
        return []
    text = text.replace("\x00", "")  # nul bytes break tsvector
    units: list[tuple[str, Optional[int]]] = [
        (p, page) for page, body in _sections(text) for p in _paragraphs(body)
    ]
    chunks: list[Chunk] = []
    buf: list[tuple[str, Optional[int]]] = []
    buf_len = 0

    def emit():
        pages = [pg for _, pg in buf if pg is not None]
        chunks.append(Chunk("\n\n".join(t for t, _ in buf).strip(),
                            min(pages) if pages else None, max(pages) if pages else None))

    for p, page in units:
        if len(p) > TARGET_CHARS:  # one unsplittable run of text
            if buf_len >= MIN_CHUNK_CHARS:
                emit()
            buf, buf_len = [], 0
            for i in range(0, len(p), TARGET_CHARS - OVERLAP_CHARS):
                chunks.append(Chunk(p[i:i + TARGET_CHARS], page, page))
            continue
        if buf_len + len(p) + 2 > TARGET_CHARS and buf_len >= MIN_CHUNK_CHARS:
            emit()
            tail_text, tail_page = buf[-1][0][-OVERLAP_CHARS:], buf[-1][1]
            buf, buf_len = [(tail_text, tail_page)], len(tail_text)
        buf.append((p, page))
        buf_len += len(p) + 2

    if buf_len >= MIN_CHUNK_CHARS or (buf and not chunks):
        emit()
    return chunks
