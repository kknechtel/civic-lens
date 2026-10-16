"""
Hybrid search: pgvector semantic + IDF-weighted Postgres full-text keyword.

  - search_chunks(): passage-level retrieval (chat tools, /api/search).
  - search_documents(): document-level retrieval ("what exists on X").

Both fuse a semantic list and a keyword list with reciprocal rank fusion.
The semantic list only runs when a real embedding model is configured, and
only compares vectors made by that model (services.embeddings). Without one,
search is keyword-only. That is not a degraded mode: services.keyword_search
handles plain-English questions on its own.
"""
import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from services import keyword_search
from services.embeddings import embed_query, model_id, to_pgvector_literal

log = logging.getLogger(__name__)

# RRF constant (Cormack et al.). With k=60, 1/(k+1) is the top-rank term;
# RRF_SCALE maps "#1 in both lists" to 1.0 so downstream boosts keep their scale.
RRF_K = 60
RRF_SCALE = (RRF_K + 1) / 2

_CHUNK_COLS = """c.id AS chunk_id, c.document_id, c.content, c.context, c.chunk_index, c.page_start, c.page_end,
               d.filename, d.fiscal_year, d.category, d.doc_type, d.department, d.created_at, d.status,
               d.title, d.doc_date, d.notes, d.municipality"""
_DOC_COLS = """d.id, d.filename, d.title, d.doc_date, d.municipality, d.doc_type, d.category,
               d.fiscal_year, d.notes, d.page_count"""


def _has_vector_column(db: Session, table: str) -> bool:
    """Detect whether the embedding column was successfully added on startup."""
    return bool(db.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = 'embedding'"
    ), {"t": table}).fetchone())


def _query_vector(db: Session, table: str, embed_text: str) -> Optional[str]:
    """pgvector literal for the query, or None when semantic search can't run."""
    if model_id() is None or not _has_vector_column(db, table):
        return None
    try:
        vec = embed_query(embed_text)
    except Exception as exc:
        log.warning("query embedding failed; keyword-only: %s", exc)
        return None
    return to_pgvector_literal(vec) if vec else None


_CHUNK_FILTERS = """
              AND (:fiscal_year IS NULL OR d.fiscal_year = :fiscal_year)
              AND (:category IS NULL OR d.category = :category)
              AND (:doc_type IS NULL OR d.doc_type = :doc_type)
              AND (:document_id IS NULL OR c.document_id = CAST(:document_id AS uuid))
              AND (:project_id IS NULL OR d.project_id = CAST(:project_id AS uuid))
              AND (:department IS NULL OR lower(d.department) = lower(:department))
              AND (:municipality IS NULL OR d.municipality = :municipality)"""


_DOC_FILTERS = """
              AND (:fiscal_year IS NULL OR d.fiscal_year = :fiscal_year)
              AND (:category IS NULL OR d.category = :category)
              AND lower(d.filename) NOT LIKE '%.xlsx'
              AND lower(d.filename) NOT LIKE '%.xls'
              AND lower(d.filename) NOT LIKE '%.csv'"""


def _fuse_sql(semantic_sql: Optional[str], keyword_sql: Optional[str], id_col: str) -> str:
    """RRF over whichever of the two ranked lists exist."""
    if semantic_sql and keyword_sql:
        return f"""
        semantic AS ({semantic_sql}),
        keyword AS ({keyword_sql}),
        combined AS (
            SELECT COALESCE(s.{id_col}, k.{id_col}) AS {id_col},
                   (COALESCE(:sem_w / (:rrf_k + s.rnk), 0) + COALESCE(:kw_w / (:rrf_k + k.rnk), 0)) * :rrf_scale AS score
            FROM semantic s FULL OUTER JOIN keyword k ON s.{id_col} = k.{id_col}
        )"""
    only = semantic_sql or keyword_sql
    return f"""
        ranked AS ({only}),
        combined AS (SELECT {id_col}, (1.0 / (:rrf_k + rnk)) * :rrf_scale AS score FROM ranked)"""


# ── Chunk-level search ──────────────────────────────────────────────────

