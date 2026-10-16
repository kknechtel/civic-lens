"""
Document ingestion: takes Documents that have extracted_text and produces
DocumentChunk rows + populates the embedding/fts_vector columns on both
documents and chunks.

Idempotent: re-ingesting a document deletes its existing chunks first.
Safe to run from a script, an admin endpoint, or as a one-shot on startup.
"""
import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from models.document import Document
from models.document_chunk import DocumentChunk
from services.chunker import chunk_pages
from services.embeddings import (
    embed_documents_batch,
    embed_document,
    model_id,
    to_pgvector_literal,
)

log = logging.getLogger(__name__)


def _refresh_doc_fts(db: Session, doc_id) -> None:
    """Build the document-level fts_vector with positional weights:
      A (highest) — title
      B           — filename
      C           — notes (AI summary)
      D (lowest)  — first 50KB of extracted_text body

    ts_rank() honors these weights, so a title hit naturally beats a body
    mention without needing a separate score-blending pass.

    Slashes / backslashes / pipes are normalized to spaces before
    tokenizing — Postgres' English config treats them as part of a token
    (it's tuned for URLs), so "Winnerling/Moody" would stay one compound
    lexeme and never match a search for "winnerling" alone.
    """
    db.execute(text(r"""
        UPDATE documents
        SET fts_vector =
            setweight(to_tsvector('english', regexp_replace(coalesce(title, ''),    '[/\\|]', ' ', 'g')), 'A') ||
            setweight(to_tsvector('english', regexp_replace(coalesce(filename, ''), '[/\\|]', ' ', 'g')), 'B') ||
            setweight(to_tsvector('english', regexp_replace(coalesce(notes, ''),    '[/\\|]', ' ', 'g')), 'C') ||
            setweight(to_tsvector('english', regexp_replace(coalesce(left(extracted_text, 50000), ''),
                                                            '[/\\|]', ' ', 'g')), 'D')
        WHERE id = CAST(:id AS uuid)
    """), {"id": str(doc_id)})


