"""Calendar events API - serves scraped borough events and document-derived dates."""
import logging
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text

from database import get_db
from models.event_rsvp import EventRsvp, RSVP_STATUSES
from models.user import User
from auth import get_current_user, get_current_user_optional

logger = logging.getLogger(__name__)
router = APIRouter()

# `time` is free text ("7:00 PM", "7:00 pm", "12:00 Noon"), so ordering on
# it in SQL sorts lexically — "10:00 PM" lands before "6:00 PM" and an
# evening lineup comes out shuffled. Parse to minutes-since-midnight and
# order in Python instead.
_CLOCK = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?|^\s*(\d{1,2})(?::(\d{2}))?\s*(noon|midnight)", re.I)
_SORT_LAST = 24 * 60 + 1        # untimed / unparseable events sort after timed ones


def _minutes(t: str | None) -> int:
    """'7:00 PM' → 1140. Anything unparseable sorts last."""
    if not t:
        return _SORT_LAST
    m = _CLOCK.match(t)
    if not m:
        return _SORT_LAST
    if m.group(1) is not None:
        hh, mm, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
    else:
        hh, mm = int(m.group(4)), int(m.group(5) or 0)
        ampm = "p" if m.group(6).lower() == "noon" else "a"
    if hh > 12 or mm > 59:
        return _SORT_LAST
    hh = hh % 12 + (12 if ampm == "p" else 0)
    return hh * 60 + mm


