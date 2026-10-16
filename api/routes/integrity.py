"""
Integrity API — red flags with evidence, the facts behind them, and review.

  GET   /api/integrity/summary
  GET   /api/integrity/flags?rule=&severity=&status=&subject=&entity=
  PATCH /api/integrity/flags/{id}           {status, reviewer_note}
  GET   /api/integrity/awards?vendor=&procurement=&action=
  GET   /api/integrity/vendors              spend + procurement mix per vendor
  POST  /api/integrity/run                  (admin) extract + rules now
  POST  /api/integrity/contributions        (admin) upload a campaign-finance CSV
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from auth import get_admin_user, get_current_user
from database import get_db
from models.integrity import Award, IntegrityFlag
from models.user import User

router = APIRouter()

_SEV_ORDER = case((IntegrityFlag.severity == "high", 0), (IntegrityFlag.severity == "medium", 1), else_=2)
STATUSES = {"open", "reviewing", "dismissed", "confirmed"}


def _flag_dict(f: IntegrityFlag) -> dict:
    return {
        "id": str(f.id), "rule": f.rule, "severity": f.severity, "score": f.score, "title": f.title,
        "explanation": f.explanation, "entity_key": f.entity_key, "subject": f.subject, "amount": f.amount,
        "evidence": f.evidence or [], "status": f.status, "reviewer_note": f.reviewer_note,
        "reviewed_by": f.reviewed_by, "reviewed_at": f.reviewed_at.isoformat() if f.reviewed_at else None,
        "first_seen": f.first_seen.isoformat() if f.first_seen else None,
    }


@router.get("/summary")
def summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    active = db.query(IntegrityFlag).filter(IntegrityFlag.active == 1)
    by_sev = dict(active.filter(IntegrityFlag.status.in_(["open", "reviewing"]))
                  .with_entities(IntegrityFlag.severity, func.count()).group_by(IntegrityFlag.severity).all())
    by_rule = dict(active.with_entities(IntegrityFlag.rule, func.count()).group_by(IntegrityFlag.rule).all())
    by_status = dict(active.with_entities(IntegrityFlag.status, func.count()).group_by(IntegrityFlag.status).all())
    spend = db.query(func.count(Award.id), func.coalesce(func.sum(Award.amount), 0)).filter(
        Award.action_type.in_(["contract", "purchase", "change_order"])).one()
    return {"open_by_severity": by_sev, "by_rule": by_rule, "by_status": by_status,
            "awards": spend[0], "award_total": float(spend[1] or 0)}


@router.get("/flags")
def list_flags(rule: Optional[str] = None, severity: Optional[str] = None, status: Optional[str] = None,
               subject: Optional[str] = None, entity: Optional[str] = None, include_inactive: bool = False,
               limit: int = 200, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(IntegrityFlag)
    if not include_inactive:
        q = q.filter(IntegrityFlag.active == 1)
    if rule:
        q = q.filter(IntegrityFlag.rule == rule)
    if severity:
        q = q.filter(IntegrityFlag.severity == severity)
    if status:
        q = q.filter(IntegrityFlag.status == status)
    if subject:
        q = q.filter(IntegrityFlag.subject.ilike(f"%{subject}%"))
    if entity:
        q = q.filter(IntegrityFlag.entity_key == entity)
    rows = q.order_by(_SEV_ORDER, IntegrityFlag.score.desc()).limit(min(limit, 1000)).all()
    return [_flag_dict(f) for f in rows]


class FlagReview(BaseModel):
    status: str
    reviewer_note: Optional[str] = None


@router.patch("/flags/{flag_id}")
def review_flag(flag_id: str, body: FlagReview, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    if body.status not in STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(STATUSES)}")
    f = db.get(IntegrityFlag, flag_id)
    if not f:
        raise HTTPException(404, "flag not found")
    f.status, f.reviewer_note = body.status, body.reviewer_note
    f.reviewed_by, f.reviewed_at = user.email, datetime.utcnow()
    db.commit()
    return _flag_dict(f)


@router.get("/awards")
def list_awards(vendor: Optional[str] = None, procurement: Optional[str] = None, action: Optional[str] = None,
                limit: int = 300, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(Award)
    if vendor:
        q = q.filter(Award.vendor_name.ilike(f"%{vendor}%"))
    if procurement:
        q = q.filter(Award.procurement == procurement)
    if action:
        q = q.filter(Award.action_type == action)
    rows = q.order_by(Award.meeting_date.desc().nullslast(), Award.amount.desc().nullslast()).limit(min(limit, 2000)).all()
    return [{"id": str(a.id), "document_id": str(a.document_id), "meeting_date": a.meeting_date.isoformat() if a.meeting_date else None,
             "item_no": a.item_no, "page": a.page, "title": a.title, "vendor_name": a.vendor_name, "amount": a.amount,
             "action_type": a.action_type, "procurement": a.procurement, "bids_received": a.bids_received,
             "account_code": a.account_code, "quote": a.quote} for a in rows]


@router.get("/vendors")
def vendors(limit: int = 100, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    noncomp = case((Award.procurement.in_(["professional_services", "no_bid", "sole_source", "unknown", "emergency"]), Award.amount), else_=0)
    rows = (db.query(Award.vendor_norm, func.max(Award.vendor_name), func.count(Award.id),
                     func.coalesce(func.sum(Award.amount), 0), func.coalesce(func.sum(noncomp), 0),
                     func.min(Award.meeting_date), func.max(Award.meeting_date))
            .filter(Award.vendor_norm.isnot(None), Award.action_type.in_(["contract", "purchase", "change_order", "other"]))
            .group_by(Award.vendor_norm).order_by(func.sum(Award.amount).desc().nullslast()).limit(limit).all())
    flags = dict(db.query(func.lower(IntegrityFlag.subject), func.count()).filter(IntegrityFlag.active == 1)
                 .group_by(func.lower(IntegrityFlag.subject)).all())
    return [{"vendor_norm": r[0], "vendor": r[1], "awards": r[2], "total": float(r[3]), "noncompetitive_total": float(r[4]),
             "noncompetitive_share": (float(r[4]) / float(r[3])) if r[3] else None,
             "first": r[5].isoformat() if r[5] else None, "last": r[6].isoformat() if r[6] else None,
             "flags": flags.get((r[1] or "").lower(), 0)} for r in rows]


@router.post("/run")
def run_now(force: bool = False, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    from services.integrity.runner import run_all
    return run_all(db, force=force)


@router.post("/contributions")
async def upload_contributions(file: UploadFile = File(...), source: str = Form(...),
                               db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    from services.integrity.runner import import_contributions_csv, run_all
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        tmp.write(await file.read())
        path = tmp.name
    try:
        n = import_contributions_csv(db, path, source)
    finally:
        os.unlink(path)
    return {"imported": n, "rules": run_all(db)}
