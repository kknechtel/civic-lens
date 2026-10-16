"""
Load a fixture corpus (tests/fixtures/corpus/<deployment>.jsonl.gz) into the
configured database and index it (chunks, full-text, embeddings if a provider
is configured). Used by CI before scripts/eval_search.

    DATABASE_URL=... DEPLOYMENT=long-beach-ny python -m scripts.load_fixture_corpus long-beach-ny
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
    args = ap.parse_args()
    os.environ.setdefault("DEPLOYMENT", args.deployment)
    from database import SessionLocal, init_db
    from models.document import Document, Project
    from models.user import User
    from services.ingestion import ingest_document
    from services.keyword_search import reset_cache

    init_db()
    path = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "corpus" / f"{args.deployment}.jsonl.gz"
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "fixtures@civic-lens.local").first()
        if not user:
            user = User(email="fixtures@civic-lens.local", username="fixtures", is_active=True, is_admin=False)
            db.add(user)
            db.commit()
        project = Project(name=f"{args.deployment} fixture corpus", created_by=user.id, is_public=True, entity_type="town")
        db.add(project)
        db.commit()
        n = 0
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                doc = Document(project_id=project.id, filename=r["filename"], original_filename=r["filename"],
                               s3_key=f"fixtures/{uuid.uuid4()}", s3_bucket="fixtures", file_size=len(r["extracted_text"]),
                               content_type="application/pdf", doc_type=r.get("doc_type"), category=r.get("category"),
                               municipality=r.get("municipality"), fiscal_year=r.get("fiscal_year"),
                               title=r.get("title"), doc_date=r.get("doc_date"), notes=r.get("notes"),
                               extracted_text=r["extracted_text"], status="processed", uploaded_by=user.id,
                               metadata_={"source_url": r.get("source_url"), "fixture": True})
                db.add(doc)
                db.flush()
                ingest_document(db, doc)
                n += 1
        reset_cache()
    print(f"loaded and indexed {n} documents")
    return 0


if __name__ == "__main__":
    sys.exit(main())
