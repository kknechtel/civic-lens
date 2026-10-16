#!/usr/bin/env python3
"""
Backfill: extract (local OCR) → title/summary/classify (flash-lite) → index,
for existing documents. Dry run by default: writes a before/after CSV and
changes nothing.

    python -m scripts.enrich_all --limit 50                 # dry run → CSV for review
    python -m scripts.enrich_all --apply --limit 500
    python -m scripts.enrich_all --apply --max-usd 5        # the rest, with a spend cap
    python -m scripts.enrich_all --apply --municipality highlands --reextract

Selects docs not yet enriched at the current ENRICH_VERSION (newest first).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("enrich_all")


def _select_ids(args) -> list[str]:
    from sqlalchemy import cast, Integer, or_
    from database import SessionLocal
    from models.document import Document
    from services.doc_enrich import ENRICH_VERSION

    with SessionLocal() as db:
        version = cast(Document.metadata_["enrich_version"].astext, Integer)
        q = (db.query(Document.id)
             .filter(or_(Document.doc_type.is_(None), ~Document.doc_type.like("recording%")))
             .filter(or_(Document.content_type.is_(None), Document.content_type != "video/youtube")))
        if not args.force:
            q = q.filter(or_(version.is_(None), version < ENRICH_VERSION))
        if args.municipality:
            q = q.filter(Document.municipality == args.municipality)
        if args.since:
            q = q.filter(Document.created_at >= datetime.fromisoformat(args.since))
        q = q.order_by(Document.created_at.desc())
        if args.limit:
            q = q.limit(args.limit)
        return [str(r[0]) for r in q.all()]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--since", help="only docs created on/after YYYY-MM-DD")
    ap.add_argument("--municipality")
    ap.add_argument("--max-usd", type=float, default=10.0, help="stop when spend reaches this")
    ap.add_argument("--reextract", action="store_true", help="re-run OCR even if text exists")
    ap.add_argument("--force", action="store_true", help="include already-enriched docs")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--csv", default=f"enrich_{datetime.now():%Y%m%d_%H%M}.csv")
    args = ap.parse_args()

    from database import SessionLocal
    from models.document import Document
    from services.document_processor import process_one

    ids = _select_ids(args)
    logger.info("%d docs selected (%s)", len(ids), "APPLY" if args.apply else "dry run")

    spent = 0.0
    counts: dict[str, int] = {}
    sem = asyncio.Semaphore(args.concurrency)
    stop = asyncio.Event()
    fh = open(args.csv, "w", newline="", encoding="utf-8")
    out = csv.writer(fh)
    out.writerow(["id", "status", "method", "chars", "old_title", "new_title", "old_filename",
                  "municipality", "doc_type", "doc_date", "summary", "cost_usd", "error"])

    async def run(doc_id: str):
        nonlocal spent
        if stop.is_set():
            return
        async with sem:
            if stop.is_set():
                return
            with SessionLocal() as db:
                doc = db.get(Document, doc_id)
                if not doc:
                    return
                old_title, filename = doc.title, doc.filename
                r = await process_one(db, doc, reextract=args.reextract, dry_run=not args.apply)
                spent += r.cost_usd
                counts[r.status] = counts.get(r.status, 0) + 1
                new = {k: v[1] for k, v in r.diff.items()}
                out.writerow([doc_id, r.status, r.method, r.chars, old_title or "",
                              new.get("title", ""), filename, new.get("municipality", ""),
                              new.get("doc_type", ""), new.get("doc_date", ""),
                              (new.get("notes") or "").replace("\n", " ")[:600],
                              round(r.cost_usd, 6), r.error or ""])
                done = sum(counts.values())
                if done % 25 == 0:
                    fh.flush()
                    logger.info("%d/%d  spent $%.4f  %s", done, len(ids), spent, counts)
                if spent >= args.max_usd:
                    logger.warning("spend cap $%.2f reached — stopping", args.max_usd)
                    stop.set()

    await asyncio.gather(*(run(i) for i in ids))
    fh.close()
    logger.info("done: %s  spent $%.4f  csv=%s", counts, spent, args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
