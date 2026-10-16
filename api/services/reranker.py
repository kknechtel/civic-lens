"""
Voyage rerank-2 wrapper.

Takes the top N results from hybrid search (semantic + keyword) and reorders
them with a cross-encoder reranker — much higher quality than the weighted
sum of cosine + BM25 because the reranker sees the query and doc together.

Cost: ~$0.05 per 1k queries at typical batch sizes (top_n=30, ~200-char
passages). The function returns the original list unchanged on any failure
so callers don't need to special-case the "no key" or "API down" paths.

Voyage rerank docs: https://docs.voyageai.com/docs/reranker
"""
import logging
import os

log = logging.getLogger(__name__)

VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
RERANK_MODEL = "rerank-2"
# rerank-2 supports up to 16k tokens per doc; we cap at ~3k chars (~750 tokens)
# per item so a top_n=30 call stays well under the 320k total-tokens cap.
MAX_DOC_CHARS = 3000


def rerank(query: str, documents: list[str], top_k: int | None = None) -> list[tuple[int, float]]:
    """Return [(original_index, score), ...] sorted by score desc.

    On any failure (no key, HTTP error, malformed response) returns a
    pass-through ranking using the input order so the caller can keep its
    original hybrid score without branching.
    """
    if not VOYAGE_API_KEY or not documents:
        return [(i, 0.0) for i in range(len(documents))]

    # Truncate aggressively — Voyage charges for full doc length and the
    # reranker doesn't need 100K of OCR text to judge relevance.
    payload_docs = [(d or "")[:MAX_DOC_CHARS] for d in documents]

    try:
        import requests
        resp = requests.post(
            "https://api.voyageai.com/v1/rerank",
            headers={
                "Authorization": f"Bearer {VOYAGE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": RERANK_MODEL,
                "query": query,
                "documents": payload_docs,
                "top_k": top_k or len(payload_docs),
                "return_documents": False,
            },
            timeout=15,
        )
        if not resp.ok:
            log.warning("Voyage rerank %d: %s", resp.status_code, resp.text[:200])
            return [(i, 0.0) for i in range(len(documents))]
        data = resp.json()

        # Record usage. Voyage rerank returns usage.total_tokens.
        try:
            total_tokens = int(((data.get("usage") or {}).get("total_tokens")) or 0)
            if total_tokens > 0:
                from database import SessionLocal
                from services.usage import record_usage
                sess = SessionLocal()
                try:
                    # rerank-2: $0.05/1M tokens (as of 2025-Q4 pricing). Keep
                    # in sync with embeddings.py if Voyage adjusts rates.
                    cost = (total_tokens * 0.05) / 1_000_000
                    record_usage(
                        sess, source="rerank", model=RERANK_MODEL,
                        input_tokens=total_tokens, output_tokens=0,
                        estimated_cost_usd=cost,
                        metadata={"batch_size": len(payload_docs)},
                    )
                finally:
                    sess.close()
        except Exception:
            log.debug("voyage rerank usage record skipped", exc_info=True)

        results = data.get("data", [])
        # Each entry: {"index": int, "relevance_score": float}
        return [
            (int(r["index"]), float(r["relevance_score"]))
            for r in results
        ]
    except Exception as exc:
        log.warning("Voyage rerank call failed: %s", exc)
        return [(i, 0.0) for i in range(len(documents))]


def is_available() -> bool:
    """Cheap check for callers that want to skip the network round-trip
    when reranking is disabled (e.g. during tests or local dev without a key)."""
    return bool(VOYAGE_API_KEY)
