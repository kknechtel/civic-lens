"""
AI provider keys and settings.

ApiKey rows belong either to the deployment (owner_type="deployment", added
by an admin) or to one user (owner_type="user"). Keys are stored encrypted
(services.ai.crypto); only the last four characters are ever returned.

AiSetting is a small key → JSON store for deployment-wide AI configuration:
role routes, sponsored features, spending caps and rate overrides.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database import Base


class ApiKey(Base):
    __tablename__ = "ai_api_keys"
    __table_args__ = (UniqueConstraint("owner_type", "user_id", "provider", name="uq_ai_key_owner_provider"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_type = Column(String, nullable=False)  # deployment | user
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    provider = Column(String, nullable=False)
    ciphertext = Column(Text, nullable=False)
    last4 = Column(String(8))
    status = Column(String, default="untested")  # untested | ok | invalid
    status_detail = Column(String)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    verified_at = Column(DateTime)


class AiSetting(Base):
    __tablename__ = "ai_settings"

    key = Column(String, primary_key=True)  # routes | sponsored | caps | rates | user_prefs:<uuid>
    value = Column(JSONB, nullable=False, default=dict)
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
