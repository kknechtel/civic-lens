"""
Fetch the original files for documents whose text was imported without them
(scripts.load_fixture_corpus --file), from each document's source_url, into
the document's own storage key. Network only — no OCR, no re-indexing.
Polite (one request at a time, crawl delay) and resumable: re-run to continue.

    DEPLOYMENT=long-beach-ny python -m scripts.rehydrate_files --limit 200
"""
from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()
    from database import SessionLocal
    from models.document import Document
    from services.s3_service import S3Service
    from services.scraper.scraper import BasicScraper

    s3, web = S3Service(), BasicScraper()
    done = missing = failed = 0
    with SessionLocal() as db:
        docs = db.query(Document).filter(Document.metadata_["imported_from"].isnot(None)).all()
        for d in docs:
            if args.limit and done >= args.limit:
                break
            try:
                s3.download_file(d.s3_key)
                continue  # already there
            except Exception:
                pass
            url = (d.metadata_ or {}).get("source_url")
            if not url:
                missing += 1
                continue
            content, _ = web.download_file_to_bytes(url)
            if not content:
                failed += 1
                continue
            s3.upload_file(content, d.s3_key, d.content_type)
            done += 1
            if done % 50 == 0:
                print(f"  {done} files fetched…", flush=True)
            time.sleep(args.delay)
    print(f"fetched {done}, failed {failed}, no source url {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