@router.get("/events")
def get_calendar_events(
    year: int = Query(None),
    month: int = Query(None),
    event_type: str = Query(None, description="'general' (borough) or 'live_music'"),
    city: str = Query(None),
    venue: str = Query(None),
    q: str = Query(None, description="Free-text — case-insensitive substring match on title/venue/description"),
    upcoming_only: bool = Query(False, description="If true, hide events whose date is in the past"),
    limit: int = Query(None, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    """Get calendar events from the scraped borough calendar + live-music
    venue scrapers. Filterable by year/month, event_type/city/venue, and
    free-text query `q`. When `q` is provided we cross-search title +
    venue + description so a band name typed by a user matches the show
    regardless of which venue posted it."""
    query = (
        "SELECT id, date, title, time, end_time, location, description, "
        "source, source_url, venue, city, event_type, ticket_url, created_at "
        "FROM calendar_events"
    )
    params = {}
    conditions = []

    if year:
        conditions.append("EXTRACT(YEAR FROM date) = :year")
        params["year"] = year
    if month:
        conditions.append("EXTRACT(MONTH FROM date) = :month")
        params["month"] = month
    if event_type:
        conditions.append("event_type = :event_type")
        params["event_type"] = event_type
    if city:
        conditions.append("city = :city")
        params["city"] = city
    if venue:
        conditions.append("venue = :venue")
        params["venue"] = venue
    if q:
        # Split on whitespace and require every token to appear somewhere
        # (title / venue / description). Matches "brian kirk" against
        # "Brian Kirk and the Jirks" the same way Google would.
        tokens = [t for t in q.strip().split() if t][:6]
        if tokens:
            for idx, tok in enumerate(tokens):
                key = f"q{idx}"
                conditions.append(
                    f"(title ILIKE :{key} OR venue ILIKE :{key} OR description ILIKE :{key})"
                )
                params[key] = f"%{tok}%"
    if upcoming_only:
        conditions.append("date >= CURRENT_DATE")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY date ASC"

    try:
        rows = db.execute(sql_text(query), params).fetchall()
    except Exception as e:
        logger.warning(f"Calendar events query failed (table may not exist): {e}")
        return []

    events = [_row_to_dict(r) for r in rows]
    # Sort here rather than in SQL — see _minutes. LIMIT is applied after
    # sorting so a capped result isn't truncated mid-day in the wrong order.
    events.sort(key=lambda e: (e["date"], _minutes(e["time"]), e["title"] or ""))
    return events[:limit] if limit else events


def _row_to_dict(r) -> dict:
    return {
        "id": str(r.id),
        "date": r.date.isoformat(),
        "title": r.title,
        "time": r.time,
        "end_time": getattr(r, "end_time", None),
        "location": r.location,
        "description": r.description,
        "source": r.source,
        "source_url": r.source_url,
        "venue": getattr(r, "venue", None),
        "city": getattr(r, "city", None),
        "event_type": getattr(r, "event_type", None) or "general",
        "ticket_url": getattr(r, "ticket_url", None),
    }


@router.get("/events/{event_id}")
def get_calendar_event(
    event_id: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    """Single event lookup for the events-app /calendar/[id] detail page."""
    try:
        row = db.execute(sql_text(
            "SELECT id, date, title, time, end_time, location, description, "
            "source, source_url, venue, city, event_type, ticket_url, created_at "
            "FROM calendar_events WHERE id = CAST(:id AS uuid)"
        ), {"id": event_id}).fetchone()
    except Exception as e:
        logger.warning(f"Calendar event lookup failed: {e}")
        raise HTTPException(status_code=404, detail="Event not found")
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    return _row_to_dict(row)


# ── RSVPs ────────────────────────────────────────────────────────────

class RsvpUser(BaseModel):
    user_id: str
    display_name: str | None
    picture_url: str | None


class RsvpSummary(BaseModel):
    event_id: str
    count: int                          # everyone with any status (going + tentative + follow_up)
    going_count: int                    # just "going" — the firm headcount
    is_going: bool                      # back-compat: my_status == "going"
    my_status: str | None               # 'going' | 'tentative' | 'follow_up' | None
    sample_users: list[RsvpUser]        # up to 12 most recent, any status


class RsvpRequest(BaseModel):
    # POST body — clients pick a status. Defaults to "going" so existing
    # callers that send no body still work.
    status: str = "going"


def _display_for(u: User) -> str | None:
    return u.display_name or u.full_name or ((u.email or "").split("@", 1)[0] or None)


@router.get("/events/{event_id}/rsvp", response_model=RsvpSummary)
def get_event_rsvp(
    event_id: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    total = db.query(func.count(EventRsvp.id)).filter(EventRsvp.event_id == event_id).scalar() or 0
    going = db.query(func.count(EventRsvp.id)).filter(
        EventRsvp.event_id == event_id, EventRsvp.status == "going",
    ).scalar() or 0
    mine = db.query(EventRsvp).filter(
        EventRsvp.event_id == event_id, EventRsvp.user_id == user.id,
    ).first() if user else None
    sample_rows = (db.query(EventRsvp, User)
                     .join(User, User.id == EventRsvp.user_id)
                     .filter(EventRsvp.event_id == event_id)
                     .order_by(EventRsvp.created_at.desc())
                     .limit(12).all())
    return RsvpSummary(
        event_id=event_id,
        count=int(total),
        going_count=int(going),
        is_going=(mine is not None and mine.status == "going"),
        my_status=mine.status if mine else None,
        sample_users=[
            RsvpUser(user_id=str(u.id),
                     display_name=_display_for(u),
                     picture_url=u.picture_url)
            for _, u in sample_rows
        ],
    )


@router.post("/events/{event_id}/rsvp", response_model=RsvpSummary, status_code=status.HTTP_201_CREATED)
def rsvp_to_event(
    event_id: str,
    payload: RsvpRequest = RsvpRequest(),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create or update the caller's RSVP. Re-POSTing with a different
    status flips it in place (tentative → going, etc.). Idempotent."""
    if payload.status not in RSVP_STATUSES:
        raise HTTPException(400, f"status must be one of {RSVP_STATUSES}")

    # Verify the event exists before letting users RSVP to a typo.
    row = db.execute(sql_text(
        "SELECT 1 FROM calendar_events WHERE id = CAST(:id AS uuid)"
    ), {"id": event_id}).fetchone()
    if not row:
        raise HTTPException(404, "Event not found")

    existing = db.query(EventRsvp).filter(
        EventRsvp.user_id == user.id, EventRsvp.event_id == event_id,
    ).first()
    if existing:
        existing.status = payload.status
    else:
        db.add(EventRsvp(user_id=user.id, event_id=event_id, status=payload.status))
    db.commit()
    return get_event_rsvp(event_id, db, user)


@router.delete("/events/{event_id}/rsvp", response_model=RsvpSummary)
def unrsvp_from_event(
    event_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    existing = db.query(EventRsvp).filter(
        EventRsvp.user_id == user.id, EventRsvp.event_id == event_id,
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
    return get_event_rsvp(event_id, db, user)


# ── My Calendar ──────────────────────────────────────────────────────

class SavedEvent(BaseModel):
    """Caller's RSVP joined with the calendar_events row it points to."""
    rsvp_id: str
    status: str          # 'going' | 'tentative' | 'follow_up'
    saved_at: str        # ISO timestamp
    # Event fields — may be None if the underlying calendar_events row
    # was purged by a scraper rerun (we tolerate dangling RSVPs).
    event_id: str
    title: str | None
    date: str | None
    time: str | None
    end_time: str | None
    venue: str | None
    city: str | None
    event_type: str | None
    ticket_url: str | None


@router.get("/me/calendar", response_model=list[SavedEvent])
def my_saved_events(
    upcoming_only: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """All of the caller's RSVPs, joined with calendar_events. Sorted by
    date ascending. By default, hides events whose date has passed."""
    today = datetime.utcnow().date().isoformat()
    rows = db.execute(sql_text(
        """
        SELECT r.id AS rsvp_id, r.status, COALESCE(r.updated_at, r.created_at) AS saved_at,
               r.event_id::text AS event_id,
               e.title, e.date, e.time, e.end_time, e.venue, e.city,
               e.event_type, e.ticket_url
        FROM event_rsvps r
        LEFT JOIN calendar_events e ON e.id = r.event_id
        WHERE r.user_id = :uid
          AND (:upcoming_only = false OR e.date IS NULL OR e.date >= :today)
        ORDER BY e.date NULLS LAST, e.time NULLS LAST, r.created_at DESC
        """
    ), {"uid": str(user.id), "upcoming_only": upcoming_only, "today": today}).fetchall()
    return [
        SavedEvent(
            rsvp_id=str(r.rsvp_id),
            status=r.status,
            saved_at=r.saved_at.isoformat() if r.saved_at else "",
            event_id=r.event_id,
            title=r.title,
            date=r.date.isoformat() if r.date else None,
            time=r.time,
            end_time=r.end_time,
            venue=r.venue,
            city=r.city,
            event_type=r.event_type,
            ticket_url=r.ticket_url,
        )
        for r in rows
    ]
