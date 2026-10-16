"""Authentication routes - login, register, magic link invites, Google."""
import logging
import re
import secrets
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import GOOGLE_OAUTH_CLIENT_ID
from database import get_db
from models.user import User, InviteToken
from auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    get_current_user_allow_pending,
)
from services.google_oauth import verify_id_token as verify_google_id_token

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Models ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


class SetPasswordRequest(BaseModel):
    invite_token: str
    email: str
    password: str
    full_name: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    pending_approval: bool = False


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    full_name: str | None
    display_name: str | None = None
    picture_url: str | None = None
    is_admin: bool
    is_active: bool
    must_change_password: bool = False

    class Config:
        from_attributes = True


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = None
    full_name: str | None = None


class ChangePasswordRequest(BaseModel):
    new_password: str


class InviteCheckResponse(BaseModel):
    valid: bool
    email: str | None
    already_registered: bool


# ── Helpers ──────────────────────────────────────────────────────────────────

def _validate_invite(db: Session, token_str: str) -> InviteToken:
    """Validate an invite token. Raises HTTPException if invalid."""
    invite = db.query(InviteToken).filter(
        InviteToken.token == token_str,
        InviteToken.is_used == False,
    ).first()

    if not invite:
        raise HTTPException(status_code=400, detail="Invalid invite link")
    if invite.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invite link has expired")
    return invite


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """Standard email/password login."""
    user = db.query(User).filter(User.email == req.email).first()
    # hashed_password is now nullable (Google-only users have it None) — reject
    # password login on those accounts cleanly instead of crashing on bcrypt.
    if not user or not user.hashed_password or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        token = create_access_token({"sub": str(user.id)})
        return TokenResponse(access_token=token, pending_approval=True)

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token)


# ── Google Sign-In ──────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: str
    password: str
    full_name: str | None = None


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post("/signup", response_model=TokenResponse)
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    """Self-service signup for the events-app community.

    Creates the account with is_active=False — the user gets a JWT
    immediately (so the SPA can show their pending state without a
    re-login), but every gated endpoint will reject them until an admin
    flips is_active=True via /admin/users.
    """
    from municipality import get_municipality
    mode = get_municipality().access.mode
    if mode == "invite_only":
        raise HTTPException(status_code=403, detail="This site is invite-only. Ask the owner for an invite link.")
    email = req.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with that email already exists")

    user = User(
        email=email,
        username=_slugify_username(email, db),
        hashed_password=hash_password(req.password),
        full_name=(req.full_name or "").strip() or None,
        display_name=(req.full_name or "").strip().split(" ")[0] or None,
        is_active=False,   # ← pending admin approval
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("Self-signup pending approval: %s", email)
    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token, pending_approval=True)


class GoogleLoginRequest(BaseModel):
    id_token: str


def _slugify_username(email: str, db: Session) -> str:
    """Generate a unique username from an email's local part. Falls back to
    appending a random suffix if the base slug is taken."""
    base = re.sub(r"[^a-z0-9_]+", "_", email.split("@", 1)[0].lower())[:24] or "user"
    candidate = base
    for _ in range(5):
        if not db.query(User).filter(User.username == candidate).first():
            return candidate
        candidate = f"{base}_{secrets.token_hex(2)}"
    return f"user_{secrets.token_hex(6)}"


@router.post("/google", response_model=TokenResponse)
def login_google(req: GoogleLoginRequest, db: Session = Depends(get_db)):
    """Verify a Google ID token (issued by GIS in the browser) and exchange
    it for our JWT.

    Account reconciliation (mirrors the Edgewater pattern):
      1. Match by google_id  → existing OAuth user, refresh picture + last_login
      2. Else match by email → link Google to an existing password account
      3. Else create a new user (active=True; Google-verified email is
         enough trust here — admin approval gates are unchanged)
    """
    if not GOOGLE_OAUTH_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Sign-In not configured on this server",
        )

    identity = verify_google_id_token(req.id_token, GOOGLE_OAUTH_CLIENT_ID)
    if not identity or not identity.get("email") or not identity.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid Google ID token")

    email = identity["email"]
    sub = identity["sub"]

    # 1) Existing OAuth user
    user = db.query(User).filter(User.google_id == sub).first()
    if user is None:
        # 2) Existing password user with the same email — link Google to them
        user = db.query(User).filter(User.email == email).first()
        if user is not None:
            user.google_id = sub
            logger.info("Linked Google account %s to existing user %s", sub, email)

    # 3) Brand-new user — only where the deployment allows signups.
    from municipality import get_municipality
    mode = get_municipality().access.mode
    if user is None and mode == "invite_only":
        raise HTTPException(status_code=403, detail="This site is invite-only. Ask the owner for an invite link.")
    if user is None:
        user = User(
            email=email,
            username=_slugify_username(email, db),
            hashed_password=None,
            full_name=identity.get("name"),
            google_id=sub,
            picture_url=identity.get("picture"),
            display_name=identity.get("given_name") or identity.get("name"),
            is_active=(mode == "open"),  # "approval": waits for an admin like password signups
            is_admin=False,
        )
        db.add(user)
        logger.info("Created Google user %s (sub=%s)", email, sub)

    # Always refresh picture + display_name + last_login on every Google login
    if identity.get("picture"):
        user.picture_url = identity["picture"]
    if not user.display_name:
        user.display_name = identity.get("given_name") or identity.get("name") or None
    user.last_login_at = datetime.utcnow()

    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(
        access_token=token,
        pending_approval=(not user.is_active),
    )


