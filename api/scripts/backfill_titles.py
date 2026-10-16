"""
One-shot title/department/doc_date backfill.

Runs services.title_extractor.derive() over every document and writes the
result back. Idempotent: skips docs whose title is already set unless
--all is passed. Safe to run on prod via SSM.

Usage (on the EC2 box, from /opt/atlantic-highlands/api):
    python scripts/backfill_titles.py                # missing titles only
    python scripts/backfill_titles.py --all          # everything
    python scripts/backfill_titles.py --limit 100    # first N only
    python scripts/backfill_titles.py --overwrite-department
"""
import argparse
import logging
import os
import sys

# Make `api/` importable when invoked directly from anywhere
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Re-derive titles even when one is already set")
    parser.add_argument("--limit", type=int, default=0, help="Cap rows processed (0 = unlimited)")
    parser.add_argument(
        "--overwrite-department",
        action="store_true",
        help="Replace department even when already set (filename patterns are usually more reliable)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print but don't commit")
    args = parser.parse_args()

    from database import SessionLocal
    from models.document import Document
    from services import title_extractor

    db = SessionLocal()
    try:
        q = db.query(Document)
        if not args.all:
            q = q.filter(Document.title.is_(None))
        if args.limit:
            q = q.limit(args.limit)

        # Materialize all rows up front instead of yield_per. yield_per uses a
        # server-side named cursor that gets invalidated by db.commit(), and
        # we want to commit in batches. 3-5k docs fits comfortably in memory.
        docs = q.all()
        total = len(docs)
        log.info("Processing %d documents (only_missing=%s, overwrite_dept=%s)",
                 total, not args.all, args.overwrite_department)

        titles_updated = 0
        dept_updated = 0
        date_updated = 0
        skipped = 0
        processed = 0

        for doc in docs:
            result = title_extractor.derive(
                filename=doc.filename,
                extracted_text=doc.extracted_text,
                metadata=doc.metadata_,
            )
            processed += 1
            if not result.get("title"):
                skipped += 1
                continue

            if doc.title != result["title"]:
                doc.title = result["title"]
                titles_updated += 1
            if result.get("department") and (args.overwrite_department or not doc.department):
                if doc.department != result["department"]:
                    doc.department = result["department"]
                    dept_updated += 1
            if result.get("doc_date") and not doc.doc_date:
                doc.doc_date = result["doc_date"]
                date_updated += 1

            # Periodic commit so a partial run still saves progress.
            if processed % 500 == 0:
                if args.dry_run:
                    db.rollback()
                else:
                    db.commit()
                log.info(
                    "  progress: %d/%d  titles+=%d dept+=%d date+=%d skipped=%d",
                    processed, total, titles_updated, dept_updated, date_updated, skipped,
                )

        if args.dry_run:
            db.rollback()
            log.info("DRY RUN — rolled back")
        else:
            db.commit()

        log.info(
            "Done. processed=%d  titles=%d  departments=%d  doc_dates=%d  skipped_no_signal=%d",
            processed, titles_updated, dept_updated, date_updated, skipped,
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
