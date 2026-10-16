"""Summarize a meeting transcript with Claude.

Produces a structured JSON summary with timestamps so the UI can render
clickable jump points into the audio/video player.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import anthropic

from services.ai import pin

logger = logging.getLogger(__name__)

# Model comes from the "meeting_summaries" feature's role (Admin → AI).


def _system_prompt() -> str:
    from municipality import get_municipality
    muni = get_municipality()
    # str.replace, not .format — the prompt contains literal JSON braces.
    return SYSTEM_PROMPT.replace("{place}", f"{muni.short_name}, {muni.state}")


SYSTEM_PROMPT = """You are a municipal-meeting analyst summarizing a recorded public meeting of a local government ({place}).

Output STRICT JSON only, matching this schema:
{
  "tldr": "2-3 sentence plain-language summary",
  "decisions": [{"description": "...", "vote": "5-0" | "passed" | "tabled" | "...", "timestamp_seconds": 0}],
  "action_items": [{"description": "...", "owner": "name or role", "due": "date or null"}],
  "topics": [{"title": "...", "summary": "1-2 sentences", "start_seconds": 0, "end_seconds": 0}],
  "public_comments": [{"speaker": "name if stated, else 'Resident'", "topic": "...", "timestamp_seconds": 0}],
  "ordinances_resolutions": [{"number": "...", "description": "...", "outcome": "adopted|introduced|tabled"}]
}

Rules:
- timestamp_seconds MUST be a float number of seconds from the start of the meeting, derived from the [HH:MM:SS] markers in the input. Never invent timestamps.
- If a field has no content for this meeting, return an empty array (not omit).
- Be terse. Decisions should be one line. Topics should each be 1-2 sentences max.
- Do NOT include any text outside the JSON object."""


def _segments_to_prompt(segments: list[dict]) -> str:
    """Render segments as time-stamped lines. Caps total length so we don't
    blow the context window on a 2-hour meeting; we drop short filler lines
    when over the cap."""
    MAX_CHARS = 120_000  # ~30K tokens; leaves room for system + completion
    lines: list[str] = []
    total = 0
    for s in segments:
        start = float(s.get("start", 0))
        text = (s.get("text") or "").strip()
        if not text:
            continue
        h = int(start // 3600)
        m = int((start % 3600) // 60)
        sec = int(start % 60)
        line = f"[{h:02d}:{m:02d}:{sec:02d}] {text}"
        if total + len(line) > MAX_CHARS:
            lines.append("... [transcript truncated for length]")
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def summarize_meeting(
    *,
    meeting_body: str,
    meeting_date: Optional[str],
    segments: list[dict],
    raw_text: Optional[str] = None,
) -> dict:
    """Returns a dict matching the SYSTEM_PROMPT schema. Caller persists it."""
    route = pin("meeting_summaries", "anthropic")
    if route is None:
        raise RuntimeError("No anthropic key configured (Admin → AI)")

    if segments:
        transcript_block = _segments_to_prompt(segments)
    else:
        transcript_block = (raw_text or "")[:120_000]

    header = f"Meeting body: {meeting_body}\nMeeting date: {meeting_date or 'unknown'}"
    user_msg = f"{header}\n\n--- Transcript ---\n{transcript_block}"

    client = anthropic.Anthropic(api_key=route.key)
    resp = client.messages.create(
        model=route.model,
        max_tokens=4096,
        system=_system_prompt(),
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = "".join(
        getattr(block, "text", "") for block in resp.content
        if getattr(block, "type", "") == "text"
    ).strip()

    # Defensive — strip code fences if the model wraps the JSON.
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if m:
        raw = m.group(1)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("Claude returned non-JSON summary, attempting recovery: %s", e)
        # Last resort — pick the first {...} block.
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        raise RuntimeError(f"Could not parse summary JSON: {raw[:400]}")
