"""Bandsintown venue-page adapter.

Bandsintown's public REST API is gone (require app_id auth as of 2024).
But the venue page HTML at `bandsintown.com/v/{venue_id}-{slug}` is
SSR-rendered and embeds every upcoming show as schema.org `MusicEvent`
JSON-LD. Parsing those blocks needs no auth, just a browser User-Agent
(default `curl/*` returns 403 from their bot guard).

JSON-LD fields we care about:
  name        - band name
  startDate   - ISO 8601 with offset
  endDate     - optional
  url         - canonical event page
  location.name / location.address - venue name + address
  performer.name (or list) - artist
  offers.url  - ticket link
  eventStatus - schema.org/EventStatusType

We don't pass a venue name into the adapter — we use what Bandsintown
reports in `location.name` to keep our display consistent with the
caller's existing registry entry (e.g. "The Sandbox at Seastreak Beach"
even though we configure the adapter with "The Sandbox at Seastreak").
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_TIMEOUT = 20
_HEADERS = {
    # MUST be a real browser UA — default curl/python-requests returns 403.
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    # Python 3.10's fromisoformat doesn't accept trailing 'Z' before 3.11
    s = re.sub(r"Z$", "+00:00", s)
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _format_time(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    # Cast to local-ish by stripping tz — Bandsintown dates carry an offset
    # in venue-local time already, so the wall-clock numbers are what we want.
    return dt.strftime("%I:%M %p").lstrip("0")


def _extract_events_from_jsonld(jsonld: dict) -> list[dict]:
    """Yield events from a parsed JSON-LD blob. Bandsintown nests them
    either as a top-level @graph or as a list of MusicEvent objects."""
    out: list[dict] = []
    if not isinstance(jsonld, (dict, list)):
        return out

    candidates: list[dict] = []
    if isinstance(jsonld, list):
        candidates = [x for x in jsonld if isinstance(x, dict)]
    elif "@graph" in jsonld and isinstance(jsonld["@graph"], list):
        candidates = [x for x in jsonld["@graph"] if isinstance(x, dict)]
    else:
        candidates = [jsonld]

    for c in candidates:
        types = c.get("@type")
        types_list = [types] if isinstance(types, str) else (types or [])
        if not any(t in ("Event", "MusicEvent") for t in types_list):
            continue
        out.append(c)
    return out


def fetch_events(
    venue_name: str,
    city: str,
    venue_url: str,
) -> list[dict]:
    """Fetch + parse a Bandsintown venue page.

    `venue_url` example: https://www.bandsintown.com/v/10206567-the-sandbox-at-seastreak-beach
    """
    try:
        resp = requests.get(venue_url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("[bandsintown:%s] fetch failed: %s", venue_name, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    blocks = soup.find_all("script", type="application/ld+json")
    if not blocks:
        logger.info("[bandsintown:%s] no JSON-LD blocks", venue_name)
        return []

    raw_events: list[dict] = []
    for b in blocks:
        try:
            data = json.loads(b.string or "")
        except Exception:
            continue
        raw_events.extend(_extract_events_from_jsonld(data))

    if not raw_events:
        logger.info("[bandsintown:%s] no MusicEvent objects in JSON-LD", venue_name)
        return []

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for ev in raw_events:
        # Skip cancelled / postponed via schema.org status
        status = (ev.get("eventStatus") or "").rsplit("/", 1)[-1].lower()
        if status in ("eventcancelled", "eventpostponed"):
            continue

        start = _parse_iso(ev.get("startDate"))
        if start is None:
            continue

        # Performer = band name; fall back to event `name` field
        performer = ev.get("performer")
        title: Optional[str] = None
        if isinstance(performer, dict):
            title = performer.get("name")
        elif isinstance(performer, list) and performer:
            first = performer[0]
            title = first.get("name") if isinstance(first, dict) else str(first)
        if not title:
            title = ev.get("name")
        if not title:
            continue
        title = title.strip()

        end = _parse_iso(ev.get("endDate"))
        offers = ev.get("offers")
        ticket_url: Optional[str] = None
        if isinstance(offers, dict):
            ticket_url = offers.get("url")
        elif isinstance(offers, list) and offers:
            first = offers[0]
            if isinstance(first, dict):
                ticket_url = first.get("url")
        ticket_url = ticket_url or ev.get("url")

        date_str = start.date().isoformat()
        key = (date_str, title.lower())
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "date": date_str,
            "title": title,
            "time": _format_time(start),
            "end_time": _format_time(end),
            "venue": venue_name,
            "city": city,
            "location": f"{venue_name}, {city}",
            "event_type": "live_music",
            "source": f"bandsintown:{venue_name}",
            "url": ticket_url,
            "ticket_url": ticket_url,
            "description": None,
        })

    logger.info("[bandsintown:%s] parsed %d events", venue_name, len(out))
    return out
