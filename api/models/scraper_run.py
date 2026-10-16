"""ScraperRun — one row per completed scraper invocation.

Lets the UI show a history of past runs and what each one actually added.
Without this, every UI restart wipes the in-memory _scraper_status and you
lose the "last run brought in 12 new docs" view.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB

from database import Base


class ScraperRun(Base):
    __tablename__ = "scraper_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    completed_at = Column(DateTime, nullable=True)

    sites = Column(JSONB, nullable=False, default=list)
    mode = Column(String, nullable=False, default="all")  # "all" | "recent_only"
    triggered_by = Column(String, nullable=True)  # "ui:<user_id>" | "schedule" | "manual"

    documents_found = Column(Integer, nullable=False, default=0)
    documents_uploaded = Column(Integer, nullable=False, default=0)
    documents_skipped = Column(Integer, nullable=False, default=0)
    errors = Column(JSONB, nullable=False, default=list)

    # List of {filename, source, category, doc_type, url, fiscal_year?}
    # for every doc that actually landed in S3 + RDS on this run. Lets the
    # UI render "New since last run: X PDFs from ahnj.com Planning Board, Y
    # from tridistrict, …" without a join.
    new_docs = Column(JSONB, nullable=False, default=list)

    __table_args__ = (
        Index("ix_scraper_runs_started_at_desc", started_at.desc()),
    )
