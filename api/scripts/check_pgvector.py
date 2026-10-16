#!/usr/bin/env python3
"""
Verify pgvector is available and installed on a Postgres instance.

Reports four things, in order:
  1. We can connect.
  2. The `vector` extension is in pg_available_extensions
     (i.e. the server has the OS-level package — RDS parameter group flipped,
      docker image is `pgvector/pgvector:pgN`, or apt-installed).
  3. The `vector` extension is created in this database.
  4. The pgvector columns we depend on (documents.embedding,
     document_chunks.embedding) exist.

Usage:
  DATABASE_URL=postgresql://user:pass@host:port/dbname \\
      python api/scripts/check_pgvector.py

  # Or from the repo:
  cd api && python scripts/check_pgvector.py

Exit codes:
  0 = all green
  1 = couldn't connect or query
  2 = vector package not on server (need RDS param group / OS install)
  3 = extension not created (will be auto-created on next API startup)
  4 = embedding columns missing (run an API restart to trigger _migrate())
"""
import os
import sys


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        # Fall back to the same default the API uses.
        db_url = "postgresql://postgres:postgres@localhost:5433/atlantic_highlands"
        print(f"DATABASE_URL not set, using default: {db_url}", file=sys.stderr)

    try:
        import psycopg2
    except ImportError:
        print("psycopg2 not installed. Run: pip install psycopg2-binary", file=sys.stderr)
        return 1

    try:
        conn = psycopg2.connect(db_url, connect_timeout=10)
    except psycopg2.OperationalError as e:
        print(f"FAIL: cannot connect — {e}", file=sys.stderr)
        return 1

    cur = conn.cursor()

    # 1. Server version
    cur.execute("SHOW server_version")
    print(f"server_version: {cur.fetchone()[0]}")

    # 2. Is pgvector available on the server?
    cur.execute(
        "SELECT name, default_version, installed_version "
        "FROM pg_available_extensions WHERE name = 'vector'"
    )
    row = cur.fetchone()
    if not row:
        print("\nFAIL: vector extension is NOT available on this Postgres server.")
        print("\nFix:")
        print("  - RDS:        parameter group → shared_preload_libraries += pgvector → reboot")
        print("  - Docker:     change image to `pgvector/pgvector:pg16` (or pg15/pg17)")
        print("  - Self-host:  apt-get install postgresql-16-pgvector  (or your version)")
        return 2
    name, default_v, installed_v = row
    print(f"available:      {name} v{default_v} (installed: {installed_v or '—'})")

    # 3. Is it installed in THIS database?
    cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    ext = cur.fetchone()
    if not ext:
        print("\nWARN: vector extension is available but not installed in this database.")
        print("It will be auto-created on the next API startup (database.py:_migrate()).")
        print("Or run manually: CREATE EXTENSION IF NOT EXISTS vector;")
        return 3
    print(f"installed:      vector v{ext[0]}")

    # 4. Schema columns
    cur.execute("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE column_name IN ('embedding', 'fts_vector')
          AND table_name IN ('documents', 'document_chunks')
        ORDER BY table_name, column_name
    """)
    cols = cur.fetchall()
    print(f"rag columns:    {len(cols)}/4 found")
    for t, c in cols:
        print(f"                  {t}.{c}")
    if len(cols) < 4:
        print("\nWARN: some embedding columns missing — restart the API to trigger _migrate().")
        return 4

    # 5. Optional: row counts
    cur.execute(
        "SELECT count(*) FROM documents WHERE embedding IS NOT NULL"
    )
    docs_emb = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM documents")
    docs_total = cur.fetchone()[0]
    cur.execute(
        "SELECT count(*) FROM document_chunks WHERE embedding IS NOT NULL"
    )
    chunks_emb = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM document_chunks")
    chunks_total = cur.fetchone()[0]

    print(f"\ndocs embedded:  {docs_emb}/{docs_total}")
    print(f"chunks embedded:{chunks_emb}/{chunks_total}")

    print("\nALL GREEN — pgvector is ready. Run the ingestion script next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
