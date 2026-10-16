"""
Who pays, with which key, for which model.

    route = resolve("chat", user_id)        # raises NoAIAccess with a reason
    client(route) … record_usage(..., payer=route.payer)

Resolution for a feature triggered by a user:
  1. The user's own key for a provider that can serve the role → user pays.
     (A user with only a Gemini key gets Gemini even if the deployment
     routes chat to Claude; a user may also pick their own model.)
  2. Otherwise, if the admin sponsors the feature and the deployment has a key
     and neither the deployment's nor the user's monthly cap is spent → the
     deployment pays.
  3. Otherwise NoAIAccess: "add your own key in Settings".
Background features (no user) only ever use deployment keys, falling back to
the environment variables so existing installs keep working.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import text

from . import catalog
from .crypto import decrypt, encrypt

log = logging.getLogger(__name__)


class NoAIAccess(Exception):
    def __init__(self, feature: str, reason: str):
        super().__init__(reason)
        self.feature, self.reason = feature, reason


@dataclass(frozen=True)
class Route:
    feature: str
    role: str
    provider: str
    model: str
    key: str
    payer: str  # user | deployment


# ── settings ────────────────────────────────────────────────────────────────

def _session():
    from database import SessionLocal
    return SessionLocal()


def get_setting(db, name: str, default):
    from models.ai import AiSetting
    row = db.get(AiSetting, name)
    return row.value if row and row.value is not None else default


def set_setting(db, name: str, value, user_id=None) -> None:
    from models.ai import AiSetting
    row = db.get(AiSetting, name)
    if row:
        row.value, row.updated_by = value, user_id
    else:
        db.add(AiSetting(key=name, value=value, updated_by=user_id))
    db.commit()


def settings(db) -> dict:
    routes = {r: catalog.DEFAULT_ROUTES[r][0] for r in catalog.ROLES}
    for role, v in (get_setting(db, "routes", {}) or {}).items():
        if role in routes and v.get("provider") and v.get("model"):
            routes[role] = (v["provider"], v["model"])
    return {
        "routes": routes,
        "sponsored": set(get_setting(db, "sponsored", sorted(catalog.DEFAULT_SPONSORED))),
        "caps": {**catalog.DEFAULT_CAPS, **(get_setting(db, "caps", {}) or {})},
        "rates": {**catalog.DEFAULT_RATES, **{k: tuple(v) for k, v in (get_setting(db, "rates", {}) or {}).items()}},
    }


# ── keys ────────────────────────────────────────────────────────────────────

def _key_row(db, provider: str, user_id=None):
    from models.ai import ApiKey
    q = db.query(ApiKey).filter(ApiKey.provider == provider)
    q = q.filter(ApiKey.owner_type == "user", ApiKey.user_id == user_id) if user_id else \
        q.filter(ApiKey.owner_type == "deployment")
    return q.first()


def store_key(db, provider: str, plaintext: str, *, user_id=None, created_by=None):
    from models.ai import ApiKey
    if provider not in catalog.PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")
    plaintext = plaintext.strip()
    if len(plaintext) < 8:
        raise ValueError("that doesn't look like an API key")
    row = _key_row(db, provider, user_id)
    if row is None:
        row = ApiKey(owner_type="user" if user_id else "deployment", user_id=user_id, provider=provider)
        db.add(row)
    row.ciphertext, row.last4, row.status, row.status_detail = encrypt(plaintext), plaintext[-4:], "untested", None
    row.created_by, row.created_at, row.verified_at = created_by, datetime.utcnow(), None
    db.commit()
    return row


def delete_key(db, provider: str, *, user_id=None) -> bool:
    row = _key_row(db, provider, user_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def _plain(row) -> Optional[str]:
    return decrypt(row.ciphertext) if row and row.status != "invalid" else None


def deployment_key(provider: str, db=None) -> Optional[str]:
    """Deployment key from the admin UI, else the environment variable."""
    own = db is None
    db = db or _session()
    try:
        k = _plain(_key_row(db, provider))
    except Exception:  # tables not created yet (first boot, scripts)
        db.rollback()
        k = None
    finally:
        if own:
            db.close()
    return k or os.getenv(catalog.PROVIDERS.get(provider, {}).get("env", ""), "") or None


def user_key(db, provider: str, user_id) -> Optional[str]:
    return _plain(_key_row(db, provider, user_id)) if user_id else None


# ── spend ───────────────────────────────────────────────────────────────────

def month_spend(db, *, payer: str, user_id=None) -> float:
    q = ("SELECT coalesce(sum(estimated_cost_usd), 0) FROM llm_usage "
         "WHERE created_at >= date_trunc('month', now()) AND coalesce(payer, 'deployment') = :payer")
    params = {"payer": payer}
    if user_id:
        q += " AND user_id = CAST(:uid AS uuid)"
        params["uid"] = str(user_id)
    try:
        return float(db.execute(text(q), params).scalar() or 0)
    except Exception:
        db.rollback()
        return 0.0


# ── resolution ──────────────────────────────────────────────────────────────

def _candidates(role: str, primary: tuple[str, str]) -> list[tuple[str, str]]:
    seen, out = set(), []
    for p, m in [primary, *catalog.DEFAULT_ROUTES[role]]:
        if p not in seen:
            seen.add(p)
            out.append((p, m))
    return out


def resolve(feature: str, user_id=None, db=None) -> Route:
    role, interactive = catalog.FEATURES[feature]
    own = db is None
    db = db or _session()
    try:
        s = settings(db)
        primary = s["routes"][role]
        cands = _candidates(role, primary)
        if user_id:
            # A user's own model choice applies to their own key only, never to
            # the deployment's (no picking the priciest model on the town's dime).
            prefs = get_setting(db, f"user_prefs:{user_id}", {}) or {}
            pref = (prefs.get("routes") or {}).get(role)
            mine = ([(pref["provider"], pref["model"])] if pref and pref.get("provider") and pref.get("model") else []) + cands
            for p, m in mine:
                k = user_key(db, p, user_id)
                if k:
                    return Route(feature, role, p, m, k, "user")
        if interactive and feature not in s["sponsored"]:
            raise NoAIAccess(feature, "This deployment doesn't pay for this feature. Add your own API key in Settings.")
        if interactive and user_id:
            if month_spend(db, payer="deployment") >= s["caps"]["deployment_monthly_usd"]:
                raise NoAIAccess(feature, "This month's AI budget is used up. Add your own API key in Settings to keep going.")
            if month_spend(db, payer="deployment", user_id=user_id) >= s["caps"]["user_monthly_usd"]:
                raise NoAIAccess(feature, "You've used your free AI allowance for this month. Add your own API key in Settings.")
        for p, m in cands:
            k = deployment_key(p, db)
            if k:
                return Route(feature, role, p, m, k, "deployment")
        raise NoAIAccess(feature, "No AI provider is configured. An admin can add one in Admin → AI, "
                                  "or you can add your own key in Settings.")
    finally:
        if own:
            db.close()


def available(feature: str, user_id=None) -> tuple[bool, str]:
    try:
        r = resolve(feature, user_id)
        return True, f"{r.provider}:{r.model} ({'your key' if r.payer == 'user' else 'deployment'})"
    except NoAIAccess as e:
        return False, e.reason


def estimate_cost(model: str, input_tokens: int, output_tokens: int, rates: dict | None = None) -> tuple[float, bool]:
    """(usd, is_estimate). Unknown models use the nearest family's rate."""
    rates = rates or catalog.DEFAULT_RATES
    r, estimated = rates.get(model), False
    if r is None:
        estimated = True
        base = next((fam for frag, fam in catalog.FAMILY_FALLBACK if frag in model.lower()), "claude-sonnet-4-6")
        r = rates.get(base) or catalog.DEFAULT_RATES[base]
    return (input_tokens * r[0] + output_tokens * r[1]) / 1_000_000, estimated


