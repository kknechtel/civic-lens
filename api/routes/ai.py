"""
AI keys and settings.

/api/ai/...        — every signed-in user: their own keys, model choices,
                     which features they can use and who pays.
/api/admin/ai/...  — admins: deployment keys, model per role, which
                     features the deployment pays for, monthly caps, rates.

Keys go in, never out: responses carry only the last four characters.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from auth import get_admin_user, get_current_user
from database import get_db
from models.ai import ApiKey
from models.user import User
from services.ai import catalog, router as ai
from services.ai.verify import verify

router = APIRouter()
admin_router = APIRouter()


class KeyIn(BaseModel):
    key: str


class RouteIn(BaseModel):
    provider: str
    model: str


class PrefsIn(BaseModel):
    routes: dict[str, Optional[RouteIn]] = {}


class SettingsIn(BaseModel):
    routes: Optional[dict[str, RouteIn]] = None
    sponsored: Optional[list[str]] = None
    caps: Optional[dict[str, float]] = None
    rates: Optional[dict[str, list[float]]] = None


def _key_view(row: Optional[ApiKey], env_set: bool = False) -> dict:
    if not row:
        return {"has_key": False, "from_env": env_set}
    return {"has_key": True, "last4": row.last4, "status": row.status, "status_detail": row.status_detail,
            "verified_at": row.verified_at, "added_at": row.created_at, "from_env": False}


def _providers(db: Session, user_id=None) -> list[dict]:
    import os
    out = []
    for pid, meta in catalog.PROVIDERS.items():
        row = ai._key_row(db, pid, user_id)
        env_set = bool(os.getenv(meta["env"])) if user_id is None else False
        out.append({"id": pid, "label": meta["label"], "key_hint": meta["key_hint"], "docs": meta["docs"],
                    "kinds": meta["kinds"], "models": catalog.MODELS.get(pid, []), **_key_view(row, env_set)})
    return out


def _test_and_save(db: Session, row: ApiKey) -> dict:
    from services.ai.crypto import decrypt
    ok, detail = verify(row.provider, decrypt(row.ciphertext) or "")
    row.status, row.status_detail = ("ok" if ok else "invalid"), detail
    row.verified_at = datetime.utcnow() if ok else None
    db.commit()
    return {"ok": ok, "detail": detail, **_key_view(row)}


def _put_key(db: Session, provider: str, key: str, owner_id, actor_id) -> dict:
    try:
        row = ai.store_key(db, provider, key, user_id=owner_id, created_by=actor_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _test_and_save(db, row)


# ── user ────────────────────────────────────────────────────────────────────

@router.get("/me")
def my_ai(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = ai.settings(db)
    features = []
    for f, (role, interactive) in catalog.FEATURES.items():
        if not interactive:
            continue
        ok, via = ai.available(f, user.id)
        features.append({"feature": f, "role": role, "available": ok, "via" if ok else "reason": via,
                         "sponsored": f in s["sponsored"]})
    prefs = ai.get_setting(db, f"user_prefs:{user.id}", {}) or {}
    return {
        "providers": _providers(db, user.id),
        "features": features,
        "roles": catalog.ROLES,
        "prefs": prefs.get("routes", {}),
        "spend_this_month": {
            "own_keys": ai.month_spend(db, payer="user", user_id=user.id),
            "sponsored": ai.month_spend(db, payer="deployment", user_id=user.id),
            "sponsored_cap": s["caps"]["user_monthly_usd"],
        },
    }


@router.put("/keys/{provider}")
def put_my_key(provider: str, body: KeyIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _put_key(db, provider, body.key, user.id, user.id)


@router.delete("/keys/{provider}")
def delete_my_key(provider: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {"deleted": ai.delete_key(db, provider, user_id=user.id)}


@router.post("/keys/{provider}/test")
def test_my_key(provider: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = ai._key_row(db, provider, user.id)
    if not row:
        raise HTTPException(404, "no key saved for this provider")
    return _test_and_save(db, row)


@router.put("/prefs")
def put_my_prefs(body: PrefsIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    routes = {r: v.model_dump() for r, v in body.routes.items() if v and r in catalog.ROLES}
    ai.set_setting(db, f"user_prefs:{user.id}", {"routes": routes}, user.id)
    return {"routes": routes}


# ── admin ───────────────────────────────────────────────────────────────────

@admin_router.get("")
def admin_ai(db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    s = ai.settings(db)
    top = db.execute(text("""
        SELECT u.email, coalesce(sum(l.estimated_cost_usd), 0) AS usd, count(*) AS calls
        FROM llm_usage l LEFT JOIN users u ON u.id = l.user_id
        WHERE l.created_at >= date_trunc('month', now()) AND coalesce(l.payer, 'deployment') = 'deployment'
        GROUP BY u.email ORDER BY usd DESC LIMIT 10""")).fetchall()
    by_feature = db.execute(text("""
        SELECT source, coalesce(sum(estimated_cost_usd), 0) AS usd, count(*) AS calls FROM llm_usage
        WHERE created_at >= date_trunc('month', now()) AND coalesce(payer, 'deployment') = 'deployment'
        GROUP BY source ORDER BY usd DESC""")).fetchall()
    return {
        "providers": _providers(db),
        "routes": {r: {"provider": p, "model": m} for r, (p, m) in s["routes"].items()},
        "roles": catalog.ROLES,
        "features": [{"feature": f, "role": r, "interactive": i, "sponsored": f in s["sponsored"]}
                     for f, (r, i) in catalog.FEATURES.items()],
        "caps": s["caps"],
        "rates": {k: list(v) for k, v in s["rates"].items()},
        "spend_this_month": {
            "deployment": ai.month_spend(db, payer="deployment"),
            "by_user": [{"user": e or "(background jobs)", "usd": float(u), "calls": c} for e, u, c in top],
            "by_feature": [{"source": src, "usd": float(u), "calls": c} for src, u, c in by_feature],
        },
    }


@admin_router.put("/keys/{provider}")
def put_deployment_key(provider: str, body: KeyIn, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    return _put_key(db, provider, body.key, None, admin.id)


@admin_router.delete("/keys/{provider}")
def delete_deployment_key(provider: str, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    return {"deleted": ai.delete_key(db, provider)}


@admin_router.post("/keys/{provider}/test")
def test_deployment_key(provider: str, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    row = ai._key_row(db, provider)
    if not row:
        raise HTTPException(404, "no deployment key saved for this provider")
    return _test_and_save(db, row)


@admin_router.put("/settings")
def put_settings(body: SettingsIn, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    if body.routes is not None:
        bad = [r for r in body.routes if r not in catalog.ROLES]
        if bad:
            raise HTTPException(400, f"unknown roles {bad}")
        ai.set_setting(db, "routes", {r: v.model_dump() for r, v in body.routes.items()}, admin.id)
    if body.sponsored is not None:
        bad = [f for f in body.sponsored if f not in catalog.FEATURES]
        if bad:
            raise HTTPException(400, f"unknown features {bad}")
        ai.set_setting(db, "sponsored", sorted(set(body.sponsored)), admin.id)
    if body.caps is not None:
        ai.set_setting(db, "caps", {k: max(0.0, float(v)) for k, v in body.caps.items()
                                    if k in catalog.DEFAULT_CAPS}, admin.id)
    if body.rates is not None:
        ai.set_setting(db, "rates", {m: [float(v[0]), float(v[1])] for m, v in body.rates.items() if len(v) == 2},
                       admin.id)
    return admin_ai(db, admin)


# ── source discovery (admin) ────────────────────────────────────────────────

class DiscoverIn(BaseModel):
    url: str
    max_pages: int = 40


@admin_router.post("/discover")
def discover_sources(body: DiscoverIn, admin: User = Depends(get_admin_user)):
    """Crawl a website and draft sources for this deployment's jurisdiction.yaml.
    Read-only: nothing is saved; the YAML goes into the repo through review."""
    from urllib.parse import urlparse

    from municipality import get_municipality
    from services.discovery import discover, to_yaml
    u = body.url.strip()
    if not urlparse(u).scheme:
        u = "https://" + u
    if urlparse(u).scheme not in ("http", "https") or not urlparse(u).netloc:
        raise HTTPException(400, "Enter a website address, e.g. https://www.example.gov")
    rep = discover([u], max_pages=max(5, min(body.max_pages, 80)))
    entity = get_municipality().primary_entity.key
    return {"pages_fetched": rep.pages_fetched, "platforms": {k: {kk: vv for kk, vv in v.items() if kk != "feeds"}
                                                              for k, v in rep.platforms.items()},
            "sources": rep.sources, "notes": rep.notes, "blocked": rep.blocked[:5],
            "yaml": to_yaml(rep, entity), "jurisdiction": get_municipality().slug}
