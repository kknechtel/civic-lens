"""Admin event-ingest — turn a venue's calendar image into events.

Two endpoints, both admin-gated:

  POST /api/event-ingest/parse
    multipart: image, venue, city, hint_month (optional)
    → { events: [{date, time, end_time, title}], notes, dropped, model }

  POST /api/event-ingest/commit
    JSON:    { venue, city, events: [...], source }
    → { inserted, skipped }

The flow is deliberately two-step: parse returns a draft, the admin
reviews / edits in the UI, then commits. This catches OCR misreads
(band name truncation, day-of-week-only "date", time AM/PM swaps)
before they pollute calendar_events.

Inserted rows are tagged source="image_ocr:<venue>" so we can
distinguish them from scraper rows for auditing or rollback.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from auth import get_admin_user
from database import get_db
from models.user import User
from services.calendar_image_extract import (
    ALLOWED_MIME_TYPES, MAX_IMAGE_BYTES, extract_events,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Parse ─────────────────────────────────────────────────────────────

class ParsedEvent(BaseModel):
    date: str
    title: str
    time: str | None = None
    end_time: str | None = None


class ParseResponse(BaseModel):
    events: list[ParsedEvent]
    notes: str = ""
    dropped: int = 0
    model: str = ""


@router.post("/parse", response_model=ParseResponse)
async def parse_image(
    image: UploadFile = File(...),
    venue: str = Form(...),
    city: str = Form(""),
    hint_month: str = Form(""),
    admin: User = Depends(get_admin_user),
):
    """Read the calendar image with Claude Vision, return a draft list of
    events for the admin to review. Nothing is persisted here."""
    venue_clean = (venue or "").strip()
    if not venue_clean:
        raise HTTPException(400, "venue is required")

    mime = (image.content_type or "").lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(415, f"unsupported image type: {mime!r}; allowed: {sorted(ALLOWED_MIME_TYPES)}")

    body = await image.read()
    if not body:
        raise HTTPException(400, "image is empty")
    if len(body) > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"image too large ({len(body)} bytes)")

    try:
        result = extract_events(
            image_bytes=body,
            mime_type=mime,
            venue=venue_clean,
            hint_month=(hint_month.strip() or None),
        )
    except RuntimeError as exc:
        logger.warning("event-ingest parse failed for venue=%s: %s", venue_clean, exc)
        raise HTTPException(502, str(exc))

    return ParseResponse(
        events=[ParsedEvent(**e) for e in result["events"]],
        notes=result.get("notes", ""),
        dropped=int(result.get("dropped", 0)),
        model=result.get("model", ""),
    )


# ── Commit ────────────────────────────────────────────────────────────

class CommitRequest(BaseModel):
    venue: str
    city: str | None = None
    events: list[ParsedEvent] = Field(default_factory=list)
    # Free-form source tag. Defaults to image_ocr but admins can override
    # (e.g. "manual:gaslight" if they typed events instead of OCR-ing).
    source: str = "image_ocr"


class CommitResponse(BaseModel):
    inserted: int
    skipped: int       # rows that collided with existing (date, venue, title)
    events_in: int     # what the request submitted, for sanity


@router.post("/commit", response_model=CommitResponse)
def commit_events(
    payload: CommitRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Insert reviewed events into calendar_events. Dedupes by
    (date, venue, title) — same key the venue scraper uses."""
    venue = (payload.venue or "").strip()
    if not venue:
        raise HTTPException(400, "venue is required")
    if not payload.events:
        return CommitResponse(inserted=0, skipped=0, events_in=0)

    source_tag = (payload.source or "image_ocr").strip()[:80]
    full_source = f"{source_tag}:{venue}" if not source_tag.endswith(venue) else source_tag

    inserted = 0
    skipped = 0
    for ev in payload.events:
        title = (ev.title or "").strip()
        if not title or not ev.date:
            skipped += 1
            continue
        try:
            existing = db.execute(sql_text(
                "SELECT 1 FROM calendar_events "
                "WHERE date = :date AND venue = :venue AND title = :title"
            ), {"date": ev.date, "venue": venue, "title": title}).fetchone()
            if existing:
                skipped += 1
                continue

            db.execute(sql_text("""
                INSERT INTO calendar_events
                  (date, title, time, end_time, location, description,
                   source, venue, city, event_type)
                VALUES
                  (:date, :title, :time, :end_time, :location, NULL,
                   :source, :venue, :city, 'live_music')
            """), {
                "date": ev.date,
                "title": title,
                "time": ev.time,
                "end_time": ev.end_time,
                "location": f"{venue}{', ' + payload.city if payload.city else ''}",
                "source": full_source,
                "venue": venue,
                "city": payload.city,
            })
            inserted += 1
        except Exception as exc:
            logger.warning(
                "event-ingest insert failed (venue=%s title=%s date=%s): %s",
                venue, title, ev.date, exc,
            )
            skipped += 1

    db.commit()
    logger.info(
        "event-ingest commit by admin=%s venue=%s: inserted=%d skipped=%d",
        admin.email, venue, inserted, skipped,
    )
    return CommitResponse(inserted=inserted, skipped=skipped, events_in=len(payload.events))
