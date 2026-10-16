"""Google ID-token verification — wraps google-auth so the route handler
stays small and the test surface (in `routes/auth.py`) doesn't need to
know about Google's libraries directly.

Server-side verification is mandatory; never trust a decoded-on-the-
frontend payload. google.oauth2.id_token.verify_oauth2_token fetches the
current JWKS from Google, verifies the signature, the issuer, the
audience (must match our GOOGLE_OAUTH_CLIENT_ID), and the expiry.
"""
from __future__ import annotations

import logging
from typing import Optional, TypedDict

logger = logging.getLogger(__name__)


class GoogleIdentity(TypedDict, total=False):
    sub: str           # stable Google user id
    email: str
    email_verified: bool
    name: str | None
    picture: str | None
    given_name: str | None
    family_name: str | None


def verify_id_token(id_token_str: str, client_id: str) -> Optional[GoogleIdentity]:
    """Verify a Google ID token; return the identity dict or None on failure.

    Failures (bad signature, wrong audience, expired) are logged at WARNING
    and the caller should respond 401. The lazy import keeps the
    google-auth dependency optional — if the lib isn't installed, return
    None so the route can 503.
    """
    if not id_token_str or not client_id:
        return None

    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests
    except ImportError:
        logger.warning("google-auth not installed; cannot verify Google ID tokens")
        return None

    try:
        claims = google_id_token.verify_oauth2_token(
            id_token_str,
            google_requests.Request(),
            client_id,
            clock_skew_in_seconds=10,
        )
    except ValueError as exc:
        logger.warning("Google ID token verification failed: %s", exc)
        return None

    # Defensive: ensure email_verified is true. Google docs strongly
    # recommend rejecting tokens for unverified emails to prevent the
    # "I made an account at attacker.com with your-email@gmail.com" attack.
    if claims.get("email") and not claims.get("email_verified"):
        logger.warning("Google ID token has email_verified=false; rejecting")
        return None

    return {
        "sub": claims.get("sub"),
        "email": (claims.get("email") or "").lower().strip(),
        "email_verified": bool(claims.get("email_verified")),
        "name": claims.get("name") or None,
        "picture": claims.get("picture") or None,
        "given_name": claims.get("given_name") or None,
        "family_name": claims.get("family_name") or None,
    }
