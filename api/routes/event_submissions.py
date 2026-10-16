"""Event submission routes — community submits, admin reviews.

Endpoints (mounted at /api/event-submissions):
  POST   /                       any user — create a pending submission
  GET    /                       admin sees all (filterable); non-admin sees own
  GET    /{id}                   submitter or admin
  POST   /{id}/approve           admin — inserts into calendar_events
  POST   /{id}/reject            admin — sets status + admin_note
  DELETE /{id}                   submitter (only if still pending) or admin
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, text as sql_text
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models.event_submission import EventSubmission, SubmissionStatus
from models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────────

class SubmissionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    event_date: date
    event_time: Optional[str] = Field(default=None, max_length=40)
    end_time: Optional[str] = Field(default=None, max_length=40)
    venue_name: str = Field(min_length=1, max_length=120)
    city: Optional[str] = Field(default=None, max_length=60)
    description: Optional[str] = Field(default=None, max_length=2000)
    ticket_url: Optional[str] = Field(default=None, max_length=500)
    submitter_note: Optional[str] = Field(default=None, max_length=500)


class SubmissionOut(BaseModel):
    id: str
    submitter_user_id: Optional[str]
    submitter_email: Optional[str]
    title: str
    event_date: date
    event_time: Optional[str]
    end_time: Optional[str]
    venue_name: str
    city: Optional[str]
    description: Optional[str]
    ticket_url: Optional[str]
    submitter_note: Optional[str]
    status: SubmissionStatus
    admin_note: Optional[str]
    calendar_event_id: Optional[str]
    created_at: datetime
    reviewed_at: Optional[datetime]


class RejectRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


def _to_out(s: EventSubmission, submitter: Optional[User]) -> SubmissionOut:
    return SubmissionOut(
        id=str(s.id),
        submitter_user_id=str(s.submitter_user_id) if s.submitter_user_id else None,
        submitter_email=submitter.email if submitter else None,
        title=s.title,
        event_date=s.event_date,
        event_time=s.event_time,
        end_time=s.end_time,
        venue_name=s.venue_name,
        city=s.city,
        description=s.description,
        ticket_url=s.ticket_url,
        submitter_note=s.submitter_note,
        status=s.status,
        admin_note=s.admin_note,
        calendar_event_id=str(s.calendar_event_id) if s.calendar_event_id else None,
        created_at=s.created_at,
        reviewed_at=s.reviewed_at,
    )


def _require_admin(user: User) -> None:
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "Admin only")


# ── Routes ───────────────────────────────────────────────────────────

@router.post("/", response_model=SubmissionOut, status_code=status.HTTP_201_CREATED)
def create_submission(
    payload: SubmissionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = EventSubmission(
        submitter_user_id=user.id,
        title=payload.title.strip(),
        event_date=payload.event_date,
        event_time=(payload.event_time or "").strip() or None,
        end_time=(payload.end_time or "").strip() or None,
        venue_name=payload.venue_name.strip(),
        city=(payload.city or "").strip() or None,
        description=(payload.description or "").strip() or None,
        ticket_url=(payload.ticket_url or "").strip() or None,
        submitter_note=(payload.submitter_note or "").strip() or None,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _to_out(s, user)


@router.get("/", response_model=list[SubmissionOut])
def list_submissions(
    status_filter: Optional[SubmissionStatus] = Query(None, alias="status"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Admin: all (or filtered). Non-admin: only their own."""
    q = (db.query(EventSubmission, User)
           .outerjoin(User, User.id == EventSubmission.submitter_user_id))
    if not getattr(user, "is_admin", False):
        q = q.filter(EventSubmission.submitter_user_id == user.id)
    if status_filter:
        q = q.filter(EventSubmission.status == status_filter)
    rows = q.order_by(desc(EventSubmission.created_at)).limit(200).all()
    return [_to_out(s, u) for s, u in rows]


@router.post("/{sub_id}/approve", response_model=SubmissionOut)
def approve_submission(
    sub_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_admin(user)
    s = db.query(EventSubmission).filter(EventSubmission.id == sub_id).first()
    if not s:
        raise HTTPException(404, "Submission not found")
    if s.status != SubmissionStatus.pending:
        raise HTTPException(409, f"Already {s.status.value}")

    # Insert into calendar_events. The source label preserves the
    # submitter so the moderation chain is traceable from the calendar row.
    submitter = db.query(User).filter(User.id == s.submitter_user_id).first() if s.submitter_user_id else None
    submitter_label = (submitter.email if submitter else None) or "user"
    new_id = uuid.uuid4()
    db.execute(sql_text("""
        INSERT INTO calendar_events
            (id, date, title, time, end_time, location, description,
             source, source_url, venue, city, event_type, ticket_url)
        VALUES
            (CAST(:id AS uuid), :date, :title, :time, :end_time, :location, :description,
             :source, :url, :venue, :city, :event_type, :ticket_url)
    """), {
        "id": str(new_id),
        "date": s.event_date,
        "title": s.title,
        "time": s.event_time,
        "end_time": s.end_time,
        "location": f"{s.venue_name}, {s.city}" if s.city else s.venue_name,
        "description": s.description,
        "source": f"submission:{submitter_label}",
        "url": s.ticket_url,
        "venue": s.venue_name,
        "city": s.city,
        # We don't try to guess music vs other; safest bucket is 'community'
        # so it shows on the events app, not on the civic calendar.
        "event_type": "community",
        "ticket_url": s.ticket_url,
    })
    s.status = SubmissionStatus.approved
    s.reviewed_by_user_id = user.id
    s.reviewed_at = datetime.utcnow()
    s.calendar_event_id = new_id
    db.commit()
    db.refresh(s)
    return _to_out(s, submitter)


@router.post("/{sub_id}/reject", response_model=SubmissionOut)
def reject_submission(
    sub_id: str,
    payload: RejectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_admin(user)
    s = db.query(EventSubmission).filter(EventSubmission.id == sub_id).first()
    if not s:
        raise HTTPException(404, "Submission not found")
    if s.status != SubmissionStatus.pending:
        raise HTTPException(409, f"Already {s.status.value}")
    s.status = SubmissionStatus.rejected
    s.admin_note = (payload.reason or "").strip() or None
    s.reviewed_by_user_id = user.id
    s.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(s)
    submitter = db.query(User).filter(User.id == s.submitter_user_id).first() if s.submitter_user_id else None
    return _to_out(s, submitter)


@router.delete("/{sub_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_submission(
    sub_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = db.query(EventSubmission).filter(EventSubmission.id == sub_id).first()
    if not s:
        raise HTTPException(404, "Submission not found")
    is_admin = getattr(user, "is_admin", False)
    is_own = s.submitter_user_id == user.id
    if not (is_admin or (is_own and s.status == SubmissionStatus.pending)):
        raise HTTPException(403, "Cannot delete this submission")
    db.delete(s)
    db.commit()
    return None
