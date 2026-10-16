"""
Atlantic Highlands - Database setup
PostgreSQL with SQLAlchemy ORM. Inline migrations run on startup.
"""
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from config import DATABASE_URL

logger = logging.getLogger(__name__)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

EMBEDDING_DIM = 1024  # voyage-3-lite / hash fallback


def get_db():
    """Dependency for FastAPI routes to get a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables and run inline migrations."""
    import models  # noqa: F401 — registers every table on Base.metadata (scripts may not have imported routes)
    Base.metadata.create_all(bind=engine)
    _migrate()
    logger.info("Database initialized")
    _log_rag_health_banner()


def rag_health() -> dict:
    """
    Inspect the live database and return a status dict for the RAG pipeline.

    Used by:
      - the startup banner (so an operator notices a degraded mode)
      - GET /health/rag (so a uptime check can alert)
      - tests / diagnostic scripts

    Three independent axes:
      1. pgvector extension installed              → semantic search possible
      2. embedding columns exist on docs + chunks  → ingestion can write vectors
      3. VOYAGE_API_KEY set                        → vectors will be SEMANTIC
                                                     (otherwise hash-vector fallback)
    """
    import os

    status = {
        "pgvector_extension": False,
        "pgvector_version": None,
        "documents_embedding_column": False,
        "chunks_embedding_column": False,
        "voyage_api_key_set": bool(os.environ.get("VOYAGE_API_KEY", "").strip()),
        "ready_for_semantic_search": False,
        "degraded_mode": None,  # None | "no_pgvector" | "no_voyage_key"
        "documents_total": 0,
        "documents_embedded": 0,
        "chunks_total": 0,
        "chunks_embedded": 0,
    }

    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            )).fetchone()
            if row:
                status["pgvector_extension"] = True
                status["pgvector_version"] = row[0]

            # Embedding columns
            for table in ("documents", "document_chunks"):
                exists = conn.execute(text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = 'embedding'"
                ), {"t": table}).fetchone()
                key = "documents_embedding_column" if table == "documents" else "chunks_embedding_column"
                status[key] = bool(exists)

            # Coverage (only meaningful if embedding column exists)
            if status["documents_embedding_column"]:
                status["documents_total"] = conn.execute(text("SELECT count(*) FROM documents")).scalar() or 0
                status["documents_embedded"] = conn.execute(text(
                    "SELECT count(*) FROM documents WHERE embedding IS NOT NULL"
                )).scalar() or 0
            if status["chunks_embedding_column"]:
                status["chunks_total"] = conn.execute(text("SELECT count(*) FROM document_chunks")).scalar() or 0
                status["chunks_embedded"] = conn.execute(text(
                    "SELECT count(*) FROM document_chunks WHERE embedding IS NOT NULL"
                )).scalar() or 0
    except Exception as exc:
        logger.warning("rag_health: query failed: %s", exc)

    has_pgvector = (
        status["pgvector_extension"]
        and status["documents_embedding_column"]
        and status["chunks_embedding_column"]
    )

    if not has_pgvector:
        status["degraded_mode"] = "no_pgvector"
    elif not status["voyage_api_key_set"]:
        status["degraded_mode"] = "no_voyage_key"

    status["ready_for_semantic_search"] = has_pgvector and status["voyage_api_key_set"]
    return status


