"""
Run the integrity pipeline by hand.

    python -m scripts.run_integrity                 # extract new docs + run rules
    python -m scripts.run_integrity --force         # re-extract every document
    python -m scripts.run_integrity --report        # print open flags, worst first
    python -m scripts.run_integrity --import-contributions elec.csv --source "NJ ELEC"
"""
import argparse
import json
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--import-contributions", metavar="CSV")
    ap.add_argument("--source", default="contributions")
    args = ap.parse_args()

    from database import SessionLocal, init_db
    from models.integrity import IntegrityFlag
    from services.integrity.runner import import_contributions_csv, run_all

    init_db()
    with SessionLocal() as db:
        if args.import_contributions:
            n = import_contributions_csv(db, args.import_contributions, args.source)
            print(f"imported {n} contributions from {args.import_contributions} as {args.source!r}")
        if not args.report or args.force:
            print(json.dumps(run_all(db, force=args.force), indent=2, default=str))
        if args.report:
            rank = {"high": 0, "medium": 1, "low": 2}
            rows = db.query(IntegrityFlag).filter(IntegrityFlag.active == 1, IntegrityFlag.status != "dismissed").all()
            rows.sort(key=lambda f: (rank.get(f.severity, 3), -(f.score or 0)))
            for f in rows[: args.limit]:
                print(f"[{f.severity:6s}] {f.rule:28s} {f.title}")
                for e in (f.evidence or [])[:2]:
                    print(f"           ↳ {e.get('title') or ''} p.{e.get('page') or '?'}: {(e.get('quote') or '')[:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
