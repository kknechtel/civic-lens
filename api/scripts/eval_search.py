"""
Retrieval eval: does the search chat relies on return the answer document?

Reads jurisdictions/<deployment>/evals/search.yaml (question → expected
filename fragments), runs each question through chat's own search tool and
scores at the document level (chunks from one document count once).

    DATABASE_URL=... python -m scripts.eval_search atlantic-highlands-nj
    python -m scripts.eval_search long-beach-ny --k 10 --show-misses

Prints hit@5, hit@k and MRR; exits non-zero below --min-hit (for CI).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def load_cases(deployment: str) -> list[dict]:
    import yaml
    from municipality import JURISDICTIONS_DIR, resolve
    path = Path(JURISDICTIONS_DIR) / resolve(deployment) / "evals" / "search.yaml"
    return yaml.safe_load(path.read_text()) or []


def ranked_docs(db, query: str, k: int) -> list[str]:
    from routes.chat import _exec_search_chunks
    res = _exec_search_chunks(db, {"query": query, "top_k": 20})
    seen, out = set(), []
    for c in res["chunks"]:
        if c["document_id"] not in seen:
            seen.add(c["document_id"])
            out.append(c.get("filename") or "")
    return out[:k]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("deployment")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--show-misses", action="store_true")
    ap.add_argument("--min-hit", type=float, default=0.0, help="fail if hit@k is below this (0–1)")
    args = ap.parse_args()
    os.environ.setdefault("DEPLOYMENT", args.deployment)

    from database import SessionLocal
    cases = load_cases(args.deployment)
    hits5 = hitsk = 0
    rr = 0.0
    with SessionLocal() as db:
        for case in cases:
            docs = ranked_docs(db, case["q"], args.k)
            want = [w.lower() for w in case["expect"]]
            rank = next((i + 1 for i, fn in enumerate(docs) if any(w in fn.lower() for w in want)), None)
            hits5 += bool(rank and rank <= 5)
            hitsk += bool(rank)
            rr += 1 / rank if rank else 0
            mark = f"#{rank}" if rank else "MISS"
            print(f"{mark:>5}  {case['q']}")
            if args.show_misses and not rank:
                for fn in docs[:5]:
                    print(f"         got: {fn[-90:]}")
    n = len(cases) or 1
    print(f"\n{args.deployment}: hit@5 {hits5 / n:.0%}  hit@{args.k} {hitsk / n:.0%}  MRR {rr / n:.2f}  ({len(cases)} questions)")
    return 1 if hitsk / n < args.min_hit else 0


if __name__ == "__main__":
    sys.exit(main())
