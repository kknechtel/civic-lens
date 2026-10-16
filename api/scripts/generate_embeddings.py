"""
(Re-)embed chunks and documents with the configured provider (services.embeddings).

    python -m scripts.generate_embeddings              # anything missing or from another model
    python -m scripts.generate_embeddings --limit 5000

Safe to re-run; it only touches rows whose vector is NULL or was made by a
different model than the current one. Keyword-only deployments
(EMBEDDINGS_PROVIDER=none) have nothing to do.
"""
from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import text

log = logging.getLogger("generate_embeddings")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max chunks this run (0 = all)")
    ap.add_argument("--batch", type=int, default=1024)
    args = ap.parse_args()

    from database import SessionLocal
    from services.embeddings import embed_documents_batch, model_id
    from services.ingestion import _set_chunk_embeddings, _set_doc_embedding
    model = model_id()
    if not model:
        log.info("Embeddings provider is 'none' — search is keyword-only; nothing to embed.")
        return 0
    log.info("Embedding with %s", model)
    done, t0 = 0, time.time()
    with SessionLocal() as db:
        while not args.limit or done < args.limit:
            rows = db.execute(text("""
                SELECT id, coalesce(context, '') || E'\\n' || content AS body FROM document_chunks
                WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM :m
                ORDER BY id LIMIT :n"""), {"m": model, "n": args.batch}).fetchall()
            if not rows:
                break
            vecs = embed_documents_batch([r.body for r in rows], batch_size=len(rows))
            pairs = [(r.id, v) for r, v in zip(rows, vecs) if v is not None]
            if not pairs:
                log.error("provider returned no vectors; stopping")
                return 1
            _set_chunk_embeddings(db, pairs)
            db.commit()
            done += len(pairs)
            log.info("chunks: %d embedded (%.0f/s)", done, done / max(time.time() - t0, 1e-6))
        docs = db.execute(text("""
            SELECT id, concat_ws(' ', title, filename, notes, left(extracted_text, 32000)) AS body FROM documents
            WHERE extracted_text IS NOT NULL AND (embedding IS NULL OR embedding_model IS DISTINCT FROM :m)"""),
            {"m": model}).fetchall()
        for i in range(0, len(docs), 64):
            batch = docs[i:i + 64]
            for d, v in zip(batch, embed_documents_batch([d.body for d in batch], batch_size=64)):
                _set_doc_embedding(db, d.id, v)
            db.commit()
        log.info("documents: %d embedded. Done in %.0fs", len(docs), time.time() - t0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