def model_for(role: str, provider: str, db=None) -> str:
    """The model this deployment uses for `role` on `provider` (call sites tied to one SDK)."""
    own = db is None
    db = db or _session()
    try:
        p, m = settings(db)["routes"][role]
    except Exception:
        p, m = catalog.DEFAULT_ROUTES[role][0]
    finally:
        if own:
            db.close()
    if p == provider:
        return m
    return next((mm for pp, mm in catalog.DEFAULT_ROUTES[role] if pp == provider), catalog.MODELS[provider][0])


def pin(feature: str, provider: str, user_id=None) -> Optional[Route]:
    """Route for `feature` restricted to one provider, or None if no key for it.

    For code written against a single SDK. Interactive features honour the
    same who-pays rules as resolve(): the user's own key first, then the
    deployment's if the feature is sponsored and within caps."""
    role, interactive = catalog.FEATURES[feature]
    db = _session()
    try:
        model = model_for(role, provider, db)
        if user_id:
            k = user_key(db, provider, user_id)
            if k:
                return Route(feature, role, provider, model, k, "user")
        if interactive and user_id:
            try:
                r = resolve(feature, user_id, db)
            except NoAIAccess:
                return None
            if r.payer == "user":  # user has a key, but for another provider
                return None
        k = deployment_key(provider, db)
        return Route(feature, role, provider, model, k, "deployment") if k else None
    finally:
        db.close()
