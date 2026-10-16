"""User model for authentication and authorization."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    username = Column(String, unique=True, nullable=False)
    # hashed_password is nullable so Google-only users (no password) work.
    # The migration in database.py drops the NOT NULL on existing rows.
    hashed_password = Column(String, nullable=True)
    full_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=False)  # Requires admin approval or invite
    is_admin = Column(Boolean, default=False)
    must_change_password = Column(Boolean, default=False)

    # Google Sign-In. Single users table holds both password + OAuth identities;
    # find-by-google_id → find-by-email → create reconciles new sign-ins with
    # any existing password account using the same email.
    google_id = Column(String, unique=True, nullable=True, index=True)
    picture_url = Column(String, nullable=True)
    display_name = Column(String, nullable=True)  # what to show in chat / on check-ins
    last_login_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InviteToken(Base):
    __tablename__ = "invite_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token = Column(String, unique=True, nullable=False, index=True)
    email = Column(String, nullable=True)  # Optional: lock to specific email
    created_by = Column(UUID(as_uuid=True), nullable=False)
    used_by = Column(UUID(as_uuid=True), nullable=True)
    is_used = Column(Boolean, default=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
