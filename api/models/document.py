"""Document and project models for the document library."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, ForeignKey, Text, Table
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from database import Base


# Many-to-many: documents <-> tags
document_tags = Table(
    "document_tags",
    Base.metadata,
    Column("document_id", UUID(as_uuid=True), ForeignKey("documents.id"), primary_key=True),
    Column("tag_id", UUID(as_uuid=True), ForeignKey("tags.id"), primary_key=True),
)


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    entity_type = Column(String, nullable=True)  # "town", "school", "general"
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    documents = relationship("Document", back_populates="project", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    filename = Column(String, nullable=False)
    original_filename = Column(String, nullable=False)
    s3_key = Column(String, nullable=False)
    s3_bucket = Column(String, nullable=False)
    file_size = Column(Integer, default=0)
    content_type = Column(String, nullable=True)
    doc_type = Column(String, nullable=True)  # "financial_statement", "budget", "audit", "minutes", etc.
    category = Column(String, nullable=True)  # "town", "school"
    # Which government the record belongs to — "town" alone can't tell
    # Atlantic Highlands from neighboring Highlands Borough.
    # atlantic_highlands | highlands | hhrsd | regional | state
    municipality = Column(String, nullable=True)
    department = Column(String, nullable=True)
    fiscal_year = Column(String, nullable=True)
    # Human-readable title for the document (e.g. "Planning Board Meeting Minutes —
    # February 4, 2026"). Derived by services.title_extractor from extracted_text +
    # filename patterns + AI metadata. Filename stays as the canonical identifier
    # (used for URLs, S3 keys, dedup) but title is what the UI shows users.
    title = Column(String, nullable=True)
    doc_date = Column(String, nullable=True)  # ISO YYYY-MM-DD when extractable
    status = Column(String, default="uploaded")  # uploaded, processing, processed, failed
    metadata_ = Column("metadata", JSONB, default={})
    notes = Column(Text, nullable=True)
    extracted_text = Column(Text, nullable=True)
    page_count = Column(Integer, nullable=True)
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="documents")
    tags = relationship("Tag", secondary=document_tags, back_populates="documents")


class Tag(Base):
    __tablename__ = "tags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, unique=True, nullable=False, index=True)
    color = Column(String, nullable=True)

    documents = relationship("Document", secondary=document_tags, back_populates="tags")
