"""Authentication utilities - JWT-based auth with Google OAuth support."""
import logging
from datetime import datetime, timedelta
from typing import Literal, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRATION_HOURS
from database import get_db
from models.user import User

ShareRole = Literal["viewer", "editor"]

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=JWT_EXPIRATION_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _resolve_user_from_token(
    credentials: Optional[HTTPAuthorizationCredentials],
    db: Session,
) -> User:
    """Decode JWT and look up user. Does NOT check is_active."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def get_current_user_allow_pending(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Returns user even if is_active=False (for /me endpoint and pending screen)."""
    return _resolve_user_from_token(credentials, db)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Verify JWT token and return an active, approved user."""
    user = _resolve_user_from_token(credentials, db)
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account pending approval")
    return user


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Returns the active user if a valid token is supplied, else None.

    Use for endpoints that should be readable anonymously (e.g. events-app
    public browsing) but want to personalize the response when a user IS
    signed in (e.g. include their RSVP/check-in state). Never raises 401.
    """
    if not credentials:
        return None
    try:
        user = _resolve_user_from_token(credentials, db)
    except HTTPException:
        return None
    if not user.is_active:
        return None
    return user


def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """Require admin privileges."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ─── Ownership / sharing helpers ────────────────────────────────────────────
#
# Resources scoped per-user follow the same shape: a `created_by` column on
# the resource table, plus an optional `<resource>_shares` table with
# (resource_id, user_id, role). Admins bypass all checks.

_SHARE_TABLES = {
    "presentations": ("presentation_shares", "presentation_id"),
    "projects": ("project_shares", "project_id"),
}


def user_share_role(
    db: Session,
    resource: str,
    resource_id,
    user_id,
) -> Optional[ShareRole]:
    """Return the share role for (resource, user) or None if not shared."""
    share_tbl, fk_col = _SHARE_TABLES[resource]
    row = db.execute(sql_text(
        f"SELECT role FROM {share_tbl} "
        f"WHERE {fk_col} = :rid AND user_id = :uid"
    ), {"rid": str(resource_id), "uid": str(user_id)}).fetchone()
    return row[0] if row else None


def can_view(db: Session, resource: str, owner_id, resource_id, user: User) -> bool:
    """Owner, any share, or admin can view."""
    if user.is_admin or str(owner_id) == str(user.id):
        return True
    return user_share_role(db, resource, resource_id, user.id) is not None


def can_edit(db: Session, resource: str, owner_id, resource_id, user: User) -> bool:
    """Owner, editor share, or admin can edit. Viewer cannot."""
    if user.is_admin or str(owner_id) == str(user.id):
        return True
    return user_share_role(db, resource, resource_id, user.id) == "editor"


def require_view(db: Session, resource: str, owner_id, resource_id, user: User) -> None:
    if not can_view(db, resource, owner_id, resource_id, user):
        raise HTTPException(status_code=404, detail=f"{resource[:-1].capitalize()} not found")


def require_edit(db: Session, resource: str, owner_id, resource_id, user: User) -> None:
    if not can_edit(db, resource, owner_id, resource_id, user):
        # If they can view but not edit, say so; otherwise 404 to avoid
        # leaking existence.
        if can_view(db, resource, owner_id, resource_id, user):
            raise HTTPException(status_code=403, detail="Read-only access")
        raise HTTPException(status_code=404, detail=f"{resource[:-1].capitalize()} not found")


def require_owner_or_admin(owner_id, user: User) -> None:
    """For destructive ops (delete, share-management): only owner or admin."""
    if user.is_admin or str(owner_id) == str(user.id):
        return
    raise HTTPException(status_code=403, detail="Only the owner can perform this action")


def shared_resource_ids(db: Session, resource: str, user_id) -> list[str]:
    """IDs of resources of this kind that have been shared with this user."""
    share_tbl, fk_col = _SHARE_TABLES[resource]
    rows = db.execute(sql_text(
        f"SELECT {fk_col} FROM {share_tbl} WHERE user_id = :uid"
    ), {"uid": str(user_id)}).fetchall()
    return [str(r[0]) for r in rows]
