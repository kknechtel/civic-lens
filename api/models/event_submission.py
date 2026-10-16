"""User-submitted event proposals — moderated into calendar_events.

The fallback for FB-only venues (Eventide, Tommy's, Atlantic House, etc).
Logged-in user fills a form; an admin reviews; on approve, the row is
copied into calendar_events with source='submission:<email>' so it
flows through the same surface as scraped events.

Status enum:
  pending   awaiting admin review (default)
  approved  pushed to calendar_events; calendar_event_id holds the FK
  rejected  admin declined; reason in admin_note

Hard-delete is allowed only by admins; submitters can withdraw their
own pending submission via DELETE on the row.
"""
import enum
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Enum, Index, Text, Date,
)
from sqlalchemy.dialects.postgresql import UUID
from database import Base


class SubmissionStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class EventSubmission(Base):
    __tablename__ = "event_submissions"
    __table_args__ = (
        Index("ix_event_submissions_status", "status", "created_at"),
        Index("ix_event_submissions_submitter", "submitter_user_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submitter_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # User-supplied fields
    title = Column(String, nullable=False)           # band name or event title
    event_date = Column(Date, nullable=False)
    event_time = Column(String, nullable=True)        # free text e.g. "8:00 PM"
    end_time = Column(String, nullable=True)
    venue_name = Column(String, nullable=False)
    city = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    ticket_url = Column(String, nullable=True)
    submitter_note = Column(Text, nullable=True)      # "I work there", "saw the flyer"

    # Moderation
    status = Column(Enum(
        SubmissionStatus, name="submission_status",
        values_callable=lambda e: [m.value for m in e],
    ), nullable=False, default=SubmissionStatus.pending)
    admin_note = Column(Text, nullable=True)
    reviewed_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    calendar_event_id = Column(UUID(as_uuid=True), nullable=True)  # populated on approve

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
