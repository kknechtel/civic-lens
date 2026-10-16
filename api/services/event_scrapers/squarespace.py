"""Squarespace events-collection adapter.

Squarespace exposes any collection page as JSON by appending
`?format=json-pretty`. For an Events Collection (recordType 12) the
response is:

    {
      "upcoming": [
        {
          "id": "...",
          "title": "Ray Tigre",
          "urlId": "ray-tigre",
          "startDate": 1748073600000,   # Unix ms
          "endDate":   1748081400000,
          "body": "<p>Live music…</p>",
          "recordType": 12,
          ...
        }
      ],
      "past":     [ ... ],
      "calendarLayoutItems": { ... }
    }

We only read `upcoming[]`. Past events would just churn the dedupe key
without surfacing anywhere useful.

The earlier version of this file used `?format=json` and read `items[]`,
which is the shape Squarespace returns for blog/portfolio collections,
not Events. That's why every Squarespace venue returned 0 events on the
first prod run.

Failures here never raise — they return [] and log. The runner treats
empty as "venue had no events" rather than "venue is broken".
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 15
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; civic-lens-events/1.0; +https://github.com/kknechtel/civic-lens)",
    "Accept": "application/json, text/plain, */*",
}

# US Eastern Time offset for display purposes only. We render the time as a
# plain string ("8:00 PM") rather than try to store TZ-aware data — venues
# all post in local time and DST behaviour matches the borough's wall clock.
_ET_OFFSET_HOURS_DEFAULT = -5  # winter; the date itself is correct either way

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = None


def _to_local(ts_ms: int) -> Optional[datetime]:
    if not ts_ms:
        return None
    try:
        utc = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
        if _ET is not None:
            return utc.astimezone(_ET)
        return utc  # fall back; date will still be correct, time may be off
    except Exception:
        return None


def _format_time(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    s = dt.strftime("%I:%M %p").lstrip("0")
    return s


def fetch_events(
    venue_name: str,
    city: str,
    collection_url: str,
    site_base: Optional[str] = None,
) -> list[dict]:
    """Fetch an event list from a Squarespace events collection.

    `collection_url` is the full URL to the events page, e.g.
    `https://www.theprovingground.com/events`. `site_base` is the site's
    origin, used to expand the `fullUrl` field into an absolute link.
    Defaults to the origin of `collection_url` if not given.
    """
    if not site_base:
        from urllib.parse import urlparse
        parsed = urlparse(collection_url)
        site_base = f"{parsed.scheme}://{parsed.netloc}"

    join_char = "&" if "?" in collection_url else "?"
    url = f"{collection_url}{join_char}format=json-pretty"

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.warning("[squarespace:%s] fetch failed: %s", venue_name, exc)
        return []

    # Squarespace Events Collections return `upcoming` + `past` at the top
    # level. Non-Events collections return `items[]` — when we see that, we
    # treat it as "this URL isn't an events collection" and bail.
    items = payload.get("upcoming")
    if items is None:
        if "items" in payload:
            logger.info("[squarespace:%s] URL is not an Events collection (got items[]) — skipping", venue_name)
        else:
            logger.info("[squarespace:%s] no `upcoming` array in response", venue_name)
        return []
    if not items:
        logger.info("[squarespace:%s] 0 upcoming events", venue_name)
        return []

    out: list[dict] = []
    for it in items:
        start_ms = it.get("startDate")
        end_ms = it.get("endDate")
        start = _to_local(start_ms)
        end = _to_local(end_ms)
        if start is None:
            continue

        title = (it.get("title") or "").strip() or "Live music"
        # Events Collection items expose `fullUrl` (when set) or `urlId`
        # (slug). The collection URL minus the host gives the parent path;
        # append the slug for the per-event detail page.
        from urllib.parse import urlparse
        coll_path = urlparse(collection_url).path.rstrip("/")
        full_url = it.get("fullUrl") or (f"{coll_path}/{it['urlId']}" if it.get("urlId") else "")
        ticket_url = urljoin(site_base, full_url) if full_url else None

        out.append({
            "date": start.date().isoformat(),
            "title": title,
            "time": _format_time(start),
            "end_time": _format_time(end),
            "venue": venue_name,
            "city": city,
            "location": f"{venue_name}, {city}",
            "event_type": "live_music",
            "source": f"squarespace:{venue_name}",
            "url": ticket_url,
            "ticket_url": ticket_url,
            "description": (it.get("excerpt") or "").strip() or None,
        })

    logger.info("[squarespace:%s] parsed %d events", venue_name, len(out))
    return out