def _log_rag_health_banner() -> None:
    """Print a hard-to-miss multi-line banner about the RAG pipeline state.
    Helps operators notice when the system is silently running in a degraded
    mode (keyword-only search, hash-vector fallback)."""
    s = rag_health()
    rule = "=" * 72

    if s["ready_for_semantic_search"]:
        logger.info("\n%s\nRAG: READY (semantic + keyword)\n  pgvector v%s · voyage embeddings · %d/%d chunks embedded\n%s",
                    rule, s["pgvector_version"], s["chunks_embedded"], s["chunks_total"], rule)
        return

    lines = [rule, "RAG PIPELINE: DEGRADED MODE"]

    if s["degraded_mode"] == "no_pgvector":
        lines += [
            "",
            "  pgvector extension is NOT available on this Postgres instance.",
            "  Semantic search is OFF. Chat will fall back to tsvector keyword search.",
            "",
            f"  pgvector_extension:        {s['pgvector_extension']}",
            f"  documents.embedding col:   {s['documents_embedding_column']}",
            f"  document_chunks.embedding: {s['chunks_embedding_column']}",
            "",
            "  Fix:",
            "    RDS:        parameter group → shared_preload_libraries += pgvector → reboot",
            "    Docker:     image must be `pgvector/pgvector:pgN`, not stock postgres",
            "    Self-host:  apt-get install postgresql-{16,15,...}-pgvector",
            "",
            "  Then restart the API — the inline migration will create the extension",
            "  and add the embedding columns. Re-run ingestion to populate vectors.",
        ]
    elif s["degraded_mode"] == "no_voyage_key":
        lines += [
            "",
            "  pgvector is installed, but VOYAGE_API_KEY is not set.",
            "  Embeddings will use the SHA-512 hash fallback (deterministic, NOT semantic).",
            "  Keyword search via tsvector still works; chat recall will be okay but not great.",
            "",
            f"  pgvector v{s['pgvector_version']} · {s['chunks_embedded']}/{s['chunks_total']} chunks embedded (hash)",
            "",
            "  Fix:",
            "    Set VOYAGE_API_KEY (free tier covers AH's corpus). Get a key at",
            "      https://dash.voyageai.com",
            "    Restart the API and re-run ingestion to overwrite hash vectors with",
            "    real embeddings.",
        ]
    else:
        lines.append("  Unknown state — see status: %s" % s)

    lines.append(rule)
    logger.warning("\n%s", "\n".join(lines))


