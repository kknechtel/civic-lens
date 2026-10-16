"""
Scaffold a new municipality (or county) from its website.

    python -m scripts.new_jurisdiction us/nj/monmouth/rumson --name "Borough of Rumson" \\
        --site https://www.rumsonnj.gov
    python -m scripts.new_jurisdiction us/ny/nassau/glen-cove --name "City of Glen Cove" \\
        --site https://glencoveny.gov --site https://www.glencove.k12.ny.us --level municipality

What it does:
  1. Creates jurisdictions/<path>/jurisdiction.yaml (and a minimal county file
     if the county doesn't exist yet). The state must already exist: state
     packs carry the law (bid thresholds, records law, pay-to-play).
  2. Runs discovery on each --site: fingerprints records platforms (eCode360,
     CivicPlus, YouTube, iCal…) and pages dense with documents, and writes them
     as draft sources.
  3. Prints what it found and the next commands to run.

Review the file before deploying: discovery is a draft, not a configuration.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml


def _slug_key(path: str) -> str:
    return path.rstrip("/").split("/")[-1].replace("-", "_")


def _short(name: str) -> str:
    return re.sub(r"^(Borough|City|Town|Township|Village|County) of ", "", name).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="jurisdiction path, e.g. us/nj/monmouth/rumson")
    ap.add_argument("--name", required=True, help='official name, e.g. "Borough of Rumson"')
    ap.add_argument("--site", action="append", default=[], help="website to discover (repeatable)")
    ap.add_argument("--level", default="municipality", choices=["municipality", "county", "school_district"])
    ap.add_argument("--government-type", default=None, help="borough | city | township | village | county …")
    ap.add_argument("--max-pages", type=int, default=60)
    ap.add_argument("--force", action="store_true", help="overwrite an existing jurisdiction.yaml")
    ap.add_argument("--dry-run", action="store_true", help="print the YAML instead of writing it")
    args = ap.parse_args()

    from municipality import JURISDICTIONS_DIR
    base = Path(JURISDICTIONS_DIR)
    path = args.path.strip("/").lower()
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "us":
        print("path must look like us/<state>/<county>[/<place>]", file=sys.stderr)
        return 2
    state_dir = base / "/".join(parts[:2])
    if not (state_dir / "jurisdiction.yaml").exists():
        print(f"No state pack at {state_dir.relative_to(base)}. Add the state first (see docs/ADDING_A_MUNICIPALITY.md §6).",
              file=sys.stderr)
        return 2
    target = base / path / "jurisdiction.yaml"
    if target.exists() and not (args.force or args.dry_run):
        print(f"{target.relative_to(base)} exists; pass --force to overwrite.", file=sys.stderr)
        return 2

    created = []
    if args.level == "municipality" and len(parts) >= 4:
        county_dir = base / "/".join(parts[:3])
        if not (county_dir / "jurisdiction.yaml").exists():
            county_name = parts[2].replace("-", " ").title()
            county = {"name": f"County of {county_name}", "short_name": f"{county_name} County", "level": "county"}
            if not args.dry_run:
                county_dir.mkdir(parents=True, exist_ok=True)
                (county_dir / "jurisdiction.yaml").write_text(
                    f"# {county['short_name']} — created by new_jurisdiction; add county-wide sources here.\n"
                    + yaml.safe_dump(county, sort_keys=False))
            created.append(str((county_dir / "jurisdiction.yaml").relative_to(base)))

    from services.discovery import discover
    sources, notes, platforms = [], [], {}
    entity = _slug_key(path)
    for site in args.site:
        print(f"Discovering {site} …", flush=True)
        rep = discover([site], max_pages=args.max_pages)
        print(f"  {rep.pages_fetched} pages · platforms: {', '.join(rep.platforms) or 'none'} · "
              f"{len(rep.sources)} draft source(s)")
        platforms.update(rep.platforms)
        notes += rep.notes
        taken = {s["id"] for s in sources}
        for s in rep.sources:
            sid = s["id"] if s["id"] not in taken else f"{s['id']}_{len(sources) + 1}"
            out = {k: v for k, v in {"id": sid, "label": s.get("label"), "description": s.get("description"),
                                     "adapter": s["adapter"], "entity": entity}.items() if v}
            if s.get("doc_type"):
                out["doc_type"] = s["doc_type"]
            out["params"] = s["params"]
            sources.append(out)

    gov = args.government_type or (args.name.split(" of ")[0].lower() if " of " in args.name else args.level)
    short = _short(args.name)
    doc = {
        "name": args.name,
        "short_name": short,
        "level": args.level,
        "government_type": gov,
        "aliases": [path.split("/")[-1] + "-" + parts[1]],
        "branding": {"app_name": short, "theme_color": "#2563eb"},
        "entities": [{"key": entity, "label": args.name, "short": short, "kind": "municipal" if args.level == "municipality" else args.level,
                      "aliases": [short.lower()]}],
        "ai": {"background": f"{args.name} is in {parts[2].replace('-', ' ').title()} County, {parts[1].upper()}. "
                             "Add sourced, dated facts here (form of government, officials, notable events).",
               "suggested_questions": ["What did the governing body approve at its last meeting?",
                                       "Which contracts were awarded without competitive bidding?",
                                       "How has the budget changed over the last three years?"]},
        "sources": sources,
    }
    header = (f"# {args.name} — drafted by scripts/new_jurisdiction.py from {', '.join(args.site) or 'no site'}.\n"
              "# REVIEW BEFORE DEPLOYING: sources are discovery guesses. Check each with\n"
              f"#   python -m scripts.check_profile {path} --live\n"
              + "".join(f"# NOTE: {n}\n" for n in notes))
    text = header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110)
    if args.dry_run:
        print(text)
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    created.append(str(target.relative_to(base)))

    from municipality import load_deployment
    m = load_deployment(path)  # validates the tree: fails loudly on a bad file
    print(f"\nCreated: {', '.join(created)}")
    print(f"Loads as deployment '{m.slug}' with {len(m.sources)} source(s) "
          f"(state rules from {parts[1].upper()}: bid threshold, records law, integrity pack).")
    for n in notes:
        print(f"  note: {n}")
    print(f"""
Next:
  python -m scripts.check_profile {path} --live          # what each source finds
  SCRAPER_MAX_DOCS_PER_SOURCE=10 DEPLOYMENT={path} python -m scripts.scheduled_scrape
  python -m scripts.eval_search {path}                   # after writing jurisdictions/{path}/evals/search.yaml""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
