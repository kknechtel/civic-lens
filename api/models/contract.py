"""
Contracts, vendors, and payments — sourced from resolutions, bid awards,
purchase orders, and bills/vouchers extracted from public documents.

NJ context:
- Contracts above the bid threshold (currently $44,000 per N.J.S.A. 40A:11-3) require
  competitive bidding unless an exception applies.
- Pay-to-play (N.J.S.A. 19:44A-20.5) creates extra disclosures for contracts >$17,500.
- Professional services contracts use a fair-and-open or extraordinary-unspecifiable-services
  process and are NOT subject to bid threshold but ARE subject to pay-to-play.
- Bills/vouchers (paid bills list) are immediate-access OPRA records.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Integer, ForeignKey, Text, Date, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from database import Base


class Vendor(Base):
    """A vendor or service provider that has done business with the entity."""
    __tablename__ = "vendors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False, index=True)
    normalized_name = Column(String, nullable=False, index=True)  # lowercased + stripped, for dedup
    address = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)
    ein = Column(String, nullable=True)  # employer identification number, if known
    category = Column(String, nullable=True)  # legal, engineering, construction, professional_services, supplies, etc.
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    contracts = relationship("Contract", back_populates="vendor", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="vendor", cascade="all, delete-orphan")


class Contract(Base):
    """A contract or award. May reference an authorizing resolution + the bid solicitation."""
    __tablename__ = "contracts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id"), nullable=False)
    entity_type = Column(String, nullable=False)  # town | school
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=True)  # source doc

    contract_number = Column(String, nullable=True)
    title = Column(String, nullable=False)
    contract_type = Column(String, nullable=True)  # bid | professional_services | competitive_contracting | shared_services | grant_funded | sole_source | emergency
    description = Column(Text, nullable=True)

    amount = Column(Float, nullable=True)            # not-to-exceed / award amount
    spent_to_date = Column(Float, nullable=True)
    fiscal_year = Column(String, nullable=True)

    awarded_date = Column(Date, nullable=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)

    # NJ-specific
    bid_threshold_applies = Column(String, nullable=True)  # yes | no | exception
    paytoplay_required = Column(String, nullable=True)     # yes | no
    authorizing_resolution = Column(String, nullable=True)  # e.g. "Resolution 2024-187"
    funded_from = Column(String, nullable=True)            # general | capital | grant | water_sewer

    status = Column(String, default="active")  # active | completed | terminated | superseded

    extracted_metadata = Column(JSONB, default={})

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = relationship("Vendor", back_populates="contracts")


Index("ix_contracts_entity_fy", Contract.entity_type, Contract.fiscal_year)


class Payment(Base):
    """A payment / voucher / bill paid to a vendor. Sourced from bills lists or extracted from minutes."""
    __tablename__ = "payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id"), nullable=False)
    contract_id = Column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=True)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=True)
    entity_type = Column(String, nullable=False)
    fiscal_year = Column(String, nullable=True)

    amount = Column(Float, nullable=False)
    payment_date = Column(Date, nullable=True)
    fund = Column(String, nullable=True)  # current | general | capital | water_sewer | trust | grant
    purpose = Column(Text, nullable=True)
    voucher_number = Column(String, nullable=True)
    check_number = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    vendor = relationship("Vendor", back_populates="payments")


Index("ix_payments_entity_fy", Payment.entity_type, Payment.fiscal_year)
Index("ix_payments_vendor_fy", Payment.vendor_id, Payment.fiscal_year)
