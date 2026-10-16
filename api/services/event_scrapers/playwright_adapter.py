"""Playwright adapter — renders JS-heavy venue pages and parses the
post-render DOM.

Use this only for venues that fail with requests + BeautifulSoup
because their events are injected client-side (Chubby Pickle's
JetEngine widget, Donovan's Reef BeatGig embed).

Playwright's sync API refuses to run inside an asyncio event loop
("Please use the Async API instead"). scheduled_scrape.py runs the
whole batch under asyncio.run(), so we offload the actual
sync_playwright call into a dedicated worker thread — a thread with
no event loop = sync_playwright is happy. This costs ~one thread per
playwright venue per scrape and keeps the rest of the scraper sync.

Per-venue parsers in this module:
  parse_chubby_pickle_dom   — JetEngine event-list grid
  parse_donovans_dom        — BeatGig venue-calendar embed

Each parser takes the rendered HTML and the current year; returns a
list of partial event dicts. The shared `fetch_events` wrapper drives
Playwright, hands the rendered HTML to the parser, and normalizes the
output into our standard event dict.

Install on prod:
    /opt/atlantic-highlands/api/venv/bin/playwright install --with-deps chromium

That step is idempotent. The browser binary lives in
~/.cache/ms-playwright on the host running it (so under the
ah-scraper systemd user's HOME).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Callable, Optional

from bs4 import BeautifulSoup

from .html_parse import (
    _MONTHS, _safe_date, _normalize_time, normalize_time_range,
)

logger = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 25_000      # hard cap on page load
SETTLE_WAIT_MS = 4_000         # extra wait after load for JS-only events
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)


ParserFn = Callable[[str, int], list[dict]]

_CAPTION_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{4})\b", re.I)


def _calendar_caption_month(soup, today, venue_label: str) -> tuple[int, int]:
    """Read the month a calendar grid is displaying, as (month, year).

    Month-grid widgets render one month and caption it, but the day cells
    carry only a number. Both venues using this adapter previously guessed
    the month from today's date instead, which silently relabelled the
    start of the displayed month and produced different dates depending on
    the day the scrape ran. Read the caption; guess only as a last resort,
    and log when we do.
    """
    caption = soup.select_one(".jet-calendar-caption__name, [class*='caption']")
    text = caption.get_text(" ", strip=True) if caption else soup.get_text(" ", strip=True)
    m = _CAPTION_RE.search(text)
    if m:
        month = _MONTHS.get(m.group(1).lower())
        if month:
            return month, int(m.group(2))
    logger.warning("[playwright:%s] no month caption found; falling back to "
                   "the current month — dates may be wrong", venue_label)
    return today.month, today.year


def _render_page_inner(url: str, wait_for_selector: Optional[str] = None) -> Optional[str]:
    """The actual sync_playwright work. MUST run on a thread that has no
    asyncio event loop — see module docstring for why."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed; cannot render %s", url)
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 1800})
                page = context.new_page()
                page.set_default_timeout(PAGE_TIMEOUT_MS)
                # domcontentloaded, not networkidle: pages with a polling
                # widget never go idle. Drifthouse embeds OpenTable and
                # timed out every run on prod (25s) while working locally
                # only because the local probe used domcontentloaded. The
                # selector wait plus SETTLE_WAIT_MS below already cover
                # client-side rendering, which is the actual requirement.
                page.goto(url, wait_until="domcontentloaded")
                if wait_for_selector:
                    try:
                        page.wait_for_selector(wait_for_selector, timeout=PAGE_TIMEOUT_MS)
                    except Exception as exc:
                        logger.info("[playwright] selector %r not found on %s: %s",
                                    wait_for_selector, url, exc)
                page.wait_for_timeout(SETTLE_WAIT_MS)
                return page.content()
            finally:
                browser.close()
    except Exception as exc:
        logger.warning("[playwright] render failed for %s: %s", url, exc)
        return None


