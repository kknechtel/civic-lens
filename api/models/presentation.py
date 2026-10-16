"""Presentation models — analytical decks and reports about Atlantic Highlands."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from database import Base


class Presentation(Base):
    __tablename__ = "presentations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    slug = Column(String, nullable=True, index=True)
    public_slug = Column(String, nullable=True, unique=True, index=True)
    status = Column(String, default="draft")  # draft, published, archived

    # JSONB so the section schema can evolve without migrations.
    # Each section: {id, kind, title?, body?, headers?, rows?, caption?, attachment_id?, ...}
    sections = Column(JSONB, default=list)
    attachments = Column(JSONB, default=list)  # [{id, document_id, filename, caption?}]
    theme = Column(JSONB, default=dict)

    # Last fact-check result snapshot
    last_fact_check = Column(JSONB, nullable=True)

    # First-visit disclosure modal config: {enabled, is_draft, custom_text}.
    # When enabled, the public viewer renders a one-time modal on first visit
    # (localStorage `ah-deck-disclosed:{slug}`). Body composes:
    #   "This presentation has been prepared with the assistance of AI."
    #   + (if is_draft) " This is a draft and critical points should be
    #      verified against the included source documents."
    #   + (if custom_text) custom_text on a new line.
    disclosure = Column(JSONB, nullable=True)

    # Optional bcrypt-hashed password for public_slug access
    public_password_hash = Column(String, nullable=True)
    public_password_set_at = Column(DateTime, nullable=True)

    published_at = Column(DateTime, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    comments = relationship("PresentationComment", back_populates="presentation",
                            cascade="all, delete-orphan")


class PresentationComment(Base):
    __tablename__ = "presentation_comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    presentation_id = Column(UUID(as_uuid=True), ForeignKey("presentations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    section_id = Column(String, nullable=True, index=True)
    parent_comment_id = Column(UUID(as_uuid=True), ForeignKey("presentation_comments.id"), nullable=True)
    author_email = Column(String, nullable=True)
    author_name = Column(String, nullable=True)
    body = Column(Text, nullable=False)
    resolved = Column(Boolean, default=False)
    resolved_by_email = Column(String, nullable=True)
    # Inline-range anchor (W3C TextPositionSelector-style). NULL for
    # section-level comments. Shape: {quote, prefix?, suffix?}. At render
    # time the viewer searches the section body for `quote` and uses
    # prefix/suffix to disambiguate when the quote appears more than once.
    anchor = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    presentation = relationship("Presentation", back_populates="comments")
