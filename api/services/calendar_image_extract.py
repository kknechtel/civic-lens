"""Pull events out of a venue's monthly calendar image.

Use case: Gaslight (and any other FB-only / IG-only venue) posts a
monthly schedule as a single graphic — calendar grid, list, or flyer.
We can't scrape the underlying data because there isn't any; we just
have pixels. Claude Sonnet's vision is reliable at reading these and
returning structured JSON.

Workflow:
  1. Admin uploads the image via /admin/event-ingest/parse
  2. extract_events() base64-encodes it and asks Claude to return JSON
     in our standard event shape ({date, time, title})
  3. Admin reviews/edits the parsed events in the UI
  4. /admin/event-ingest/commit inserts the approved rows

We deliberately keep this two-step (parse → review → commit). OCR/LLM
extraction on calendar images is good but not infallible (band names
get truncated, AM/PM gets dropped, days line up wrong on busy grids).
A 5-second human glance prevents bad rows from polluting the calendar.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from datetime import date, datetime
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

# Sonnet has the best price/perf for vision-heavy structured extraction.
# Opus would also work but is overkill for OCR-and-format.
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

# Cap input size — most monthly calendar images are well under 2 MB.
# Anything bigger gets rejected before we even hit the API.
MAX_IMAGE_BYTES = 6 * 1024 * 1024

ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
}


_SYSTEM_PROMPT = """\
You extract live-music events from a venue's monthly calendar image.

Return ONLY a JSON object with this exact shape — no prose, no code fences:
{
  "events": [
    {
      "date": "YYYY-MM-DD",
      "time": "8:00 PM",      // optional, omit or null if not shown
      "end_time": "11:00 PM", // optional, omit or null if not shown
      "title": "Band Name"     // act / band / event title as shown
    },
    ...
  ],
  "notes": "freeform observations (illegible cell, ambiguous date, etc.)"
}

Rules:
- One row per scheduled act. If a day has two acts, emit two rows.
- "date" MUST be a full ISO date. The image typically shows month + day-of-week
  + day-of-month; infer the year from the visible month header. If the year is
  not visible, assume the next occurrence of that month from today.
- Skip days that are clearly empty / closed / "private event" / "no music".
- Skip generic week-recurring fillers ("Trivia Tues") unless the image shows
  them as scheduled music acts.
- Preserve band names exactly as written, including ampersands and punctuation.
  Don't normalize "&" to "and" or vice-versa.
- If the image is not a calendar or you can't parse it, return
  {"events": [], "notes": "explanation"}.
"""


def _user_prompt(venue: str, hint_month: Optional[str]) -> str:
    bits = [
        f"This is a monthly music calendar for {venue}.",
    ]
    if hint_month:
        bits.append(f"The admin says this is for: {hint_month}.")
    bits.append("Extract all acts and return JSON as instructed.")
    return " ".join(bits)


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.search(text)
    return m.group(1) if m else text.strip()


def _normalize_date(d: str) -> Optional[str]:
    """Accept 'YYYY-MM-DD' or a few near-misses Claude sometimes emits.
    Returns ISO YYYY-MM-DD or None if it can't be parsed."""
    if not d:
        return None
    s = d.strip()
    # Already ISO
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        pass
    # M/D/YYYY or MM/DD/YYYY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return None
    return None


def extract_events(
    image_bytes: bytes,
    mime_type: str,
    venue: str,
    hint_month: Optional[str] = None,
) -> dict:
    """Send the image to Claude Vision, return {events, notes}.

    Raises RuntimeError on config / size / type problems so the route
    can map to a clean 4xx. Per-event parse glitches are silently
    dropped and surfaced in `notes`.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    if mime_type not in ALLOWED_MIME_TYPES:
        raise RuntimeError(f"unsupported image type: {mime_type}")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise RuntimeError(
            f"image too large: {len(image_bytes)} bytes "
            f"(max {MAX_IMAGE_BYTES})"
        )

    b64 = base64.b64encode(image_bytes).decode("ascii")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": mime_type, "data": b64,
                }},
                {"type": "text", "text": _user_prompt(venue, hint_month)},
            ],
        }],
    )

    raw = "".join(
        getattr(block, "text", "") for block in resp.content
        if getattr(block, "type", "") == "text"
    ).strip()
    body = _strip_fences(raw)

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.warning("calendar_image_extract: model returned non-JSON: %s", raw[:400])
        raise RuntimeError(f"model did not return JSON: {exc}")

    events_in = parsed.get("events") or []
    notes = parsed.get("notes") or ""

    cleaned: list[dict] = []
    dropped = 0
    for ev in events_in:
        if not isinstance(ev, dict):
            dropped += 1
            continue
        d = _normalize_date(str(ev.get("date") or ""))
        title = str(ev.get("title") or "").strip()
        if not d or not title or len(title) < 2:
            dropped += 1
            continue
        cleaned.append({
            "date": d,
            "title": title,
            "time": (str(ev.get("time")).strip() if ev.get("time") else None),
            "end_time": (str(ev.get("end_time")).strip() if ev.get("end_time") else None),
        })
    cleaned.sort(key=lambda e: (e["date"], e.get("time") or ""))

    return {
        "events": cleaned,
        "notes": notes,
        "dropped": dropped,
        "model": MODEL,
    }
