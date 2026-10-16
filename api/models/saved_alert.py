"""
Saved alerts — subscriptions that fan out to email digests.

Three subscription kinds, all flowing through the same digest job:
  - keyword:       re-run a hybrid search against new content since last_run_at
  - new_meeting:   notify when a new meeting (optionally filtered by body) is ingested
  - new_document:  notify when a new doc (optionally filtered by category/doc_type) is ingested

`filters` is a JSONB bag whose shape depends on `kind`:
  - keyword:       {"category"?: "town"|"school", "department"?: "Council"|..., "doc_type"?: "..."}
  - new_meeting:   {"body"?: "Council"|"Planning"|"Harbor"|"HHRSD"}
  - new_document:  {"category"?: "town"|"school", "doc_type"?: "..."}

`last_run_at` is the high-water mark — content with created_at > last_run_at
is candidate for the next digest. The worker advances it after each send.
"""
import enum
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Enum, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base


class AlertKind(str, enum.Enum):
    keyword = "keyword"
    new_meeting = "new_meeting"
    new_document = "new_document"


class DigestFrequency(str, enum.Enum):
    daily = "daily"
    weekly = "weekly"


class SavedAlert(Base):
    __tablename__ = "saved_alerts"
    __table_args__ = (
        Index("ix_saved_alerts_user", "user_id"),
        Index("ix_saved_alerts_due", "enabled", "last_sent_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    kind = Column(Enum(AlertKind, name="alert_kind", values_callable=lambda e: [m.value for m in e]),
                  nullable=False)
    name = Column(String, nullable=False)        # user-given label
    query = Column(String, nullable=True)        # required when kind == keyword
    filters = Column(JSONB, default=dict)        # kind-specific (see module docstring)

    frequency = Column(Enum(DigestFrequency, name="digest_frequency", values_callable=lambda e: [m.value for m in e]),
                       nullable=False, default=DigestFrequency.daily)
    enabled = Column(Boolean, nullable=False, default=True)

    last_run_at = Column(DateTime, nullable=True)   # watermark — content newer than this is candidate
    last_sent_at = Column(DateTime, nullable=True)  # last time a non-empty digest went out
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
