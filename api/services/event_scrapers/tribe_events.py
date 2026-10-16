"""WordPress "The Events Calendar" REST adapter.

Tribe Events exposes events at:
    {site}/wp-json/tribe/events/v1/events?per_page=50&start_date=YYYY-MM-DD

Response shape:
    {
      "events": [
        {
          "title": "Ray Tigre",
          "start_date": "2026-05-24 20:00:00",
          "start_date_details": {"hour": "20", "minutes": "00", ...},
          "end_date":   "2026-05-24 23:00:00",
          "url": "https://…/event/ray-tigre/",
          "excerpt": "…",
          "venue": { "venue": "Chubby Pickle" },  # may be empty
          ...
        }
      ]
    }

We trust the venue_name passed in (vs the API's `venue` field) so listings
without an attached venue object are still attributed correctly.
"""
from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 15
_PER_PAGE = 50
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; civic-lens-events/1.0; +https://github.com/kknechtel/civic-lens)",
    "Accept": "application/json",
}


def _parse_tribe_datetime(s: Optional[str]) -> Optional[datetime]:
    """Tribe Events posts naive local times like '2026-05-24 20:00:00'."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _format_time(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.strftime("%I:%M %p").lstrip("0")


def fetch_events(
    venue_name: str,
    city: str,
    site_base: str,
    start_date: Optional[date] = None,
) -> list[dict]:
    """Fetch upcoming events from a Tribe Events REST endpoint.

    `site_base` is the WordPress site origin (e.g.
    `https://thechubbypicklenj.com`). `start_date` filters out past
    events; defaults to today.
    """
    start = (start_date or date.today()).isoformat()
    base = site_base.rstrip("/")
    url = f"{base}/wp-json/tribe/events/v1/events?per_page={_PER_PAGE}&start_date={start}"

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.warning("[tribe:%s] fetch failed: %s", venue_name, exc)
        return []

    events = payload.get("events") or []
    if not events:
        logger.info("[tribe:%s] no events in response", venue_name)
        return []

    out: list[dict] = []
    for ev in events:
        start_dt = _parse_tribe_datetime(ev.get("start_date"))
        end_dt = _parse_tribe_datetime(ev.get("end_date"))
        if start_dt is None:
            continue

        title = (ev.get("title") or "").strip() or "Live music"
        ticket_url = ev.get("url") or None
        # Strip HTML tags from excerpts the cheap way — Tribe ships excerpts
        # wrapped in <p>…</p>. For richer descriptions look at ev["description"].
        excerpt = (ev.get("excerpt") or "").strip()
        if excerpt.startswith("<") and excerpt.endswith(">"):
            import re
            excerpt = re.sub(r"<[^>]+>", "", excerpt).strip()

        out.append({
            "date": start_dt.date().isoformat(),
            "title": title,
            "time": _format_time(start_dt),
            "end_time": _format_time(end_dt),
            "venue": venue_name,
            "city": city,
            "location": f"{venue_name}, {city}",
            "event_type": "live_music",
            "source": f"tribe:{venue_name}",
            "url": ticket_url,
            "ticket_url": ticket_url,
            "description": excerpt or None,
        })

    logger.info("[tribe:%s] parsed %d events", venue_name, len(out))
    return out