def _render_page(url: str, wait_for_selector: Optional[str] = None) -> Optional[str]:
    """Public entry — runs the sync Playwright call on a worker thread so it
    doesn't trip on the caller's asyncio loop (scheduled_scrape.py runs
    under asyncio.run). Returns the rendered HTML or None on failure."""
    import concurrent.futures
    # max_workers=1 + a fresh executor per call guarantees a brand-new thread
    # with no inherited event loop. Cheaper than spawning a subprocess and
    # avoids the Playwright import cost on every call.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_render_page_inner, url, wait_for_selector)
        try:
            # Slightly more than PAGE_TIMEOUT_MS to give the inner function
            # time to log + return None on its own timeout rather than us
            # killing the thread mid-render.
            return future.result(timeout=(PAGE_TIMEOUT_MS / 1000) + 15)
        except concurrent.futures.TimeoutError:
            logger.warning("[playwright] thread timeout on %s", url)
            return None


def _render_paginated_inner(
    url: str,
    next_selector: str,
    steps: int = 8,
    wait_between_ms: int = 1500,
    wait_for_selector: Optional[str] = None,
) -> list[str]:
    """Load the page, snapshot the DOM, click `next_selector`, wait, snapshot
    again — repeat up to `steps` times. Returns one HTML string per snapshot
    (current + each subsequent click). For sites like Sandbox where future
    weeks are only rendered after a JS click."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed; cannot render %s", url)
        return []

    snapshots: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 1800})
                page = context.new_page()
                page.set_default_timeout(PAGE_TIMEOUT_MS)
                # domcontentloaded, not networkidle: pages with a polling
                # widget never go idle. Drifthouse embeds OpenTable and
                # timed out every run on prod (25s) while working locally
                # only because the local probe used domcontentloaded. The
                # selector wait plus SETTLE_WAIT_MS below already cover
                # client-side rendering, which is the actual requirement.
                page.goto(url, wait_until="domcontentloaded")
                if wait_for_selector:
                    try:
                        page.wait_for_selector(wait_for_selector, timeout=PAGE_TIMEOUT_MS)
                    except Exception:
                        pass
                page.wait_for_timeout(SETTLE_WAIT_MS)
                snapshots.append(page.content())

                for i in range(steps):
                    el = page.query_selector(next_selector)
                    if not el:
                        logger.info("[playwright] no more next-button at step %d on %s", i, url)
                        break
                    try:
                        el.click()
                    except Exception as exc:
                        logger.info("[playwright] click failed at step %d on %s: %s", i, url, exc)
                        break
                    page.wait_for_timeout(wait_between_ms)
                    snapshots.append(page.content())
                return snapshots
            finally:
                browser.close()
    except Exception as exc:
        logger.warning("[playwright] paginated render failed for %s: %s", url, exc)
        return snapshots  # whatever we got before the crash


def _render_paginated(
    url: str,
    next_selector: str,
    steps: int = 8,
    wait_between_ms: int = 1500,
    wait_for_selector: Optional[str] = None,
) -> list[str]:
    """Threaded wrapper for _render_paginated_inner (see _render_page docstring
    for the asyncio rationale)."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            _render_paginated_inner, url, next_selector, steps, wait_between_ms, wait_for_selector,
        )
        # Budget = full render + (steps * settle) + buffer
        budget = (PAGE_TIMEOUT_MS / 1000) + 15 + (steps * wait_between_ms / 1000) + 10
        try:
            return future.result(timeout=budget)
        except concurrent.futures.TimeoutError:
            logger.warning("[playwright] paginated thread timeout on %s", url)
            return []


def fetch_events_paginated(
    venue_name: str,
    city: str,
    url: str,
    parser: ParserFn,
    next_selector: str,
    steps: int = 8,
    wait_between_ms: int = 1500,
    wait_for_selector: Optional[str] = None,
    year_context: Optional[int] = None,
) -> list[dict]:
    """Multi-snapshot variant for sites that only render one page-slice at
    a time (Sandbox shows one week per click). Runs `parser` on every
    snapshot and dedupes by (date, title)."""
    snapshots = _render_paginated(
        url=url, next_selector=next_selector, steps=steps,
        wait_between_ms=wait_between_ms, wait_for_selector=wait_for_selector,
    )
    yr = year_context or date.today().year
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for snap in snapshots:
        try:
            partials = parser(snap, yr)
        except Exception as exc:
            logger.exception("[playwright:%s] parser raised on snapshot: %s", venue_name, exc)
            continue
        for p in partials:
            if not p.get("date") or not p.get("title"):
                continue
            key = (p["date"], p["title"].lower())
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "date": p["date"],
                "title": p["title"],
                "time": p.get("time"),
                "end_time": p.get("end_time"),
                "venue": venue_name,
                "city": city,
                "location": f"{venue_name}, {city}",
                "event_type": "live_music",
                "source": f"playwright:{venue_name}",
                "url": p.get("url") or url,
                "ticket_url": p.get("url"),
                "description": p.get("description"),
            })
    logger.info("[playwright-paginated:%s] %d snapshots → %d unique events",
                venue_name, len(snapshots), len(out))
    return out


