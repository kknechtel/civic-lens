"""
Integrity tables: facts extracted from public records, and red flags raised on them.

Everything here is derived and re-buildable from the document corpus:
`services.integrity` deletes and re-extracts a document's facts, then re-runs
the rules. Flags keep their review status across re-runs via a stable
fingerprint, so a reviewer's "dismissed" sticks.

A flag is a lead for human review — an anomaly with evidence — never a
finding of wrongdoing.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database import Base


class Award(Base):
    """A spending decision in a resolution: contract, purchase, payout, transfer…"""
    __tablename__ = "integrity_awards"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_key = Column(String, index=True)
    meeting_date = Column(Date, index=True)
    item_no = Column(String)
    page = Column(Integer)
    title = Column(Text)
    vendor_name = Column(String)
    vendor_norm = Column(String, index=True)
    vendor_address = Column(String)
    amount = Column(Float)
    # contract | purchase | payout | transfer | settlement | change_order | grant | lease | other
    action_type = Column(String, index=True)
    # competitive_bid | rfp | emergency | professional_services | state_contract | sole_source | no_bid | unknown
    procurement = Column(String, index=True)
    bids_received = Column(Integer)
    account_code = Column(String)
    quote = Column(Text)  # evidence excerpt
    # base_amount/new_total (change orders), funds (bill lists), p2p_disclosure,
    # meeting_type, votes, withdrawn …
    details = Column(JSONB, default=dict)
    extracted_by = Column(String, default="rules")
    created_at = Column(DateTime, default=datetime.utcnow)


class PersonnelAction(Base):
    """A hire, promotion, raise or separation from a personnel report/resolution."""
    __tablename__ = "integrity_personnel"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_key = Column(String, index=True)
    last_name = Column(String, index=True)
    first_name = Column(String)
    department = Column(String)
    action = Column(String)
    position = Column(String)
    salary = Column(String)
    effective = Column(String)
    page = Column(Integer)
    quote = Column(Text)


class Official(Base):
    """An elected/appointed official seen in proceedings (roll call) or rosters."""
    __tablename__ = "integrity_officials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
    entity_key = Column(String, index=True)
    name = Column(String)
    last_name = Column(String, index=True)
    role = Column(String)
    seen_on = Column(Date)


class Contribution(Base):
    """Campaign contribution, imported from a state/local campaign-finance dataset."""
    __tablename__ = "integrity_contributions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(String)  # dataset name, e.g. "NJ ELEC", "NYS BOE"
    contributor = Column(String)
    contributor_norm = Column(String, index=True)
    employer = Column(String)
    employer_norm = Column(String, index=True)
    address = Column(String)
    recipient = Column(String)  # committee/candidate
    amount = Column(Float)
    date = Column(Date, index=True)
    raw = Column(JSONB, default=dict)


class IntegrityFlag(Base):
    __tablename__ = "integrity_flags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fingerprint = Column(String, unique=True, nullable=False)  # rule + subject + scope; stable across runs
    rule = Column(String, index=True, nullable=False)
    severity = Column(String, index=True)  # low | medium | high
    score = Column(Float, default=0)
    title = Column(Text, nullable=False)
    explanation = Column(Text)  # why this matters and what would explain it innocently
    entity_key = Column(String, index=True)
    subject = Column(String, index=True)  # vendor / person / account the flag is about
    amount = Column(Float)
    evidence = Column(JSONB, default=list)  # [{document_id, title, page, quote, url}]
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    active = Column(Integer, default=1)  # 0 when a re-run no longer produces it
    status = Column(String, default="open", index=True)  # open | reviewing | dismissed | confirmed
    reviewer_note = Column(Text)
    reviewed_by = Column(String)
    reviewed_at = Column(DateTime)

    __table_args__ = (Index("ix_integrity_flags_rank", "active", "severity", "score"),)
