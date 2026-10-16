"""
Export a corpus: processed documents (metadata + extracted text, no files,
no vectors) as one .jsonl.gz.

For CI: every document an eval question expects plus a seeded sample of
others, text truncated at a page boundary, to tests/fixtures/corpus/.
    DATABASE_URL=... python -m scripts.export_fixture_corpus long-beach-ny --distractors 250

To move a deployment's processed corpus to another server (import with
scripts.load_fixture_corpus --file, then fetch the files back with
scripts.rehydrate_files) — no OCR needed on the new box:
    DATABASE_URL=... python -m scripts.export_fixture_corpus long-beach-ny --all --out lb-corpus.jsonl.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import re
import sys
from pathlib import Path

MAX_CHARS = 40_000


def _truncate(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    cut = text.rfind("\n## Page ", 0, MAX_CHARS)
    return text[: cut if cut > MAX_CHARS // 2 else MAX_CHARS]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("deployment")
    ap.add_argument("--distractors", type=int, default=250)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--all", action="store_true", help="every processed document, untruncated")
    ap.add_argument("--out", default=None, help="output path (default tests/fixtures/corpus/<deployment>.jsonl.gz)")
    args = ap.parse_args()
    os.environ.setdefault("DEPLOYMENT", args.deployment)
    from sqlalchemy import text

    from database import SessionLocal
    from scripts.eval_search import load_cases
    want = {w.lower() for c in load_cases(args.deployment) for w in c["expect"]}
    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT filename, original_filename, s3_key, content_type, file_size, page_count, department,
                   title, doc_type, category, municipality, doc_date, fiscal_year, notes,
                   extracted_text, metadata->>'source_url' AS source_url, metadata AS metadata
            FROM documents WHERE extracted_text IS NOT NULL AND length(extracted_text) > 200""")).mappings().all()
    targets = [r for r in rows if any(w in r["filename"].lower() for w in want)]
    rest = [r for r in rows if r not in targets]
    random.Random(args.seed).shuffle(rest)
    picked = list(rows) if args.all else targets + rest[: args.distractors]
    out = Path(args.out) if args.out else \
        Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "corpus" / f"{args.deployment}.jsonl.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        for r in picked:
            d = dict(r)
            text_ = re.sub("\x00", "", d["extracted_text"])
            d["extracted_text"] = text_ if args.all else _truncate(text_)
            if not args.all:
                d.pop("metadata", None)
            f.write(json.dumps(d, default=str) + "\n")
    print(f"{out.name}: {len(targets)} eval targets + {len(picked) - len(targets)} distractors, "
          f"{out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
