"""
Validate a municipality profile and (optionally) dry-run its sources.

    python -m scripts.check_profile long-beach-ny            # schema + references only
    python -m scripts.check_profile long-beach-ny --live     # also fetch index pages (no downloads)
    python -m scripts.check_profile long-beach-ny --live --only lbcsd_board osc_audits

--live reports, per source: documents discovered, doc_type mix, robots.txt
blocks, and three sample titles — the fastest way to tune a new profile.
Nothing is written to the database or storage.
"""
from __future__ import annotations

import argparse
import collections
import logging
import os
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--live", action="store_true", help="fetch index pages and list what each source would ingest")
    ap.add_argument("--only", nargs="*", default=None, help="source ids to check")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="  %(levelname)s %(name)s: %(message)s")
    os.environ["MUNICIPALITY"] = args.slug
    from municipality import load_municipality
    from services.sources import build_adapter

    try:
        muni = load_municipality(args.slug)
    except Exception as e:  # validation errors are the point of this tool
        print(f"✗ {args.slug}: {e}")
        return 1

    print(f"✓ {muni.name}, {muni.state} — {len(muni.entities)} entities, {len(muni.sources)} sources, "
          f"{len(muni.events.venues)} event venues")
    print(f"  records law: {muni.state_profile.records_law.short if muni.state_profile else '— (no _states file)'}"
          f" · fiscal year starts month {muni.fiscal_year.start_month}")
    if not args.live:
        return 0

    failures = 0
    for s in muni.sources:
        if args.only and s.id not in args.only:
            continue
        if not s.enabled:
            print(f"\n· {s.id}: disabled")
            continue
        t = time.time()
        adapter = build_adapter(s, muni)
        try:
            items = adapter.find_documents()
            err = None
        except Exception as e:
            items, err = [], f"{type(e).__name__}: {e}"
        types = collections.Counter(i.get("doc_type") or "—" for i in items)
        mark = "✗" if err or (not items and not adapter.basic.errors) else ("!" if adapter.basic.errors else "✓")
        print(f"\n{mark} {s.id} [{s.adapter} → {s.entity}] {len(items)} items in {time.time() - t:.1f}s"
              f"  {dict(types.most_common(5))}")
        for e in ([err] if err else []) + adapter.basic.errors[:2]:
            print(f"    {e}")
        for i in items[:3]:
            print(f"    - {i['title'][:80]}")
        failures += bool(err)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
