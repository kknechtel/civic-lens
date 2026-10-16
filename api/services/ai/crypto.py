"""
Encryption for stored API keys (Fernet: AES-128-CBC + HMAC-SHA256).

The encryption key comes from AI_KEYS_SECRET (any long random string). If it
is unset, one is derived from JWT_SECRET so keys still aren't stored in the
clear, but rotating JWT_SECRET would then make stored keys unreadable: set
AI_KEYS_SECRET explicitly in production.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)
_warned = False


def _fernet() -> Fernet:
    global _warned
    secret = os.getenv("AI_KEYS_SECRET")
    if not secret:
        if not _warned:
            log.warning("AI_KEYS_SECRET is unset; deriving the key-encryption key from JWT_SECRET. "
                        "Set AI_KEYS_SECRET so rotating JWT_SECRET doesn't orphan stored API keys.")
            _warned = True
        secret = "civic-lens/ai-keys/" + (os.getenv("JWT_SECRET") or "")
    if len(secret) < 32:
        raise RuntimeError("AI_KEYS_SECRET (or JWT_SECRET) must be at least 32 characters")
    digest = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str | None:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        log.error("stored API key could not be decrypted (AI_KEYS_SECRET changed?)")
        return None