def fetch_events(
    venue_name: str,
    city: str,
    url: str,
    parser: ParserFn,
    wait_for_selector: Optional[str] = None,
    year_context: Optional[int] = None,
) -> list[dict]:
    html = _render_page(url, wait_for_selector=wait_for_selector)
    if not html:
        return []
    yr = year_context or date.today().year
    try:
        partials = parser(html, yr)
    except Exception as exc:
        logger.exception("[playwright:%s] parser raised: %s", venue_name, exc)
        return []

    out: list[dict] = []
    for p in partials:
        if not p.get("date") or not p.get("title"):
            continue
        out.append({
            "date": p["date"],
            "title": p["title"],
            "time": p.get("time"),
            "end_time": p.get("end_time"),
            "venue": venue_name,
            "city": city,
            "location": f"{venue_name}, {city}",
            "event_type": "live_music",
            "source": f"playwright:{venue_name}",
            "url": p.get("url") or url,
            "ticket_url": p.get("url"),
            "description": p.get("description"),
        })
    logger.info("[playwright:%s] parsed %d events", venue_name, len(out))
    return out


# ── Per-venue parsers ────────────────────────────────────────────────

def parse_chubby_pickle_dom(html: str, year: int) -> list[dict]:
    """Chubby Pickle's calendar is a JetEngine Calendar widget. Post-render
    each visible day is a .jet-calendar-week__day-wrap div containing:
      - .jet-calendar-week__day-date  (text: "26", "27", …)
      - .jet-calendar-week__day-event (one per event on that day)
          inner text: "Title | 8:30 PM | - 11:59 PM"

    Day numbers wrap across month boundaries (visible week might show
    28, 29, 30, 1, 2, 3, 4). We assume current month for day >= today's
    day, next month for day < today's day. Year follows the month.
    """
    soup = BeautifulSoup(html, "html.parser")
    for s in soup.find_all(["svg", "style", "script"]):
        s.decompose()

    events: list[dict] = []
    seen: set[tuple[str, str]] = set()
    today = date.today()
    time_re = re.compile(
        r"\d{1,2}(?::\d{2})?\s*[APap][Mm](?:\s*[-–—]\s*\d{1,2}(?::\d{2})?\s*[APap][Mm])?",
    )

    # Same shape as Donovan's: the grid renders one month, captions it
    # ("August 2026" in .jet-calendar-caption__name), and the day cells
    # carry only a number.
    #
    # This previously guessed the month from today's date — current month
    # unless `day < today.day - 7`, then next. That relabelled the start of
    # the displayed month, and because the cutoff moves daily the same show
    # landed on different dates run to run. On Donovan's, which had the
    # identical code, it put a month of July shows into August and left a
    # dozen conflicting rows in prod before anyone noticed.
    base_month, base_year = _calendar_caption_month(soup, today, "chubby pickle")

    for wrap in soup.select(".jet-calendar-week__day-wrap"):
        day_el = wrap.select_one(".jet-calendar-week__day-date")
        if not day_el:
            continue
        day_text = day_el.get_text(strip=True)
        if not day_text.isdigit():
            continue
        day = int(day_text)
        d = _safe_date(base_year, base_month, day)
        if not d:
            continue

        for ev_div in wrap.select(".jet-calendar-week__day-event"):
            text = ev_div.get_text(" | ", strip=True)
            if not text:
                continue
            parts = [p.strip() for p in text.split("|") if p.strip()]
            if not parts:
                continue
            # Bug fix 2026-05-24: the earlier version only excluded the
            # FIRST time-shaped part, so "Ray Tigre | 8:30 PM | - 11:59 PM"
            # produced title="Ray Tigre - 11:59 PM" with the end-time
            # smushed in. Strip every time-shaped or leading-dash-time
            # part from title; use the first one as start_time, second as
            # end_time.
            time_part_re = re.compile(
                r"^\s*[-–—]?\s*\d{1,2}(?::\d{2})?\s*[APap][Mm]\s*$"
            )
            title_parts = []
            times: list[str] = []
            for p in parts:
                if time_part_re.match(p):
                    tm = time_re.search(p)
                    if tm:
                        times.append(_normalize_time(tm.group(0)))
                    continue
                title_parts.append(p)
            time_str = times[0] if times else None
            end_time_str = times[1] if len(times) > 1 else None
            title = " ".join(title_parts).strip()
            if not title or len(title) < 2:
                continue
            key = (d, title.lower())
            if key in seen:
                continue
            seen.add(key)
            events.append({
                "date": d, "title": title, "time": time_str,
                "end_time": end_time_str,
            })
    return events