@router.get("/invite/{token}", response_model=InviteCheckResponse)
def check_invite(token: str, db: Session = Depends(get_db)):
    """Check if an invite token is valid. Frontend calls this on page load."""
    invite = db.query(InviteToken).filter(
        InviteToken.token == token,
        InviteToken.is_used == False,
    ).first()

    if not invite or invite.expires_at < datetime.utcnow():
        return InviteCheckResponse(valid=False, email=None, already_registered=False)

    already_registered = False
    if invite.email:
        existing = db.query(User).filter(User.email == invite.email).first()
        already_registered = existing is not None

    return InviteCheckResponse(
        valid=True,
        email=invite.email,
        already_registered=already_registered,
    )


@router.post("/magic-link", response_model=TokenResponse)
def magic_link_login(req: SetPasswordRequest, db: Session = Depends(get_db)):
    """
    Accept an invite, set a password, and log in.
    - If user exists (email-locked invite): activate + update password
    - If user doesn't exist: create account, activate, set password
    """
    invite = _validate_invite(db, req.invite_token)

    # If invite is email-locked, enforce it
    if invite.email and invite.email.lower() != req.email.lower():
        raise HTTPException(status_code=400, detail="This invite is for a different email address")

    user = db.query(User).filter(User.email == req.email).first()

    if user:
        # Existing user — activate and update password
        user.hashed_password = hash_password(req.password)
        user.is_active = True
        if req.full_name:
            user.full_name = req.full_name
    else:
        # New user — create account
        username = req.email.split("@")[0]
        base_username = username
        counter = 1
        while db.query(User).filter(User.username == username).first():
            username = f"{base_username}{counter}"
            counter += 1

        user = User(
            email=req.email,
            username=username,
            hashed_password=hash_password(req.password),
            full_name=req.full_name,
            is_active=True,
        )
        db.add(user)
        db.flush()

    # Consume the invite
    invite.is_used = True
    invite.used_by = user.id
    db.commit()
    db.refresh(user)

    logger.info(f"Magic link login: {req.email} (invite consumed)")
    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token)


def _user_to_response(u: User) -> UserResponse:
    return UserResponse(
        id=str(u.id),
        email=u.email,
        username=u.username,
        full_name=u.full_name,
        display_name=getattr(u, "display_name", None),
        picture_url=getattr(u, "picture_url", None),
        is_admin=u.is_admin,
        is_active=u.is_active,
        must_change_password=u.must_change_password,
    )


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user_allow_pending)):
    return _user_to_response(current_user)


@router.patch("/me", response_model=UserResponse)
def update_me(
    payload: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user_allow_pending),
    db: Session = Depends(get_db),
):
    """Update editable profile fields. Caps and trims server-side so a
    rogue client can't post a 10KB display name."""
    if payload.display_name is not None:
        v = payload.display_name.strip()[:60]
        current_user.display_name = v or None
    if payload.full_name is not None:
        v = payload.full_name.strip()[:120]
        current_user.full_name = v or None
    db.commit()
    db.refresh(current_user)
    return _user_to_response(current_user)


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user_allow_pending),
    db: Session = Depends(get_db),
):
    """Change password. Clears the must_change_password flag."""
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    current_user.hashed_password = hash_password(req.new_password)
    current_user.must_change_password = False
    db.commit()
    return {"detail": "Password changed"}


class DirectoryEntry(BaseModel):
    id: str
    email: str
    full_name: str | None


@router.get("/directory", response_model=list[DirectoryEntry])
def user_directory(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Minimal directory of approved users — used by share dialogs to pick
    a target user. Excludes pending/disabled accounts and the requester
    themselves."""
    users = (
        db.query(User)
        .filter(User.is_active == True, User.id != current_user.id)
        .order_by(User.email)
        .all()
    )
    return [
        DirectoryEntry(id=str(u.id), email=u.email, full_name=u.full_name)
        for u in users
    ]
