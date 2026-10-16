"""Contract / vendor / payment routes."""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models.contract import Vendor, Contract, Payment
from models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


def _normalize(s: str) -> str:
    return " ".join((s or "").lower().split())


# ─── Vendors ─────────────────────────────────────────────────────────────────

class VendorIn(BaseModel):
    name: str
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    ein: Optional[str] = None
    category: Optional[str] = None
    notes: Optional[str] = None


class VendorOut(BaseModel):
    id: str
    name: str
    category: Optional[str]
    contract_count: int
    payment_total: float
    created_at: str

    class Config:
        from_attributes = True


@router.get("/vendors", response_model=List[VendorOut])
def list_vendors(
    q: Optional[str] = Query(None, description="Substring match on name"),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Vendor)
    if q:
        query = query.filter(Vendor.normalized_name.ilike(f"%{_normalize(q)}%"))
    if category:
        query = query.filter(Vendor.category == category)

    vendors = query.order_by(Vendor.name).all()

    out: List[VendorOut] = []
    for v in vendors:
        contract_count = db.query(func.count(Contract.id)).filter(Contract.vendor_id == v.id).scalar() or 0
        payment_total = db.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(Payment.vendor_id == v.id).scalar() or 0.0
        out.append(VendorOut(
            id=str(v.id), name=v.name, category=v.category,
            contract_count=int(contract_count), payment_total=float(payment_total),
            created_at=v.created_at.isoformat(),
        ))
    return out


@router.post("/vendors")
def create_vendor(
    body: VendorIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    norm = _normalize(body.name)
    if not norm:
        raise HTTPException(status_code=400, detail="name is required")

    # Dedupe by normalized name
    existing = db.query(Vendor).filter(Vendor.normalized_name == norm).first()
    if existing:
        return {"id": str(existing.id), "created": False}

    v = Vendor(
        name=body.name.strip(), normalized_name=norm,
        address=body.address, city=body.city, state=body.state, zip_code=body.zip_code,
        ein=body.ein, category=body.category, notes=body.notes,
    )
    db.add(v); db.commit(); db.refresh(v)
    return {"id": str(v.id), "created": True}


@router.get("/vendors/{vendor_id}/contracts")
def vendor_contracts(
    vendor_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contracts = db.query(Contract).filter(Contract.vendor_id == vendor_id).order_by(Contract.awarded_date.desc()).all()
    return [
        {
            "id": str(c.id), "title": c.title, "amount": c.amount,
            "fiscal_year": c.fiscal_year, "contract_type": c.contract_type,
            "awarded_date": c.awarded_date.isoformat() if c.awarded_date else None,
            "status": c.status,
        } for c in contracts
    ]


# ─── Contracts ───────────────────────────────────────────────────────────────

class ContractIn(BaseModel):
    vendor_name: str
    entity_type: str
    title: str
    contract_type: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[float] = None
    fiscal_year: Optional[str] = None
    document_id: Optional[str] = None
    contract_number: Optional[str] = None
    awarded_date: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    authorizing_resolution: Optional[str] = None
    funded_from: Optional[str] = None
    bid_threshold_applies: Optional[str] = None
    paytoplay_required: Optional[str] = None


@router.get("/contracts")
def list_contracts(
    entity_type: Optional[str] = Query(None),
    fiscal_year: Optional[str] = Query(None),
    vendor: Optional[str] = Query(None),
    min_amount: Optional[float] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Contract).join(Vendor)
    if entity_type:
        query = query.filter(Contract.entity_type == entity_type)
    if fiscal_year:
        query = query.filter(Contract.fiscal_year == fiscal_year)
    if vendor:
        query = query.filter(Vendor.normalized_name.ilike(f"%{_normalize(vendor)}%"))
    if min_amount is not None:
        query = query.filter(Contract.amount >= min_amount)
    rows = query.order_by(Contract.awarded_date.desc().nullslast()).limit(500).all()
    return [
        {
            "id": str(c.id), "vendor_id": str(c.vendor_id), "vendor_name": c.vendor.name,
            "entity_type": c.entity_type, "title": c.title, "amount": c.amount,
            "fiscal_year": c.fiscal_year, "contract_type": c.contract_type,
            "awarded_date": c.awarded_date.isoformat() if c.awarded_date else None,
            "authorizing_resolution": c.authorizing_resolution, "status": c.status,
        } for c in rows
    ]


@router.post("/contracts")
def create_contract(
    body: ContractIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from datetime import date
    # find or create vendor
    norm = _normalize(body.vendor_name)
    vendor = db.query(Vendor).filter(Vendor.normalized_name == norm).first()
    if not vendor:
        vendor = Vendor(name=body.vendor_name.strip(), normalized_name=norm)
        db.add(vendor); db.flush()

    def _date(s: Optional[str]) -> Optional[date]:
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None

    c = Contract(
        vendor_id=vendor.id, entity_type=body.entity_type, title=body.title,
        contract_type=body.contract_type, description=body.description,
        amount=body.amount, fiscal_year=body.fiscal_year,
        document_id=body.document_id, contract_number=body.contract_number,
        awarded_date=_date(body.awarded_date), start_date=_date(body.start_date),
        end_date=_date(body.end_date),
        authorizing_resolution=body.authorizing_resolution, funded_from=body.funded_from,
        bid_threshold_applies=body.bid_threshold_applies,
        paytoplay_required=body.paytoplay_required,
    )
    db.add(c); db.commit(); db.refresh(c)
    return {"id": str(c.id), "vendor_id": str(vendor.id)}


# ─── Payments ────────────────────────────────────────────────────────────────


class PaymentIn(BaseModel):
    vendor_name: str
    entity_type: str
    amount: float
    payment_date: Optional[str] = None
    fiscal_year: Optional[str] = None
    fund: Optional[str] = None
    purpose: Optional[str] = None
    voucher_number: Optional[str] = None
    check_number: Optional[str] = None
    document_id: Optional[str] = None


@router.get("/payments")
def list_payments(
    entity_type: Optional[str] = Query(None),
    fiscal_year: Optional[str] = Query(None),
    vendor: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Payment).join(Vendor)
    if entity_type:
        query = query.filter(Payment.entity_type == entity_type)
    if fiscal_year:
        query = query.filter(Payment.fiscal_year == fiscal_year)
    if vendor:
        query = query.filter(Vendor.normalized_name.ilike(f"%{_normalize(vendor)}%"))
    rows = query.order_by(Payment.payment_date.desc().nullslast()).limit(500).all()
    return [
        {
            "id": str(p.id), "vendor_id": str(p.vendor_id), "vendor_name": p.vendor.name,
            "entity_type": p.entity_type, "amount": p.amount, "fund": p.fund,
            "fiscal_year": p.fiscal_year, "purpose": p.purpose,
            "payment_date": p.payment_date.isoformat() if p.payment_date else None,
            "voucher_number": p.voucher_number, "check_number": p.check_number,
        } for p in rows
    ]