def _migrate():
    """
    Inline migrations. Adds columns/indexes/extensions create_all won't add
    to existing tables. Idempotent — safe to run on every startup.
    """
    new_columns = [
        ("users", "must_change_password", "BOOLEAN DEFAULT FALSE"),
        # Document titles — added 2026-05-19. Populated by
        # services.title_extractor (also runnable as a one-shot via
        # POST /api/documents/backfill-titles). The filename column is the
        # canonical identifier; title is the display string.
        ("documents", "title", "VARCHAR"),
        ("documents", "doc_date", "VARCHAR"),
        # atlantic_highlands | highlands | hhrsd | regional | state — seeded
        # from metadata.source_site below, refined by services.doc_enrich.
        ("documents", "municipality", "VARCHAR"),
        ("document_chunks", "context", "TEXT"),
        # Financial statement upgrades (Phase 1)
        ("financial_statements", "extraction_pass", "INTEGER DEFAULT 0"),
        ("financial_statements", "reconcile_status", "VARCHAR DEFAULT 'not_attempted'"),
        ("financial_statements", "reconcile_details", "JSONB DEFAULT '{}'::jsonb"),
        ("financial_statements", "anomaly_flags", "JSONB DEFAULT '[]'::jsonb"),
        ("financial_statements", "drill_results", "JSONB DEFAULT '{}'::jsonb"),
        ("financial_statements", "accounting_basis", "VARCHAR"),
        ("financial_statements", "fiscal_calendar", "VARCHAR"),
        ("financial_statements", "predecessor_entity", "VARCHAR"),
        # Financial line item upgrades — fund + NJ account codes
        ("financial_line_items", "fund", "VARCHAR"),
        ("financial_line_items", "account_code", "VARCHAR"),
        ("financial_line_items", "program_code", "VARCHAR"),
        ("financial_line_items", "function_code", "VARCHAR"),
        ("financial_line_items", "object_code", "VARCHAR"),
        ("financial_line_items", "is_total_row", "BOOLEAN DEFAULT FALSE"),
        ("financial_line_items", "yoy_change_pct", "FLOAT"),
        ("financial_line_items", "variance_pct", "FLOAT"),
        ("financial_line_items", "anomaly_flags", "JSONB DEFAULT '[]'::jsonb"),
        # Presentations: disclosure modal config (added with versions feature)
        ("presentations", "disclosure", "JSONB"),
        # Comment threads: inline-range anchor for highlight-comment
        ("presentation_comments", "anchor", "JSONB"),
        ("presentation_comments", "resolved_at", "TIMESTAMP"),
        # RSVP status: tentative / going / follow_up. Pre-existing rows
        # are backfilled to 'going' (the historical default).
        ("event_rsvps", "status", "VARCHAR(16) NOT NULL DEFAULT 'going'"),
        ("event_rsvps", "updated_at", "TIMESTAMP NOT NULL DEFAULT NOW()"),
    ]

    with engine.connect() as conn:
        def _table_exists(t: str) -> bool:
            return bool(conn.execute(text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :t"
            ), {"t": t}).fetchone())

        # 1. Add per-row missing columns. Skip silently if the table doesn't
        # exist yet (can happen mid-deploy if models haven't been imported).
        for table, column, col_type in new_columns:
            if not _table_exists(table):
                continue
            exists = conn.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ), {"table": table, "column": column}).fetchone()
            if not exists:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                logger.info(f"Added column {table}.{column}")

        # 1b. Seed documents.municipality for rows that predate the column:
        # source id → its entity (from the municipality profile), else the
        # first school entity for school-category rows, else the primary
        # entity. No-op once set. Savepoint so a failure here can't abort the
        # rest of the migration.
        if _table_exists("documents"):
            try:
                from municipality import get_municipality
                muni = get_municipality()
                school = next((e.key for e in muni.entities if e.kind == "school"), muni.primary_entity.key)
                params = {"school": school, "primary": muni.primary_entity.key}
                whens = []
                for i, s in enumerate(muni.sources):
                    whens.append(f"WHEN metadata->>'source_site' = :src{i} THEN :ent{i}")
                    params[f"src{i}"], params[f"ent{i}"] = s.id, s.entity
                with conn.begin_nested():
                    conn.execute(text(f"""
                        UPDATE documents SET municipality = CASE
                            {' '.join(whens)}
                            WHEN category = 'school' THEN :school
                            ELSE :primary
                        END
                        WHERE municipality IS NULL
                    """), params)
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_documents_municipality ON documents (municipality)"))
            except Exception as e:
                logger.warning(f"municipality seed skipped: {e}")

        # 2. Enable pgvector + tsvector for hybrid RAG
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception as e:
            logger.warning(f"pgvector extension not available — semantic search will fall back to keyword-only: {e}")

        # 2b. pg_trgm powers the "did you mean" feature — similarity() against
        # the filename corpus turns "highander" into a suggestion for "highlander".
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        except Exception as e:
            logger.warning(f"pg_trgm extension not available — typo suggestions disabled: {e}")

        # 3. Add embedding + fts_vector columns + chunk-level pgvector cols
        rag_columns = [
            ("documents", "embedding", f"vector({EMBEDDING_DIM})"),
            ("documents", "fts_vector", "tsvector"),
            ("document_chunks", "embedding", f"vector({EMBEDDING_DIM})"),
            ("document_chunks", "fts_vector", "tsvector"),
            # Live-music venue scrapers populate these (Squarespace /
            # Tribe-Events adapters). On existing prod tables they're
            # added here; on fresh installs the CREATE TABLE in
            # scrape_events.save_to_db includes them.
            ("calendar_events", "venue", "TEXT"),
            ("calendar_events", "city", "TEXT"),
            ("calendar_events", "event_type", "TEXT DEFAULT 'general'"),
            ("calendar_events", "end_time", "TEXT"),
            ("calendar_events", "ticket_url", "TEXT"),
        ]
        for table, column, col_type in rag_columns:
            if not _table_exists(table):
                continue
            exists = conn.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ), {"table": table, "column": column}).fetchone()
            if exists:
                continue
            try:
                with conn.begin_nested():
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                logger.info(f"Added column {table}.{column} ({col_type})")
            except Exception as e:
                # Most likely pgvector wasn't installable — keep the keyword-only path working
                logger.warning(f"Failed to add {table}.{column}: {e}")

        # 4. Indexes for fast hybrid search
        rag_indexes = [
            # GIN over tsvector for keyword
            ("ix_documents_fts", "CREATE INDEX IF NOT EXISTS ix_documents_fts ON documents USING gin(fts_vector)"),
            ("ix_chunks_fts", "CREATE INDEX IF NOT EXISTS ix_chunks_fts ON document_chunks USING gin(fts_vector)"),
            # IVFFLAT for vector cosine. Lists=100 is fine up to ~100K chunks; bump later.
            ("ix_documents_vec", "CREATE INDEX IF NOT EXISTS ix_documents_vec ON documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"),
            ("ix_chunks_vec", "CREATE INDEX IF NOT EXISTS ix_chunks_vec ON document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"),
            # Trigram index on filenames — powers "did you mean" via similarity().
            # gin_trgm_ops keeps query latency sub-5ms on the ~3k-doc corpus.
            ("ix_documents_filename_trgm", "CREATE INDEX IF NOT EXISTS ix_documents_filename_trgm ON documents USING gin (filename gin_trgm_ops)"),
            # Composite filter index — hybrid search routinely filters by some
            # combination of project_id / category / fiscal_year / doc_type
            # AFTER the IVFFLAT step. Without this, every filtered hybrid
            # query scans the full ANN top-k then post-filters in the join.
            ("ix_documents_filter", "CREATE INDEX IF NOT EXISTS ix_documents_filter ON documents (project_id, category, fiscal_year, doc_type)"),
            # FK index — chunk lookups by document_id are the hottest path
            # in the hybrid-search CTE join.
            ("ix_chunks_doc", "CREATE INDEX IF NOT EXISTS ix_chunks_doc ON document_chunks (document_id)"),
        ]
        for name, sql in rag_indexes:
            try:
                # Savepoint: a failed index (e.g. no pgvector) must not abort
                # the enclosing transaction and every migration after it.
                with conn.begin_nested():
                    conn.execute(text(sql))
            except Exception as e:
                logger.warning(f"Index {name} skipped: {e}")

        # 4a. Google Sign-In columns on users table. Idempotent (IF NOT EXISTS).
        # hashed_password also needs to become nullable so Google-only users
        # can exist; the ALTER is a no-op on Postgres when already nullable.
        if _table_exists("users"):
            user_cols = [
                ("google_id", "TEXT UNIQUE"),
                ("picture_url", "TEXT"),
                ("display_name", "TEXT"),
                ("last_login_at", "TIMESTAMP"),
            ]
            for col, ctype in user_cols:
                exists = conn.execute(text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'users' AND column_name = :c"
                ), {"c": col}).fetchone()
                if not exists:
                    try:
                        conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {ctype}"))
                        logger.info(f"Added column users.{col}")
                    except Exception as e:
                        logger.warning(f"users.{col} add skipped: {e}")
            try:
                conn.execute(text("ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL"))
            except Exception:
                pass  # already nullable
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_google_id ON users(google_id)"))
            except Exception as e:
                logger.warning(f"users.google_id index skipped: {e}")

        # 4b. Backfill calendar_events.event_type for rows still tagged
        # 'general' (pre-classification). Keyword list mirrors
        # scripts.scrape_events._GOVT_KEYWORDS so both sides agree on what
        # counts as a govt meeting. Idempotent — only touches rows whose
        # event_type is still NULL or 'general'.
        if _table_exists("calendar_events"):
            try:
                conn.execute(text("""
                    UPDATE calendar_events
                    SET event_type = CASE
                        WHEN LOWER(title) ~ '(council|planning board|commission|board of education|\\mboe\\M|reorganization|offices? (are )?closed|borough hall|town hall|court|zoning board|shade tree|environmental commission|recreation commission)'
                            THEN 'govt'
                        ELSE 'community'
                    END
                    WHERE event_type IS NULL OR event_type = 'general'
                """))
            except Exception as e:
                logger.warning(f"calendar_events event_type backfill skipped: {e}")

        # 5. Per-user scoping: add user_id to chat_history so each user only sees
        # their own sessions. Existing rows (pre-migration) get NULL — they're
        # treated as legacy/unowned and only visible to admins.
        if _table_exists("chat_history"):
            exists = conn.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'chat_history' AND column_name = 'user_id'"
            )).fetchone()
            if not exists:
                conn.execute(text(
                    "ALTER TABLE chat_history ADD COLUMN user_id UUID REFERENCES users(id)"
                ))
                logger.info("Added column chat_history.user_id")
            try:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_chat_history_user_id "
                    "ON chat_history(user_id)"
                ))
            except Exception as e:
                logger.warning(f"Index ix_chat_history_user_id skipped: {e}")

        # 6. Sharing tables for presentations and projects. Owner-or-share-or-admin
        # access is enforced at the route level; these tables just record who has
        # been granted access and at what role (viewer | editor).
        share_tables = [
            ("presentation_shares", """
                CREATE TABLE IF NOT EXISTS presentation_shares (
                    presentation_id UUID NOT NULL REFERENCES presentations(id) ON DELETE CASCADE,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role VARCHAR NOT NULL DEFAULT 'viewer',
                    created_at TIMESTAMP DEFAULT now(),
                    PRIMARY KEY (presentation_id, user_id)
                )
            """),
            ("project_shares", """
                CREATE TABLE IF NOT EXISTS project_shares (
                    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role VARCHAR NOT NULL DEFAULT 'viewer',
                    created_at TIMESTAMP DEFAULT now(),
                    PRIMARY KEY (project_id, user_id)
                )
            """),
        ]
        for name, sql in share_tables:
            try:
                conn.execute(text(sql))
            except Exception as e:
                logger.warning(f"Table {name} skipped: {e}")

        share_indexes = [
            "CREATE INDEX IF NOT EXISTS ix_presentation_shares_user ON presentation_shares(user_id)",
            "CREATE INDEX IF NOT EXISTS ix_project_shares_user ON project_shares(user_id)",
        ]
        for sql in share_indexes:
            try:
                conn.execute(text(sql))
            except Exception as e:
                logger.warning(f"Share index skipped: {e}")

        # 7. LLM usage tracking — one row per LLM call. Aggregated for the
        # admin cost panel; raw rows kept for drill-down. user_id may be NULL
        # for system jobs (batch OCR, scheduled extraction).
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS llm_usage (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    source VARCHAR NOT NULL,
                    model VARCHAR NOT NULL,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    estimated_cost_usd DOUBLE PRECISION DEFAULT 0,
                    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                    resource_type VARCHAR,
                    resource_id VARCHAR,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP DEFAULT now()
                )
            """))
        except Exception as e:
            # Older Postgres without pgcrypto: fall back to client-supplied UUIDs
            logger.warning(f"llm_usage table create with gen_random_uuid failed: {e} — retrying without default")
            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS llm_usage (
                        id UUID PRIMARY KEY,
                        source VARCHAR NOT NULL,
                        model VARCHAR NOT NULL,
                        input_tokens INTEGER DEFAULT 0,
                        output_tokens INTEGER DEFAULT 0,
                        estimated_cost_usd DOUBLE PRECISION DEFAULT 0,
                        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                        resource_type VARCHAR,
                        resource_id VARCHAR,
                        metadata JSONB DEFAULT '{}'::jsonb,
                        created_at TIMESTAMP DEFAULT now()
                    )
                """))
            except Exception as e2:
                logger.warning(f"llm_usage table create failed: {e2}")

        for sql in [
            "CREATE INDEX IF NOT EXISTS ix_llm_usage_created ON llm_usage(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_llm_usage_user ON llm_usage(user_id)",
            "CREATE INDEX IF NOT EXISTS ix_llm_usage_source ON llm_usage(source)",
        ]:
            try:
                conn.execute(text(sql))
            except Exception as e:
                logger.warning(f"llm_usage index skipped: {e}")

        # 8. Search analytics — one row per query. The zero-result rows are
        # the highest-value: each is either a missing document or a vocab gap
        # in the synonym dictionary. clicked_document_id is back-filled by
        # POST /api/search/click when a user opens a result.
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS search_query_log (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    query TEXT NOT NULL,
                    normalized_query TEXT,
                    parsed_filters JSONB DEFAULT '{}'::jsonb,
                    result_count INTEGER DEFAULT 0,
                    clicked_document_id UUID,
                    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                    latency_ms INTEGER,
                    created_at TIMESTAMP DEFAULT now()
                )
            """))
        except Exception as e:
            logger.warning(f"search_query_log create with gen_random_uuid failed: {e}")
            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS search_query_log (
                        id UUID PRIMARY KEY,
                        query TEXT NOT NULL,
                        normalized_query TEXT,
                        parsed_filters JSONB DEFAULT '{}'::jsonb,
                        result_count INTEGER DEFAULT 0,
                        clicked_document_id UUID,
                        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                        latency_ms INTEGER,
                        created_at TIMESTAMP DEFAULT now()
                    )
                """))
            except Exception as e2:
                logger.warning(f"search_query_log create failed: {e2}")

        for sql in [
            "CREATE INDEX IF NOT EXISTS ix_search_log_created ON search_query_log(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_search_log_zero ON search_query_log(result_count) WHERE result_count = 0",
            "CREATE INDEX IF NOT EXISTS ix_search_log_user ON search_query_log(user_id)",
        ]:
            try:
                conn.execute(text(sql))
            except Exception as e:
                logger.warning(f"search_query_log index skipped: {e}")

        conn.commit()
