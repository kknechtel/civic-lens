#!/usr/bin/env python3
"""
Drive the OCR cascade until every PDF in the corpus has extracted text.

Calls /api/extraction/run repeatedly in batches until /api/extraction/status
reports pdfs_needing_ocr == 0. Each batch:
  1. Downloads the PDF from S3
  2. Tries pdfplumber (free, instant)
  3. Falls through to Gemini Vision OCR for scanned PDFs (~$0.0015/page)
  4. Saves new extracted_text + page_count
  5. Auto-chunks + embeds for chat / search

Usage:
  AH_API=https://api.example.org  AH_TOKEN=$(curl ...login)  python api/scripts/run_ocr.py

Env vars:
  AH_API     base URL              (default http://localhost:8000)
  AH_TOKEN   admin JWT             (required)
  AH_BATCH   docs per call         (default 20 — Tesseract is fast)
  AH_SLEEP   seconds between calls (default 1)
"""
import os
import sys
import time
from urllib.parse import urljoin

import requests

API_BASE = os.environ.get("AH_API", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("AH_TOKEN")
BATCH = int(os.environ.get("AH_BATCH", "20"))
SLEEP_BETWEEN = float(os.environ.get("AH_SLEEP", "1"))

if not TOKEN:
    print("ERROR: set AH_TOKEN to an admin JWT", file=sys.stderr)
    sys.exit(1)


def hdrs():
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def status() -> dict:
    r = requests.get(urljoin(API_BASE + "/", "api/extraction/status"),
                     headers=hdrs(), timeout=30)
    r.raise_for_status()
    return r.json()


def run_batch(limit: int) -> dict:
    r = requests.post(
        urljoin(API_BASE + "/", f"api/extraction/run?limit={limit}"),
        headers=hdrs(),
        # Each doc can take 30-90s with Gemini Vision on a multi-page scan,
        # batch of 5 may need a long timeout.
        timeout=1800,
    )
    r.raise_for_status()
    return r.json()


def fmt(s: dict) -> str:
    return (
        f"docs_total={s['documents_total']}  "
        f"pdfs={s['pdfs_total']}  "
        f"with_text={s['pdfs_with_text']}  "
        f"needs_ocr={s['pdfs_needing_ocr']}"
    )


def main() -> int:
    print(f"API: {API_BASE}")
    s = status()
    print(f"start: {fmt(s)}")
    if s["pdfs_needing_ocr"] == 0:
        print("nothing to OCR")
        return 0

    iteration = 0
    last = s["pdfs_needing_ocr"]
    stuck = 0
    total_ok = 0
    total_cost = 0.0

    while True:
        iteration += 1
        print(f"\n[batch {iteration}] running OCR (limit={BATCH})…")
        try:
            res = run_batch(BATCH)
        except requests.HTTPError as e:
            print(f"  HTTP error: {e.response.status_code} {e.response.text[:300]}",
                  file=sys.stderr)
            return 2
        except requests.RequestException as e:
            print(f"  request failed: {e}", file=sys.stderr)
            return 2

        print(f"  ok={res['ok']} skipped={res['skipped']} errors={res['errors']}")
        for d in res.get("details", []):
            cost = d.get("estimated_cost", 0)
            total_cost += cost
            if d.get("error"):
                print(f"    ERR  {d.get('filename', '?')[:60]}: {d['error'][:80]}")
            elif d.get("skipped"):
                print(f"    SKIP {d.get('filename', '?')[:60]}: {d.get('reason', '?')}")
            else:
                tier = d.get("tier", "?")
                chars = d.get("chars", 0)
                pages = d.get("page_count", 0)
                ms = d.get("elapsed_ms", 0)
                print(f"    OK   [{tier:14}] {pages:3}p {chars:6}c {ms:6}ms ${cost:.4f}  {d.get('filename', '?')[:55]}")
        total_ok += res["ok"]

        s = status()
        print(f"  state: {fmt(s)}  · total_cost so far: ${total_cost:.4f}")

        if s["pdfs_needing_ocr"] == 0:
            print(f"\nDONE — all PDFs have extracted text.")
            print(f"final: {fmt(s)}  · total_cost: ${total_cost:.4f}")
            return 0

        if s["pdfs_needing_ocr"] == last:
            stuck += 1
            if stuck >= 3:
                print(f"\nSTUCK: {s['pdfs_needing_ocr']} docs aren't being processed "
                      f"(skipped or failing). Stopping.")
                return 3
        else:
            stuck = 0
            last = s["pdfs_needing_ocr"]

        time.sleep(SLEEP_BETWEEN)


if __name__ == "__main__":
    sys.exit(main())
