#!/usr/bin/env python3
"""
manage_pipeline.py — Remote management CLI for the financial-analysis pipeline.

Runs against the DEPLOYED API (not local). Logs in once, then can:
  - diagnose      print current pipeline state
  - extract       bulk-extract every financial document
  - drill         bulk-drill every extracted statement
  - status        watch the pipeline as it processes
  - run           full pipeline: extract -> wait -> drill -> wait -> diagnose
  - sync-one      run one statement's drill in sync mode (returns errors inline)

Usage examples:

  # Initial setup (env vars or args; --base required)
  export AH_BASE_URL=https://api.example.org
  export AH_EMAIL=admin@example.org
  export AH_PASSWORD=...

  # See what's in the system
  python scripts/manage_pipeline.py diagnose

  # Extract every school financial doc that hasn't been extracted
  python scripts/manage_pipeline.py extract --entity school

  # Drill every extracted school statement
  python scripts/manage_pipeline.py drill --entity school

  # End-to-end: extract -> drill -> diagnose, with progress polling
  python scripts/manage_pipeline.py run --entity school

  # Debug one stuck statement
  python scripts/manage_pipeline.py sync-one --statement-id <uuid>

Environment variables (or pass as flags):
  AH_BASE_URL   base URL of the deployed API (https://...)
  AH_EMAIL      login email
  AH_PASSWORD   login password (stdin prompt if missing)
  AH_TOKEN      bearer token (skips login if provided)
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from typing import Optional
from urllib.parse import urlencode

try:
    import requests
except ImportError:
    print("requests not installed. pip install requests", file=sys.stderr)
    sys.exit(1)


class API:
    """Minimal client for the AH Financial API."""

    def __init__(self, base_url: str, token: Optional[str] = None):
        self.base = base_url.rstrip("/")
        self.token = token

    def login(self, email: str, password: str) -> str:
        r = requests.post(f"{self.base}/api/auth/login",
                          json={"email": email, "password": password}, timeout=30)
        r.raise_for_status()
        self.token = r.json()["access_token"]
        return self.token

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def diagnostics(self) -> dict:
        r = requests.get(f"{self.base}/api/financial/diagnostics", headers=self._h(), timeout=30)
        r.raise_for_status()
        return r.json()

    def list_statements(self, entity_type: Optional[str] = None) -> list[dict]:
        qs = f"?entity_type={entity_type}" if entity_type else ""
        r = requests.get(f"{self.base}/api/financial/statements{qs}", headers=self._h(), timeout=60)
        r.raise_for_status()
        return r.json()

    def extract_all(self, entity_type: Optional[str] = None, doc_type: Optional[str] = None,
                    fiscal_year: Optional[str] = None, re_extract: bool = False,
                    concurrency: int = 2) -> dict:
        params = {}
        if entity_type: params["entity_type"] = entity_type
        if doc_type: params["doc_type"] = doc_type
        if fiscal_year: params["fiscal_year"] = fiscal_year
        if re_extract: params["re_extract"] = "true"
        params["concurrency"] = concurrency
        qs = "?" + urlencode(params)
        r = requests.post(f"{self.base}/api/financial/extract-all{qs}",
                          headers=self._h(), timeout=120)
        r.raise_for_status()
        return r.json()

    def drill_all(self, entity_type: Optional[str] = None, fiscal_year: Optional[str] = None,
                  redrill: bool = False, concurrency: int = 2) -> dict:
        params = {}
        if entity_type: params["entity_type"] = entity_type
        if fiscal_year: params["fiscal_year"] = fiscal_year
        if redrill: params["redrill"] = "true"
        params["concurrency"] = concurrency
        qs = "?" + urlencode(params)
        r = requests.post(f"{self.base}/api/financial/drill-all{qs}",
                          headers=self._h(), timeout=60)
        r.raise_for_status()
        return r.json()

    def drill_one_sync(self, statement_id: str) -> dict:
        r = requests.post(f"{self.base}/api/financial/statements/{statement_id}/drill?sync=true",
                          headers=self._h(), timeout=600)  # sync drills can take 90s
        r.raise_for_status()
        return r.json()


# ─── Pretty-printers ─────────────────────────────────────────────────────────


def _fmt_money(n) -> str:
    if n is None:
        return "—"
    try:
        return f"${int(n):,}"
    except Exception:
        return str(n)


def print_diagnostics(d: dict):
    print("\n=== PIPELINE DIAGNOSTICS ===")
    keys = d["llm_keys"]
    print(f"  LLM keys:          anthropic={'OK' if keys['anthropic_api_key_set'] else 'X MISSING'}  "
          f"gemini={'OK' if keys['gemini_api_key_set'] else 'X MISSING'}")

    s = d["statements"]
    print(f"\n  Total statements:  {s['total']}")
    if s["by_status"]:
        print("  By status:")
        for k, v in sorted(s["by_status"].items(), key=lambda x: -x[1]):
            print(f"    {v:>4}  {k}")
    if s["by_entity_type"]:
        print("  By entity:")
        for k, v in s["by_entity_type"].items():
            print(f"    {v:>4}  {k}")
    if s["by_accounting_basis"]:
        print("  By basis:")
        for k, v in s["by_accounting_basis"].items():
            print(f"    {v:>4}  {k or '(unset)'}")

    ei = d["extraction_issues"]
    if ei["extracted_with_no_line_items_count"]:
        print(f"\n  ! {ei['extracted_with_no_line_items_count']} extracted statements have ZERO line items")
        for s_ in ei["extracted_with_no_line_items"][:5]:
            print(f"     | {s_['entity_type']} FY {s_['fiscal_year']}  id={s_['id']}")

    di = d["drill_issues"]
    if di["drills_with_errors_count"]:
        print(f"\n  ! {di['drills_with_errors_count']} statements have drill errors")
        for s_ in di["drills_with_errors_sample"][:5]:
            print(f"     | {s_['entity_type']} FY {s_['fiscal_year']}  ({len(s_['errors'])} errors)")
            for e in s_["errors"][:3]:
                print(f"         {e['drill']}: {e['error']}  {e.get('msg', '')[:80]}")

    print(f"\n  Next: {d['next_steps_hint']}")
    print()


def print_extract_response(r: dict):
    print(f"\n=== EXTRACT-ALL ===")
    print(f"  Queued:   {r.get('queued', 0)} documents")
    print(f"  Skipped:  {r.get('skipped', 0)} (already extracted; use --re-extract to override)")
    if r.get("queued") == 0 and r.get("skipped") == 0:
        print("  No matching documents found.")
    if r.get("queued_sample"):
        print(f"\n  Sample of queued documents:")
        for q in r["queued_sample"][:10]:
            print(f"    | {q['entity_type']} FY {q['fiscal_year']} {q['doc_type']:18}  {q['filename'][:60]}")
    print()


def print_drill_response(r: dict):
    print(f"\n=== DRILL-ALL ===")
    print(f"  Queued:   {r.get('queued', 0)} statements (concurrency={r.get('concurrency', 2)})")
    if r.get("queued") == 0:
        print(f"  Message: {r.get('message', '')}")
    print()


def print_sync_drill(r: dict):
    print(f"\n=== SYNC DRILL ({r['statement_id']}) ===")
    print(f"  Mode:           {r.get('mode')}")
    print(f"  Synthesis OK:   {r.get('synthesis_ok')}")
    print(f"  Drills passed:  {r.get('success_count')}/4")
    print(f"  Drills errored: {r.get('error_count')}/4")
    print(f"  Duration:       {r.get('duration_s')}s")
    drills = r.get("drill_results", {})
    for name in ("revenue", "expenditure", "debt", "fund_balance", "synthesis"):
        d = drills.get(name) or {}
        if "error" in d:
            print(f"  X {name:14}  {d['error']}: {d.get('error_message', '')[:120]}")
        else:
            print(f"  OK {name:14}  {d.get('duration_s', '?')}s")
    print()


# ─── Commands ────────────────────────────────────────────────────────────────


def cmd_diagnose(api: API, args):
    print_diagnostics(api.diagnostics())


def cmd_extract(api: API, args):
    print(f"Bulk-extracting (entity={args.entity}, doc_type={args.doc_type}, "
          f"fy={args.fiscal_year}, re_extract={args.re_extract})...")
    r = api.extract_all(
        entity_type=args.entity, doc_type=args.doc_type,
        fiscal_year=args.fiscal_year, re_extract=args.re_extract,
        concurrency=args.concurrency,
    )
    print_extract_response(r)


def cmd_drill(api: API, args):
    print(f"Bulk-drilling (entity={args.entity}, fy={args.fiscal_year}, redrill={args.redrill})...")
    r = api.drill_all(
        entity_type=args.entity, fiscal_year=args.fiscal_year,
        redrill=args.redrill, concurrency=args.concurrency,
    )
    print_drill_response(r)


def cmd_status(api: API, args):
    print("Watching pipeline status (Ctrl-C to stop)...")
    try:
        while True:
            d = api.diagnostics()
            statuses = d["statements"]["by_status"]
            line = " | ".join(f"{v} {k}" for k, v in sorted(statuses.items(), key=lambda x: -x[1]))
            err_count = d["drill_issues"]["drills_with_errors_count"]
            empty_count = d["extraction_issues"]["extracted_with_no_line_items_count"]
            print(f"[{time.strftime('%H:%M:%S')}] {line}  | drill_errors={err_count} empty={empty_count}")
            # If nothing is processing or pending, stop
            if statuses.get("processing", 0) == 0 and statuses.get("pending", 0) == 0:
                if statuses.get("extracted", 0) == 0 or args.once:
                    break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n(stopped)")


def cmd_run(api: API, args):
    print(f"\n-> Step 1/4: Initial diagnostics")
    print_diagnostics(api.diagnostics())

    print(f"-> Step 2/4: Bulk extract (entity={args.entity})")
    er = api.extract_all(
        entity_type=args.entity, doc_type=args.doc_type, fiscal_year=args.fiscal_year,
        re_extract=args.re_extract, concurrency=args.concurrency,
    )
    print_extract_response(er)

    if er.get("queued", 0) > 0:
        print(f"-> Step 3a/4: Polling extraction (every {args.interval}s, timeout {args.timeout}s)...")
        deadline = time.time() + args.timeout
        last_processing = None
        while time.time() < deadline:
            d = api.diagnostics()
            processing = d["statements"]["by_status"].get("processing", 0)
            extracted = d["statements"]["by_status"].get("extracted", 0)
            errored = d["statements"]["by_status"].get("error", 0)
            if processing != last_processing:
                print(f"  [{time.strftime('%H:%M:%S')}] processing={processing} extracted={extracted} error={errored}")
                last_processing = processing
            if processing == 0:
                print(f"  Extraction phase complete.\n")
                break
            time.sleep(args.interval)
        else:
            print(f"  Timeout — moving on. (Some statements may still be processing.)\n")
    else:
        print(f"-> Step 3a/4: skipped (nothing to extract)\n")

    print(f"-> Step 3b/4: Bulk drill")
    dr = api.drill_all(
        entity_type=args.entity, fiscal_year=args.fiscal_year,
        redrill=args.redrill, concurrency=args.concurrency,
    )
    print_drill_response(dr)

    if dr.get("queued", 0) > 0:
        print(f"-> Step 3c/4: Polling drills (every {args.interval}s, timeout {args.timeout}s)...")
        deadline = time.time() + args.timeout
        prior_drilled = -1
        while time.time() < deadline:
            d = api.diagnostics()
            drilled = d["statements"]["by_status"].get("drilled", 0)
            extracted = d["statements"]["by_status"].get("extracted", 0)
            err_count = d["drill_issues"]["drills_with_errors_count"]
            if drilled != prior_drilled:
                print(f"  [{time.strftime('%H:%M:%S')}] drilled={drilled} extracted={extracted} drill_errors={err_count}")
                prior_drilled = drilled
            # Done when no more 'extracted' (waiting to drill) and no 'processing'
            if extracted == 0 and d["statements"]["by_status"].get("processing", 0) == 0:
                print(f"  Drill phase complete.\n")
                break
            time.sleep(args.interval)
        else:
            print(f"  Timeout — moving on. (Some drills may still be running.)\n")

    print(f"-> Step 4/4: Final diagnostics")
    print_diagnostics(api.diagnostics())


def cmd_sync_one(api: API, args):
    print(f"Running synchronous drill on statement {args.statement_id} (may take 30-90s)...")
    r = api.drill_one_sync(args.statement_id)
    print_sync_drill(r)


# ─── Main ────────────────────────────────────────────────────────────────────


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", default=os.environ.get("AH_BASE_URL"),
                   help="Deployed API base URL (e.g. https://api.example.com). Env: AH_BASE_URL")
    p.add_argument("--email", default=os.environ.get("AH_EMAIL"), help="Login email. Env: AH_EMAIL")
    p.add_argument("--password", default=os.environ.get("AH_PASSWORD"),
                   help="Login password. Env: AH_PASSWORD. Will prompt if missing.")
    p.add_argument("--token", default=os.environ.get("AH_TOKEN"),
                   help="Bearer token (skips login). Env: AH_TOKEN")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("diagnose", help="Print pipeline state").set_defaults(func=cmd_diagnose)

    pe = sub.add_parser("extract", help="Bulk extract financial documents")
    pe.add_argument("--entity", choices=["town", "school"])
    pe.add_argument("--doc-type", dest="doc_type", choices=["budget", "audit", "financial_statement"])
    pe.add_argument("--fiscal-year", dest="fiscal_year")
    pe.add_argument("--re-extract", dest="re_extract", action="store_true",
                    help="Also re-extract docs that already have a statement")
    pe.add_argument("--concurrency", type=int, default=2)
    pe.set_defaults(func=cmd_extract)

    pd = sub.add_parser("drill", help="Bulk drill extracted statements")
    pd.add_argument("--entity", choices=["town", "school"])
    pd.add_argument("--fiscal-year", dest="fiscal_year")
    pd.add_argument("--redrill", action="store_true", help="Re-drill already-drilled statements")
    pd.add_argument("--concurrency", type=int, default=2)
    pd.set_defaults(func=cmd_drill)

    ps = sub.add_parser("status", help="Watch pipeline status")
    ps.add_argument("--interval", type=int, default=10)
    ps.add_argument("--once", action="store_true")
    ps.set_defaults(func=cmd_status)

    pr = sub.add_parser("run", help="End-to-end: extract -> wait -> drill -> wait -> diagnose")
    pr.add_argument("--entity", choices=["town", "school"])
    pr.add_argument("--doc-type", dest="doc_type", choices=["budget", "audit", "financial_statement"])
    pr.add_argument("--fiscal-year", dest="fiscal_year")
    pr.add_argument("--re-extract", dest="re_extract", action="store_true")
    pr.add_argument("--redrill", action="store_true")
    pr.add_argument("--concurrency", type=int, default=2)
    pr.add_argument("--interval", type=int, default=15, help="Poll interval seconds")
    pr.add_argument("--timeout", type=int, default=1800, help="Per-phase timeout seconds")
    pr.set_defaults(func=cmd_run)

    p1 = sub.add_parser("sync-one", help="Run one statement's drill synchronously (for debugging)")
    p1.add_argument("--statement-id", dest="statement_id", required=True)
    p1.set_defaults(func=cmd_sync_one)

    return p


def main():
    parser = _build_parser()
    args = parser.parse_args()

    if not args.base:
        parser.error("--base or AH_BASE_URL is required")

    api = API(args.base, token=args.token)
    if not api.token:
        if not args.email:
            parser.error("--email or AH_EMAIL is required (or supply --token / AH_TOKEN)")
        password = args.password or getpass.getpass(f"Password for {args.email}: ")
        try:
            api.login(args.email, password)
        except requests.HTTPError as e:
            print(f"Login failed: {e.response.status_code} {e.response.text[:200]}", file=sys.stderr)
            sys.exit(1)

    try:
        args.func(api, args)
    except requests.HTTPError as e:
        print(f"\nAPI error: {e.response.status_code} {e.response.text[:300]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
