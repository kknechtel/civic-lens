"""
Load a corpus export (.jsonl.gz from scripts.export_fixture_corpus) into the
configured database and index it: chunks, full-text, and embeddings if a
provider is configured. No OCR — the text is already extracted.

CI (fixture corpus, throwaway project):
    DATABASE_URL=... python -m scripts.load_fixture_corpus long-beach-ny
Moving a deployment to a new server (keeps storage keys so scraper dedupe and
scripts.rehydrate_files line up; safe to re-run — existing filenames are skipped):
    DEPLOYMENT=long-beach-ny python -m scripts.load_fixture_corpus long-beach-ny \\
        --file lb-corpus.jsonl.gz --project "Long Beach Scraped Documents"
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import uuid
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("deployment")
    ap.add_argument("--file", default=None, help="corpus .jsonl.gz (default: the CI fixture for this deployment)")
    ap.add_argument("--project", default=None, help='project name (default "<deployment> fixture corpus")')
    args = ap.parse_args()
    os.environ.setdefault("DEPLOYMENT", args.deployment)
    from config import S3_BUCKET
    from database import SessionLocal, init_db
    from models.document import Document, Project
    from models.user import User
    from services.ingestion import ingest_document
    from services.keyword_search import reset_cache

    init_db()
    path = Path(args.file) if args.file else \
        Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "corpus" / f"{args.deployment}.jsonl.gz"
    name = args.project or f"{args.deployment} fixture corpus"
    with SessionLocal() as db:
        owner = (db.query(User).filter(User.is_admin.is_(True)).order_by(User.created_at).first()
                 or db.query(User).filter(User.email == "fixtures@civic-lens.local").first())
        if not owner:
            owner = User(email="fixtures@civic-lens.local", username="fixtures", is_active=False, is_admin=False)
            db.add(owner)
            db.commit()
        project = db.query(Project).filter(Project.name == name).first()
        if not project:
            project = Project(name=name, created_by=owner.id, is_public=True, entity_type="town")
            db.add(project)
            db.commit()
        have = {fn.lower() for (fn,) in db.query(Document.filename).filter(Document.project_id == project.id)}
        n = skipped = 0
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r["filename"].lower() in have:
                    skipped += 1
                    continue
                meta = r.get("metadata") or {"source_url": r.get("source_url")}
                meta = {**meta, "imported_from": path.name}
                doc = Document(project_id=project.id, filename=r["filename"],
                               original_filename=r.get("original_filename") or r["filename"],
                               s3_key=r.get("s3_key") or f"imported/{uuid.uuid4()}", s3_bucket=S3_BUCKET,
                               file_size=r.get("file_size") or len(r["extracted_text"]),
                               content_type=r.get("content_type") or "application/pdf", page_count=r.get("page_count"),
                               doc_type=r.get("doc_type"), category=r.get("category"), department=r.get("department"),
                               municipality=r.get("municipality"), fiscal_year=r.get("fiscal_year"),
                               title=r.get("title"), doc_date=r.get("doc_date"), notes=r.get("notes"),
                               extracted_text=r["extracted_text"], status="processed", uploaded_by=owner.id,
                               metadata_=meta)
                db.add(doc)
                db.flush()
                ingest_document(db, doc)
                n += 1
                if n % 100 == 0:
                    print(f"  {n} indexed…", flush=True)
        reset_cache()
    print(f"loaded and indexed {n} documents" + (f" ({skipped} already present)" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
