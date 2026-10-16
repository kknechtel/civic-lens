"""iCalendar (.ics) feed adapter — the most portable event source.

Library calendars (LibraryCalendar, LibCal), CivicPlus/Granicus calendars,
Google Calendar public feeds and most WordPress event plugins publish iCal.

Profile entry:
    - name: Long Beach Public Library
      city: Long Beach
      adapter: ical
      params: {url: https://longbeachpl.librarycalendar.com/events/feed/ical, event_type: community}
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

import requests

from services.scraper.config import USER_AGENT

logger = logging.getLogger(__name__)
_TIMEOUT = 30


def _unfold(text: str) -> list[str]:
    """RFC 5545 §3.1: a line starting with space/tab continues the previous one."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(v: str) -> str:
    return (v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
             .replace("\\;", ";").replace("\\\\", "\\")).strip()


def _parse_dt(value: str) -> tuple[Optional[date], Optional[str]]:
    """'20261020T180000' → (date, '6:00 PM'); '20261020' → (date, None).
    Times are taken as written (TZID local time); trailing Z is treated as local too —
    good enough for a community calendar display."""
    m = re.match(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2}))?", value or "")
    if not m:
        return None, None
    d = date(int(m[1]), int(m[2]), int(m[3]))
    if m[4] is None:
        return d, None
    hh, mm = int(m[4]), int(m[5])
    return d, f"{(hh % 12) or 12}:{mm:02d} {'AM' if hh < 12 else 'PM'}"


def parse_ics(text: str) -> list[dict]:
    events, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, _, value = line.partition(":")
            name = key.split(";", 1)[0].upper()
            cur.setdefault(name, value)
    return events


def fetch_events(
    venue_name: str,
    city: str,
    url: str,
    event_type: str = "community",
    days_ahead: int = 120,
) -> list[dict]:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("[ical:%s] fetch failed: %s", venue_name, exc)
        return []

    today = datetime.now().date()
    horizon = today + timedelta(days=days_ahead)
    out = []
    for ev in parse_ics(resp.text):
        start, start_time = _parse_dt(ev.get("DTSTART", ""))
        if not start or start < today or start > horizon:
            continue
        _, end_time = _parse_dt(ev.get("DTEND", ""))
        title = _unescape(ev.get("SUMMARY", ""))
        if not title:
            continue
        out.append({
            "date": start.isoformat(),
            "title": title[:300],
            "time": start_time,
            "end_time": end_time,
            "location": _unescape(ev.get("LOCATION", "")) or None,
            "description": _unescape(ev.get("DESCRIPTION", ""))[:2000] or None,
            "url": ev.get("URL") or url,
            "venue": venue_name,
            "city": city,
            "event_type": event_type,
            "source": "ical",
        })
    logger.info("[ical:%s] %d upcoming events", venue_name, len(out))
    return out
