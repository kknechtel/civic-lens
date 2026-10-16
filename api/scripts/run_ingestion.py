#!/usr/bin/env python3
"""
Drive the chunking + embedding pipeline until every document with text is ingested.

Calls /api/ingestion/run repeatedly in batches of 200 until status reports that
all eligible documents have been chunked. Reports embedding coverage too — if
VOYAGE_API_KEY isn't set, you'll see chunks_embedded == 0 (only hash vectors)
and that's expected.

Usage:
  # Local
  AH_TOKEN=$(curl ...login) python api/scripts/run_ingestion.py

  # Production
  AH_API=https://api.example.com AH_TOKEN=... python api/scripts/run_ingestion.py

Env vars:
  AH_API     base URL  (default http://localhost:8000)
  AH_TOKEN   admin JWT (required — get from POST /api/auth/login)
  AH_BATCH   docs per call (default 200)
"""
import os
import sys
import time
from urllib.parse import urljoin

import requests

API_BASE = os.environ.get("AH_API", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("AH_TOKEN")
BATCH = int(os.environ.get("AH_BATCH", "200"))
SLEEP_BETWEEN = float(os.environ.get("AH_SLEEP", "1"))

if not TOKEN:
    print("ERROR: set AH_TOKEN to an admin JWT (POST /api/auth/login → access_token)", file=sys.stderr)
    sys.exit(1)


def hdrs():
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def status() -> dict:
    r = requests.get(urljoin(API_BASE + "/", "api/ingestion/status"), headers=hdrs(), timeout=30)
    r.raise_for_status()
    return r.json()


def run_batch(limit: int) -> dict:
    r = requests.post(
        urljoin(API_BASE + "/", f"api/ingestion/run?limit={limit}"),
        headers=hdrs(),
        timeout=900,  # ingestion + embedding can take minutes for large batches
    )
    r.raise_for_status()
    return r.json()


def fmt_status(s: dict) -> str:
    return (
        f"docs_total={s['documents_total']}  "
        f"with_text={s['documents_with_text']}  "
        f"chunked={s['documents_chunked']}  "
        f"chunks={s['chunks_total']}  "
        f"docs_embedded={s['documents_embedded']}  "
        f"chunks_embedded={s['chunks_embedded']}"
    )


def main() -> int:
    print(f"API: {API_BASE}")
    s = status()
    print(f"start: {fmt_status(s)}")

    target = s["documents_with_text"]
    if s["documents_chunked"] >= target:
        print("nothing to ingest — all eligible documents are already chunked")
        return 0

    iteration = 0
    last_chunked = s["documents_chunked"]
    stuck_iterations = 0

    while True:
        iteration += 1
        print(f"\n[batch {iteration}] running ingest (limit={BATCH})…")
        try:
            res = run_batch(BATCH)
        except requests.HTTPError as e:
            print(f"  HTTP error: {e.response.status_code} {e.response.text[:300]}", file=sys.stderr)
            return 2
        except requests.RequestException as e:
            print(f"  request failed: {e}", file=sys.stderr)
            return 2

        print(f"  ingested={res['ingested']}  skipped={res['skipped']}  errors={res['errors']}")

        # Show first few errors if any
        if res.get("errors", 0) > 0:
            for d in res.get("details", [])[:3]:
                if "error" in d:
                    print(f"    err: {d.get('document_id', '?')}: {d['error'][:150]}")

        s = status()
        print(f"  state: {fmt_status(s)}")

        if s["documents_chunked"] >= target:
            print("\nALL DONE — every eligible document is chunked.")
            print(f"final: {fmt_status(s)}")
            if s.get("documents_embedded", 0) == 0:
                print("\nNote: documents_embedded=0 means VOYAGE_API_KEY isn't configured —")
                print("the system is using hash-vector fallback. Search still works via tsvector,")
                print("but recall will improve significantly once you add a Voyage key and re-ingest.")
            return 0

        # Stuck detection: if no progress for 2 batches, bail.
        if s["documents_chunked"] == last_chunked:
            stuck_iterations += 1
            if stuck_iterations >= 2:
                print(f"\nNO PROGRESS after {stuck_iterations} batches — stopping.")
                print(f"final: {fmt_status(s)}")
                print(f"remaining unchunked: {target - s['documents_chunked']}")
                return 3
        else:
            stuck_iterations = 0
            last_chunked = s["documents_chunked"]

        time.sleep(SLEEP_BETWEEN)


if __name__ == "__main__":
    sys.exit(main())
