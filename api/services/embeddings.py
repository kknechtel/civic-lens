"""
Embeddings for hybrid search, behind one provider switch.

EMBEDDINGS_PROVIDER = auto | voyage | openai | gemini | local | none
  auto  → voyage if a Voyage key is configured, else local if fastembed is
          installed, else none.
  local → BAAI/bge-small-en-v1.5 via fastembed (ONNX, CPU, no key, ~200
          chunks/s on 4 cores). Its 384-dim vectors are zero-padded into the
          vector(1024) column; padding preserves cosine similarity exactly.
  none  → no vectors at all. Search runs keyword-only.

Every stored vector is tagged with `model_id()` (document_chunks.embedding_model).
Semantic search compares only vectors from the current model, so switching
providers never mixes incomparable spaces: re-embed with
`python -m scripts.generate_embeddings --model-changed`.

There is no non-semantic fallback. (An earlier SHA-512 "hash vector" fallback
overflowed to all-zero vectors and ranked by NaN.)
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

log = logging.getLogger(__name__)

EMBEDDING_DIM = 1024  # matches the vector(1024) columns
_LOCAL_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

_MODELS = {
    "voyage": "voyage-3",
    "openai": "text-embedding-3-small",
    "gemini": "gemini-embedding-001",
    "local": f"local:{_LOCAL_MODEL}",
}


def _key(provider: str) -> str:
    """Deployment-level key (the corpus is shared, so the deployment pays)."""
    try:
        from services.ai import deployment_key
        return deployment_key(provider) or ""
    except Exception:
        env = {"voyage": "VOYAGE_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}.get(provider)
        return os.getenv(env, "") if env else ""


def _local_available() -> bool:
    try:
        import fastembed  # noqa: F401
        return True
    except ImportError:
        return False


def provider() -> str:
    p = os.getenv("EMBEDDINGS_PROVIDER", "auto").lower()
    if p != "auto":
        return p
    if _key("voyage"):
        return "voyage"
    if _local_available():
        return "local"
    return "none"


def model_id() -> Optional[str]:
    """Tag stored with every vector; None when search is keyword-only."""
    p = provider()
    return _MODELS.get(p)


def is_semantic() -> bool:
    return model_id() is not None


# ── providers ────────────────────────────────────────────────────────────────

def _pad(vec) -> list[float]:
    v = [float(x) for x in vec]
    if len(v) > EMBEDDING_DIM:
        raise ValueError(f"embedding has {len(v)} dims; column holds {EMBEDDING_DIM}")
    return v + [0.0] * (EMBEDDING_DIM - len(v))


def _voyage(texts: list[str], input_type: str) -> list[list[float]]:
    import requests
    r = requests.post("https://api.voyageai.com/v1/embeddings",
                      headers={"Authorization": f"Bearer {_key('voyage')}", "Content-Type": "application/json"},
                      json={"input": texts, "model": _MODELS["voyage"], "input_type": input_type}, timeout=60)
    r.raise_for_status()
    return [d["embedding"] for d in r.json()["data"]]


def _openai(texts: list[str], input_type: str) -> list[list[float]]:
    import requests
    r = requests.post("https://api.openai.com/v1/embeddings",
                      headers={"Authorization": f"Bearer {_key('openai')}", "Content-Type": "application/json"},
                      json={"input": texts, "model": _MODELS["openai"], "dimensions": EMBEDDING_DIM}, timeout=60)
    r.raise_for_status()
    return [d["embedding"] for d in r.json()["data"]]


def _gemini(texts: list[str], input_type: str) -> list[list[float]]:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=_key("gemini"))
    task = "RETRIEVAL_QUERY" if input_type == "query" else "RETRIEVAL_DOCUMENT"
    res = client.models.embed_content(model=_MODELS["gemini"], contents=texts,
                                      config=types.EmbedContentConfig(task_type=task, output_dimensionality=EMBEDDING_DIM))
    return [e.values for e in res.embeddings]


@lru_cache(maxsize=1)
def _local_model():
    from fastembed import TextEmbedding
    return TextEmbedding(_LOCAL_MODEL)


def _local(texts: list[str], input_type: str) -> list[list[float]]:
    m = _local_model()
    if input_type == "query":
        vecs = m.query_embed(texts)
    else:
        # parallel=0 → one worker per core; worth it only for big batches.
        par = int(os.getenv("LOCAL_EMBED_PARALLEL", "0")) if len(texts) >= 256 else None
        vecs = m.embed(texts, batch_size=32, parallel=par)
    return [v.tolist() for v in vecs]


_IMPL = {"voyage": _voyage, "openai": _openai, "gemini": _gemini, "local": _local}


def _embed(texts: list[str], input_type: str) -> list[list[float]]:
    return [_pad(v) for v in _IMPL[provider()](texts, input_type)]


# ── public API ───────────────────────────────────────────────────────────────

def embed_document(text: str) -> Optional[list[float]]:
    """None when keyword-only, or when the provider failed (left NULL for retry)."""
    if not is_semantic():
        return None
    try:
        return _embed([text[:32000]], "document")[0]
    except Exception as exc:
        log.warning("embedding failed; leaving NULL for retry: %s", exc)
        return None


def embed_documents_batch(texts: list[str], batch_size: int = 32) -> list[Optional[list[float]]]:
    if not is_semantic():
        return [None] * len(texts)
    out: list[Optional[list[float]]] = []
    for i in range(0, len(texts), batch_size):
        batch = [t[:32000] for t in texts[i:i + batch_size]]
        try:
            out.extend(_embed(batch, "document"))
        except Exception as exc:
            log.warning("batch embedding failed at offset %d; leaving NULL: %s", i, exc)
            out.extend([None] * len(batch))
    return out


def embed_query(query: str) -> Optional[list[float]]:
    """None when keyword-only. Raises if the provider fails (callers drop to keyword)."""
    if not is_semantic():
        return None
    return _embed([query], "query")[0]


def to_pgvector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