def search_chunks(
    db: Session,
    query: str,
    top_k: int = 12,
    semantic_weight: float = 1.0,
    keyword_weight: float = 1.0,
    fiscal_year: Optional[str] = None,
    category: Optional[str] = None,
    doc_type: Optional[str] = None,
    document_id: Optional[str] = None,
    project_id: Optional[str] = None,
    department: Optional[str] = None,
    use_websearch: bool = False,  # kept for callers; operators are detected automatically
    municipality: Optional[str] = None,
    embed_text: Optional[str] = None,
) -> list[dict]:
    """Up to top_k chunks, best first. `query` may carry synonym expansions
    ("a b OR c"); `embed_text` is the user's own question (embedded for the
    semantic side, and given full weight on the keyword side)."""
    import os as _os
    semantic_weight = float(_os.getenv("SEARCH_SEMANTIC_WEIGHT", semantic_weight))
    kq = keyword_search.build(db, query, primary=embed_text)
    vec = _query_vector(db, "document_chunks", embed_text or query) if semantic_weight > 0 else None
    if kq.empty and vec is None:
        return []
    filters = _CHUNK_FILTERS
    sem = kw = None
    if vec is not None:
        sem = f"""
            SELECT c.id, ROW_NUMBER() OVER (ORDER BY c.embedding <=> CAST(:embedding AS vector)) AS rnk
            FROM document_chunks c JOIN documents d ON d.id = c.document_id
            WHERE c.embedding IS NOT NULL AND c.embedding_model = :emb_model {filters}
            ORDER BY c.embedding <=> CAST(:embedding AS vector)
            LIMIT :pool"""
    if not kq.empty:
        score = kq.score("c.fts_vector")
        kw = f"""
            SELECT c.id, ROW_NUMBER() OVER (ORDER BY {score} DESC) AS rnk
            FROM document_chunks c JOIN documents d ON d.id = c.document_id
            WHERE {kq.where("c.fts_vector")} {filters}
            ORDER BY {score} DESC
            LIMIT :pool"""
    sql = text(f"""
        WITH {_fuse_sql(sem, kw, "id")}
        SELECT {_CHUNK_COLS}, x.score
        FROM combined x
        JOIN document_chunks c ON c.id = x.id
        JOIN documents d ON d.id = c.document_id
        ORDER BY x.score DESC
        LIMIT :top_k
    """)
    params = {
        "embedding": vec, "emb_model": model_id(),
        "fiscal_year": fiscal_year, "category": category, "doc_type": doc_type,
        "document_id": str(document_id) if document_id else None,
        "project_id": str(project_id) if project_id else None,
        "department": department, "municipality": municipality,
        "pool": max(top_k * 4, 40), "top_k": top_k,
        "sem_w": semantic_weight, "kw_w": keyword_weight, "rrf_k": RRF_K, "rrf_scale": RRF_SCALE,
        **kq.params(),
    }
    try:
        if vec is not None:
            # Wider HNSW candidate list: filters (municipality, doc_type…) are
            # applied after the index scan and would otherwise starve the pool.
            db.execute(text("SET LOCAL hnsw.ef_search = 200"))
        rows = db.execute(sql, params).fetchall()
    except Exception as exc:
        if vec is None:
            raise
        log.warning("hybrid chunk search failed, retrying keyword-only: %s", exc)
        db.rollback()
        return search_chunks(db, query, top_k, 0, keyword_weight, fiscal_year, category, doc_type,
                             document_id, project_id, department, use_websearch, municipality,
                             embed_text="") if not kq.empty else []
    return [dict(r._mapping) for r in rows]


# ── Document-level search (summaries + metadata) ───────────────────────

def search_documents(
    db: Session,
    query: str,
    top_k: int = 10,
    fiscal_year: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict]:
    """Ranked documents with their summaries — for "what documents exist on X"."""
    kq = keyword_search.build(db, query)
    vec = _query_vector(db, "documents", query)
    if kq.empty and vec is None:
        return []
    sem = kw = None
    if vec is not None:
        sem = f"""
            SELECT d.id, ROW_NUMBER() OVER (ORDER BY d.embedding <=> CAST(:embedding AS vector)) AS rnk
            FROM documents d
            WHERE d.embedding IS NOT NULL AND d.embedding_model = :emb_model {_DOC_FILTERS}
            ORDER BY d.embedding <=> CAST(:embedding AS vector)
            LIMIT :pool"""
    if not kq.empty:
        score = kq.score("d.fts_vector")
        kw = f"""
            SELECT d.id, ROW_NUMBER() OVER (ORDER BY {score} DESC) AS rnk
            FROM documents d
            WHERE {kq.where("d.fts_vector")} {_DOC_FILTERS}
            ORDER BY {score} DESC
            LIMIT :pool"""
    sql = text(f"""
        WITH {_fuse_sql(sem, kw, "id")}
        SELECT {_DOC_COLS}, x.score
        FROM combined x JOIN documents d ON d.id = x.id
        ORDER BY x.score DESC
        LIMIT :top_k
    """)
    params = {"embedding": vec, "emb_model": model_id(), "fiscal_year": fiscal_year, "category": category,
              "pool": max(top_k * 4, 40), "top_k": top_k, "sem_w": 1.0, "kw_w": 1.0,
              "rrf_k": RRF_K, "rrf_scale": RRF_SCALE, **kq.params()}
    if vec is not None:
        db.execute(text("SET LOCAL hnsw.ef_search = 200"))
    return [dict(r._mapping) for r in db.execute(sql, params).fetchall()]


# ── Direct lookups used by chat tools ──────────────────────────────────

def get_document_excerpt(db: Session, document_id: str, max_chars: int = 12000) -> Optional[dict]:
    """Fetch a document's metadata + first N chars of extracted text. Used by
    the `read_document` tool when Claude wants to actually look at a file."""
    row = db.execute(text("""
        SELECT id, filename, doc_type, category, fiscal_year, notes, page_count,
               substring(extracted_text from 1 for :n) AS excerpt
        FROM documents WHERE id = CAST(:id AS uuid)
    """), {"id": str(document_id), "n": max_chars}).fetchone()
    return dict(row._mapping) if row else None


def list_recent_documents(db: Session, doc_type: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Surface recent documents for browsing-style queries ('what's the latest budget?')."""
    sql = """
        SELECT id, filename, doc_type, category, fiscal_year, notes,
               page_count, created_at
        FROM documents
        WHERE lower(filename) NOT LIKE '%.xlsx'
          AND lower(filename) NOT LIKE '%.csv'
    """
    params: dict = {"limit": limit}
    if doc_type:
        sql += " AND doc_type = :doc_type"
        params["doc_type"] = doc_type
    sql += " ORDER BY fiscal_year DESC NULLS LAST, created_at DESC LIMIT :limit"
    rows = db.execute(text(sql), params).fetchall()
    return [dict(r._mapping) for r in rows]
