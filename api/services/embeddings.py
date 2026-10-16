"""
Embedding generation for hybrid RAG.

Order of preference:
  1. Voyage AI voyage-3-lite (1024 dims, $0.02/1M tokens) if VOYAGE_API_KEY set
  2. Deterministic SHA-512-derived hash vector (not semantic, but keeps the
     vector columns populated so cosine distance is well-defined)

The hash fallback exists so the system stays functional without a Voyage
account — keyword search still does most of the lifting via tsvector. Once
a real embedding key is wired up, re-run the ingestion script to overwrite
hash vectors with semantic ones.
"""
import hashlib
from typing import Optional
import logging
import os

import numpy as np

log = logging.getLogger(__name__)

VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
# voyage-3 is 1024-dim (matches database.py:EMBEDDING_DIM and the
# vector(1024) columns). voyage-3-lite is 512-dim — DO NOT use without
# also changing the column type, or every UPDATE rejects with
# "expected 1024 dimensions, not 512".
EMBEDDING_MODEL = "voyage-3"
EMBEDDING_DIM = 1024


def _voyage_embed(texts: list[str], input_type: str = "document") -> list[list[float]]:
    import requests
    if not VOYAGE_API_KEY:
        raise RuntimeError("VOYAGE_API_KEY not configured")
    resp = requests.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {VOYAGE_API_KEY}", "Content-Type": "application/json"},
        json={"model": EMBEDDING_MODEL, "input": texts, "input_type": input_type},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Voyage API error {resp.status_code}: {resp.text[:200]}")
    data = resp.json()

    # Record usage on a fresh short-lived session. Voyage returns
    # usage.total_tokens — stash that as input_tokens (embeddings have no
    # output side). Safe to skip silently if SessionLocal can't bind.
    try:
        total_tokens = int(((data.get("usage") or {}).get("total_tokens")) or 0)
        if total_tokens > 0:
            from database import SessionLocal
            from services.usage import record_usage
            sess = SessionLocal()
            try:
                # voyage-3 is $0.06/1M; voyage-3-lite is $0.02/1M. Use a
                # tighter rate than the catch-all in usage.py for accuracy.
                rate = 0.06 if EMBEDDING_MODEL == "voyage-3" else 0.02
                cost = (total_tokens * rate) / 1_000_000
                record_usage(
                    sess, source="embeddings", model=EMBEDDING_MODEL,
                    input_tokens=total_tokens, output_tokens=0,
                    estimated_cost_usd=cost,
                    metadata={"input_type": input_type, "batch_size": len(texts)},
                )
            finally:
                sess.close()
    except Exception:
        log.debug("voyage usage record skipped", exc_info=True)

    return [d["embedding"] for d in data["data"]]


def _hash_embed(text: str) -> list[float]:
    """SHA-512-derived unit vector. Not semantic — placeholder so the vector
    columns aren't NULL and cosine math doesn't degenerate."""
    target_bytes = EMBEDDING_DIM * 4  # float32
    raw = b""
    seed = text[:5000].encode()
    counter = 0
    while len(raw) < target_bytes:
        raw += hashlib.sha512(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
    arr = np.frombuffer(raw[:target_bytes], dtype=np.float32).copy()
    arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr.tolist()


# With a Voyage key configured, a failed call returns None (stored as NULL and
# retried by ingestion.reembed_missing) rather than a hash vector that would
# silently poison semantic ranking. The hash fallback is only for keyless dev.

def embed_document(text: str) -> Optional[list[float]]:
    truncated = text[:32000]
    if VOYAGE_API_KEY:
        try:
            return _voyage_embed([truncated], input_type="document")[0]
        except Exception as exc:
            log.warning("Voyage embed failed; leaving NULL for retry: %s", exc)
            return None
    return _hash_embed(truncated)


def embed_documents_batch(texts: list[str], batch_size: int = 32) -> list[Optional[list[float]]]:
    out: list[Optional[list[float]]] = []
    for i in range(0, len(texts), batch_size):
        batch = [t[:32000] for t in texts[i:i + batch_size]]
        if VOYAGE_API_KEY:
            try:
                out.extend(_voyage_embed(batch, input_type="document"))
            except Exception as exc:
                log.warning("Voyage batch embed failed at offset %d; leaving NULL: %s", i, exc)
                out.extend([None] * len(batch))
            continue
        out.extend(_hash_embed(t) for t in batch)
    return out


def embed_query(query: str) -> list[float]:
    """Raises when Voyage is configured but fails — callers fall back to keyword search."""
    if VOYAGE_API_KEY:
        return _voyage_embed([query], input_type="query")[0]
    return _hash_embed(query)


def to_pgvector_literal(vec: list[float]) -> str:
    """Format a Python float list as the string literal pgvector expects."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