def _refresh_chunk_fts(db: Session, chunk_ids: list) -> None:
    if not chunk_ids:
        return
    # Same slash normalization as the doc-level vector.
    db.execute(text(r"""
        UPDATE document_chunks
        SET fts_vector =
            setweight(to_tsvector('english', regexp_replace(coalesce(context, ''), '[/\\|]', ' ', 'g')), 'D') ||
            setweight(to_tsvector('english', regexp_replace(coalesce(content, ''), '[/\\|]', ' ', 'g')), 'B')
        WHERE id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": [str(i) for i in chunk_ids]})


class _EntityLabels(dict):
    """Entity key → display label, read from the municipality profile."""
    def __missing__(self, key):
        from municipality import get_municipality
        e = get_municipality().entity(key)
        return e.label if e else key

    def get(self, key, default=None):
        return self[key] if key else default


_MUNI_LABEL = _EntityLabels()


def chunk_header(doc: Document) -> str:
    """Deterministic one-line context for every chunk of a doc — no LLM."""
    meta = doc.metadata_ or {}
    parts = [
        doc.title or doc.filename,
        _MUNI_LABEL.get(doc.municipality or "", ""),
        meta.get("body") or doc.department or "",
        (doc.doc_type or "").replace("_", " "),
        doc.doc_date or (f"FY {doc.fiscal_year}" if doc.fiscal_year else ""),
    ]
    seen, out = set(), []
    for p in parts:
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return " · ".join(out)


def _with_page(header: str, start, end) -> str:
    if not start:
        return header
    return f"{header} · p.{start}" if start == end else f"{header} · pp.{start}-{end}"


def _set_doc_embedding(db: Session, doc_id, vec) -> None:
    if vec is None:
        return
    db.execute(text("""
        UPDATE documents
        SET embedding = CAST(:vec AS vector), embedding_model = :model
        WHERE id = CAST(:id AS uuid)
    """), {"id": str(doc_id), "vec": to_pgvector_literal(vec), "model": model_id()})


def _set_chunk_embeddings(db: Session, pairs: list[tuple]) -> None:
    """pairs: [(chunk_id, vec), ...]

    Single UPDATE ... FROM (VALUES ...) rather than one round-trip per row
    — a 100-chunk doc went from ~100 statements to 1.
    """
    pairs = [(cid, vec) for cid, vec in pairs if vec is not None]
    if not pairs:
        return
    values_sql = ", ".join(
        f"(CAST(:id{i} AS uuid), CAST(:vec{i} AS vector))" for i in range(len(pairs))
    )
    params: dict = {}
    for i, (cid, vec) in enumerate(pairs):
        params[f"id{i}"] = str(cid)
        params[f"vec{i}"] = to_pgvector_literal(vec)
    db.execute(text(f"""
        UPDATE document_chunks AS c
        SET embedding = v.vec, embedding_model = :model
        FROM (VALUES {values_sql}) AS v(id, vec)
        WHERE c.id = v.id
    """), {**params, "model": model_id()})


def ingest_document(db: Session, doc: Document, force: bool = False) -> dict:
    """Chunk + embed one document. Returns a small summary dict."""
    if not doc.extracted_text or len(doc.extracted_text) < 100:
        return {"document_id": str(doc.id), "skipped": True, "reason": "no_text"}

    # Delete existing chunks to keep this idempotent.
    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete()

    chunks = chunk_pages(doc.extracted_text)
    if not chunks:
        return {"document_id": str(doc.id), "skipped": True, "reason": "empty_after_chunking"}

    # Insert chunk rows first (without embeddings) so we have IDs.
    header = chunk_header(doc)
    rows = [
        DocumentChunk(
            document_id=doc.id,
            chunk_index=i,
            content=c.content,
            context=_with_page(header, c.page_start, c.page_end),
            page_start=c.page_start,
            page_end=c.page_end,
            token_count=len(c.content) // 4,
        )
        for i, c in enumerate(chunks)
    ]
    db.add_all(rows)
    db.flush()

    # Embed header + passage so "Highlands Council minutes" style queries
    # match passages that never repeat the body name. fts covers both too.
    vecs = embed_documents_batch([f"{r.context}\n{r.content}" for r in rows])
    _set_chunk_embeddings(db, [(r.id, v) for r, v in zip(rows, vecs)])
    _refresh_chunk_fts(db, [r.id for r in rows])

    # Document-level vector uses filename + notes + first 32K of body.
    doc_text = " ".join(filter(None, [doc.title or "", doc.filename, doc.notes or "",
                                      doc.extracted_text[:32000]]))
    _set_doc_embedding(db, doc.id, embed_document(doc_text))
    _refresh_doc_fts(db, doc.id)

    db.commit()
    return {"document_id": str(doc.id), "chunks": len(rows), "filename": doc.filename}


def ingest_all_pending(db: Session, limit: Optional[int] = None) -> dict:
    """Ingest all documents that don't have chunks yet (or have empty fts_vector)."""
    q = (
        db.query(Document)
        .outerjoin(DocumentChunk, DocumentChunk.document_id == Document.id)
        .filter(Document.extracted_text.isnot(None))
        .filter(DocumentChunk.id.is_(None))  # no chunks yet
    )
    if limit:
        q = q.limit(limit)

    docs = q.all()
    log.info("Ingesting %d documents", len(docs))
    summary = {"total": len(docs), "ingested": 0, "skipped": 0, "errors": 0, "details": []}
    for d in docs:
        try:
            res = ingest_document(db, d)
            if res.get("skipped"):
                summary["skipped"] += 1
            else:
                summary["ingested"] += 1
            summary["details"].append(res)
        except Exception as exc:
            log.exception("Failed to ingest %s: %s", d.filename, exc)
            db.rollback()
            summary["errors"] += 1
            summary["details"].append({"document_id": str(d.id), "error": str(exc)})
    return summary


def ingest_one(db: Session, document_id: str, force: bool = False) -> dict:
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        return {"error": "not_found"}
    return ingest_document(db, doc, force=force)


def reembed_missing(db: Session, limit: int = 2000) -> int:
    """Embed chunks with no vector, or a vector from a different model than the
    current provider (after a provider switch). Returns count fixed."""
    if model_id() is None:
        return 0
    rows = db.execute(text("""
        SELECT id, coalesce(context, '') || ' ' || content AS body
        FROM document_chunks
        WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM :model
        ORDER BY created_at DESC LIMIT :n
    """), {"n": limit, "model": model_id()}).fetchall()
    if not rows:
        return 0
    vecs = embed_documents_batch([r.body for r in rows])
    pairs = [(r.id, v) for r, v in zip(rows, vecs) if v is not None]
    _set_chunk_embeddings(db, pairs)
    db.commit()
    log.info("reembed_missing: %d/%d chunks embedded", len(pairs), len(rows))
    return len(pairs)
