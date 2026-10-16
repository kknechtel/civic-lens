"""Saved-alert routes — per-user subscription CRUD.

Three subscription kinds (keyword / new_meeting / new_document); the daily
digest worker (`scripts/run_digest.py`) is what actually emails them.

All endpoints require auth and operate only on the caller's own rows;
no admin-only or cross-user surface here.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models.saved_alert import AlertKind, DigestFrequency, SavedAlert
from models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────────────

class AlertCreate(BaseModel):
    kind: AlertKind
    name: str = Field(min_length=1, max_length=120)
    query: Optional[str] = Field(default=None, max_length=500)
    filters: dict = Field(default_factory=dict)
    frequency: DigestFrequency = DigestFrequency.daily
    enabled: bool = True


class AlertUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    query: Optional[str] = Field(default=None, max_length=500)
    filters: Optional[dict] = None
    frequency: Optional[DigestFrequency] = None
    enabled: Optional[bool] = None


class AlertOut(BaseModel):
    id: str
    kind: AlertKind
    name: str
    query: Optional[str]
    filters: dict
    frequency: DigestFrequency
    enabled: bool
    last_run_at: Optional[datetime]
    last_sent_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


def _to_out(a: SavedAlert) -> AlertOut:
    return AlertOut(
        id=str(a.id), kind=a.kind, name=a.name, query=a.query,
        filters=a.filters or {}, frequency=a.frequency, enabled=a.enabled,
        last_run_at=a.last_run_at, last_sent_at=a.last_sent_at,
        created_at=a.created_at,
    )


# ─── Routes ───────────────────────────────────────────────────────────

@router.get("/", response_model=list[AlertOut])
def list_alerts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = (db.query(SavedAlert)
              .filter(SavedAlert.user_id == user.id)
              .order_by(SavedAlert.created_at.desc())
              .all())
    return [_to_out(a) for a in rows]


@router.post("/", response_model=AlertOut, status_code=status.HTTP_201_CREATED)
def create_alert(
    payload: AlertCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.kind == AlertKind.keyword and not (payload.query or "").strip():
        raise HTTPException(400, "query is required for keyword alerts")

    a = SavedAlert(
        user_id=user.id,
        kind=payload.kind,
        name=payload.name.strip(),
        query=(payload.query or "").strip() or None,
        filters=payload.filters or {},
        frequency=payload.frequency,
        enabled=payload.enabled,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return _to_out(a)


@router.patch("/{alert_id}", response_model=AlertOut)
def update_alert(
    alert_id: str,
    payload: AlertUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    a = (db.query(SavedAlert)
           .filter(SavedAlert.id == alert_id, SavedAlert.user_id == user.id)
           .first())
    if not a:
        raise HTTPException(404, "alert not found")

    if payload.name is not None:
        a.name = payload.name.strip()
    if payload.query is not None:
        a.query = payload.query.strip() or None
    if payload.filters is not None:
        a.filters = payload.filters
    if payload.frequency is not None:
        a.frequency = payload.frequency
    if payload.enabled is not None:
        a.enabled = payload.enabled

    if a.kind == AlertKind.keyword and not (a.query or "").strip():
        raise HTTPException(400, "query cannot be empty for a keyword alert")

    db.commit()
    db.refresh(a)
    return _to_out(a)


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert(
    alert_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    a = (db.query(SavedAlert)
           .filter(SavedAlert.id == alert_id, SavedAlert.user_id == user.id)
           .first())
    if not a:
        raise HTTPException(404, "alert not found")
    db.delete(a)
    db.commit()
    return None
