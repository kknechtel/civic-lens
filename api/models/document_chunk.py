"""DocumentChunk model — RAG vector chunks for hybrid retrieval."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from database import Base


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False, default=0)
    content = Column(Text, nullable=False)
    # One-line doc context ("title · municipality · body · type · date · p.N"),
    # embedded and indexed with the passage but kept out of displayed snippets.
    context = Column(Text, nullable=True)
    token_count = Column(Integer, nullable=True)
    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # `embedding vector(1024)` and `fts_vector tsvector` columns are added by
    # the inline migration in database.py — SQLAlchemy core types don't cover
    # pgvector, and we don't want to depend on the pgvector Python package
    # to define the schema (only the search service uses it).
