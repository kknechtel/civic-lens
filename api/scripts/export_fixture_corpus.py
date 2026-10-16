"""
Export a small, real corpus for CI search evals.

Takes every document an eval question expects plus a seeded random sample of
others (distractors), with extracted text truncated at a page boundary, and
writes tests/fixtures/corpus/<deployment>.jsonl.gz.

    DATABASE_URL=... python -m scripts.export_fixture_corpus long-beach-ny --distractors 250
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
    args = ap.parse_args()
    os.environ.setdefault("DEPLOYMENT", args.deployment)
    from sqlalchemy import text

    from database import SessionLocal
    from scripts.eval_search import load_cases
    want = {w.lower() for c in load_cases(args.deployment) for w in c["expect"]}
    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT filename, title, doc_type, category, municipality, doc_date, fiscal_year, notes,
                   extracted_text, metadata->>'source_url' AS source_url
            FROM documents WHERE extracted_text IS NOT NULL AND length(extracted_text) > 200""")).mappings().all()
    targets = [r for r in rows if any(w in r["filename"].lower() for w in want)]
    rest = [r for r in rows if r not in targets]
    random.Random(args.seed).shuffle(rest)
    picked = targets + rest[: args.distractors]
    out = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "corpus" / f"{args.deployment}.jsonl.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        for r in picked:
            d = dict(r)
            d["extracted_text"] = _truncate(re.sub("\x00", "", d["extracted_text"]))
            f.write(json.dumps(d, default=str) + "\n")
    print(f"{out.name}: {len(targets)} eval targets + {len(picked) - len(targets)} distractors, "
          f"{out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
