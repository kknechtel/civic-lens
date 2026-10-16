"""PresentationVersion — immutable snapshot of a Presentation as it was at
publish time. The public viewer at /p/{slug} reads the row where
is_current_public=True, NOT the live presentations.sections blob the editor
mutates as the user types. So edits stay in draft until the operator
clicks Republish, and old public links keep resolving to the exact content
they shipped with even if the underlying source documents are later
replaced or deleted.

Doc-snapshot policy
-------------------
At publish time we walk every section for [DOC:id] / [EMAIL:id] tokens,
resolve them against the documents table, copy the S3 object to a
version-scoped key under presentation-versions/{pid}/v{N}/docs/, and
record the mapping in `doc_snapshots`. The public doc preview endpoint
resolves citations through this manifest first so a published deck's
links can never serve a stranger's document if the underlying Document
row gets reused.
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from database import Base


class PresentationVersion(Base):
    __tablename__ = "presentation_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    presentation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("presentations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no = Column(Integer, nullable=False)

    # Frozen content snapshot. These are deep copies — nothing the editor
    # does to the parent presentations.sections after this row is written
    # affects what the public viewer sees.
    title = Column(String, nullable=False)
    sections = Column(JSONB, nullable=False, default=list)
    attachments = Column(JSONB, nullable=False, default=list)
    disclosure = Column(JSONB, nullable=True)

    # { "<doc_id>": {"snapshot_s3_key": "...", "filename": "...",
    #                "size_bytes": int, "snapshotted_at": isoformat,
    #                "source_s3_key": "...", "source_etag": "..." }, ... }
    # The public doc preview endpoint resolves [DOC:id] tokens through
    # this map. `source_*` is kept for diagnostics — to tell whether the
    # original doc has changed since publish.
    doc_snapshots = Column(JSONB, nullable=False, default=dict)

    # Same hash format as Presentation.public_password_hash. Snapshotted
    # so an old version stays accessible by its original password if the
    # author later changes the password on the parent presentation.
    public_password_hash = Column(String, nullable=True)

    is_current_public = Column(Boolean, nullable=False, default=False)
    published_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Author identifier — typically the user's email. Snapshotted as a
    # string so the version row stays meaningful even if the user is
    # later deleted.
    published_by = Column(String, nullable=True)
    # When non-null, this version was created as a rollback to the named
    # earlier version. Lets the UI show a "← rolled back to v3" annotation.
    rolled_back_from_version_no = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    presentation = relationship("Presentation", backref="versions")

    __table_args__ = (
        UniqueConstraint("presentation_id", "version_no", name="uq_presentation_version_no"),
        # Partial unique: at most one row per presentation can be the
        # "currently public" version. Postgres enforces it cheaply.
        Index(
            "uq_presentation_current_public",
            "presentation_id",
            unique=True,
            postgresql_where=(is_current_public == True),  # noqa: E712
        ),
        Index("ix_presentation_versions_pid_published_at", "presentation_id", "published_at"),
    )