def parse_donovans_dom(html: str, year: int) -> list[dict]:
    """Donovan's Reef calendar (post-render) uses Squarespace's events
    widget — NOT BeatGig as the venue's embed suggested. Diagnostic
    confirmed:
      .marker                  31 instances, text "Fri | 1" (day name | num)
      .flyoutitem-datetime    108 instances (3x per event: 12hr/24hr/local)
                              text "6:00 PM – 10:00 PM"

    Each marker represents one date (day-of-month integer); events for
    that date attach as siblings/children. The reliable extractor walks
    `.marker` divs and then for each looks at the immediate area for
    flyoutitem groups containing title + datetime.

    The flyoutitem text we have so far gives us only times — title
    extraction needs to look for an event-title element near each
    datetime. Falls back to a generic page-text regex over date
    headings + nearby title text if that doesn't work, so we always
    return SOMETHING.
    """
    soup = BeautifulSoup(html, "html.parser")
    for s in soup.find_all(["svg", "style", "script"]):
        s.decompose()

    events: list[dict] = []
    seen: set[tuple[str, str]] = set()
    today = date.today()
    time_re = re.compile(
        r"\d{1,2}(?::\d{2})?\s*[APap][Mm](?:\s*[-–—]\s*\d{1,2}(?::\d{2})?\s*[APap][Mm])?",
    )

    # The grid renders ONE month and captions it ("August 2026"), while the
    # markers carry only a day number. Read that caption.
    #
    # This used to be inferred from today's date — current month unless
    # `day < today.day - 7`, then next month. That silently relabelled the
    # first days of the displayed month: scraped on the 14th, Aug 1-6 came
    # out as Sep 1-6, and because the cutoff moves with the calendar the
    # same show landed on different dates depending on the day we ran. It
    # left prod holding two date-conflicting copies of a dozen Donovan's
    # shows.
    base_month, base_year = _calendar_caption_month(soup, today, "donovans")

    # Each marker has its parent that contains the day's events
    for marker in soup.select(".marker"):
        # Marker text is like "Fri | 1" — read the day number
        text = marker.get_text(" ", strip=True)
        day_match = re.search(r"\b(\d{1,2})\b", text)
        if not day_match:
            continue
        day = int(day_match.group(1))
        if not (1 <= day <= 31):
            continue
        d = _safe_date(base_year, base_month, day)
        if not d:
            continue

        # Walk up to a reasonable container — try parent then grandparent.
        container = marker.parent
        for _ in range(3):
            if container is None:
                break
            classes = " ".join(container.get("class") or [])
            # Heuristic: stop when we reach something that looks like a
            # day row or eventlist item
            if any(k in classes for k in ("day", "item", "eventlist", "event")):
                break
            container = container.parent
        if container is None:
            continue

        # Inside the container, look for title + time pairs.
        # Squarespace event widget commonly uses .eventlist-title or h3 for title.
        titles = container.select(
            ".eventlist-title, .flyoutitem-title, .event-title, h3, h4"
        )
        times = container.select(".flyoutitem-datetime--12hr, .eventlist-meta-date, time")

        # Pair them by index — fragile but the cleanest first cut
        for idx, title_el in enumerate(titles):
            title = title_el.get_text(" ", strip=True)
            if not title or len(title) < 2:
                continue
            time_str: Optional[str] = None
            end_time_str: Optional[str] = None
            if idx < len(times):
                tm = time_re.search(times[idx].get_text(" ", strip=True))
                if tm:
                    time_str, end_time_str = normalize_time_range(tm.group(0))
            key = (d, title.lower())
            if key in seen:
                continue
            seen.add(key)
            events.append({"date": d, "title": title,
                           "time": time_str, "end_time": end_time_str})

    return events
