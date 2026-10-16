"""Financial analysis models for town and school financial statements.

Schema notes (NJ government accounting):
- Fund: General/Capital/Debt Service/Special Revenue/Trust/Enterprise. Required to
  reason about financial health; funds don't intermix.
- NJ school account codes follow program-function-object: e.g. "11-130-100-101".
    program  = "11"  (General Current Expense), "12" (Capital Outlay), "13" (Special Schools)
    function = "130" (Regular Programs - Instruction), "230" (Support - Admin), etc.
    object   = "100" (Salaries), "200" (Benefits), "300" (Purchased Services), "500" (Capital Outlay)
- Reconcile pass sums line items by section and compares to the reported summary —
  divergence > 0.5% gets flagged.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Integer, ForeignKey, Text, Boolean, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from database import Base


class FinancialStatement(Base):
    """A parsed financial statement (budget, audit, CAFR, etc.)."""
    __tablename__ = "financial_statements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    entity_name = Column(String, nullable=True)
    entity_type = Column(String, nullable=False)  # "town", "school"
    statement_type = Column(String, nullable=False)  # "budget", "audit", "acfr", "afs", "ufb", "annual_report"
    fiscal_year = Column(String, nullable=False)
    fiscal_year_start = Column(DateTime, nullable=True)
    fiscal_year_end = Column(DateTime, nullable=True)

    # NJ municipal AFS uses regulatory basis; NJ schools use GAAP/GASB.
    # Health metrics, fund balance rules, and salary ratios differ between the two.
    accounting_basis = Column(String, nullable=True)  # "gaap" (schools/ACFR) or "nj_regulatory" (NJ municipal AFS)
    # AH borough is calendar year; HHRSD is school year (July-June).
    fiscal_calendar = Column(String, nullable=True)  # "calendar_year" / "school_year" / "sfy"
    # For HHRSD pre-7/1/2024 data, identify which predecessor (AHSD / HSD / HHRS-HS) — values can't be summed.
    predecessor_entity = Column(String, nullable=True)

    # Summary figures
    total_revenue = Column(Float, nullable=True)
    total_expenditures = Column(Float, nullable=True)
    surplus_deficit = Column(Float, nullable=True)
    fund_balance = Column(Float, nullable=True)
    total_debt = Column(Float, nullable=True)

    # Raw extraction data
    raw_extraction = Column(JSONB, default={})
    extraction_model = Column(String, nullable=True)
    extraction_confidence = Column(Float, nullable=True)
    extraction_pass = Column(Integer, default=0)  # how many passes ran

    # Reconciliation: sum(line_items by section) vs reported totals
    reconcile_status = Column(String, default="not_attempted")  # balanced, off_lt_1pct, off_gt_1pct, unbalanced, not_attempted
    reconcile_details = Column(JSONB, default={})

    # Anomaly flags from rule-based detection (see services/financial_anomaly.py)
    anomaly_flags = Column(JSONB, default=list)

    # Drill-down results (revenue/expenditure/debt/fund_balance) — written by the agent orchestrator
    drill_results = Column(JSONB, default={})

    status = Column(String, default="pending")  # pending, processing, extracted, drilled, verified, error
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    line_items = relationship("FinancialLineItem", back_populates="statement", cascade="all, delete-orphan")


class FinancialLineItem(Base):
    """Individual line items from a financial statement."""
    __tablename__ = "financial_line_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    statement_id = Column(UUID(as_uuid=True), ForeignKey("financial_statements.id"), nullable=False)
    section = Column(String, nullable=True)  # Revenue / Expenditures / Assets / Liabilities / Fund Balance / Debt Service
    subsection = Column(String, nullable=True)
    line_name = Column(String, nullable=False)
    amount = Column(Float, nullable=True)
    prior_year_amount = Column(Float, nullable=True)
    budget_amount = Column(Float, nullable=True)
    variance = Column(Float, nullable=True)
    variance_pct = Column(Float, nullable=True)

    # Fund classification (NJ governmental accounting)
    fund = Column(String, nullable=True, index=True)  # general, capital, debt_service, special_revenue, trust, enterprise

    # NJ school district account-code structure (program-function-object)
    account_code = Column(String, nullable=True, index=True)  # full string e.g. "11-130-100-101"
    program_code = Column(String, nullable=True)
    function_code = Column(String, nullable=True)
    object_code = Column(String, nullable=True)

    # True for subtotals/totals lines (so summing won't double-count them)
    is_total_row = Column(Boolean, default=False)
    line_order = Column(Integer, nullable=True)

    # YoY change in pct, computed downstream
    yoy_change_pct = Column(Float, nullable=True)

    # Per-line anomaly flags
    anomaly_flags = Column(JSONB, default=list)

    metadata_ = Column("metadata", JSONB, default={})

    statement = relationship("FinancialStatement", back_populates="line_items")


Index("ix_line_items_statement_section", FinancialLineItem.statement_id, FinancialLineItem.section)
Index("ix_line_items_fund", FinancialLineItem.fund)


class FinancialAnalysis(Base):
    """Stored analysis results comparing financial statements over time."""
    __tablename__ = "financial_analyses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    analysis_type = Column(String, nullable=False)  # trend / comparison / ratio / variance / anomaly / drill_synthesis
    fiscal_years = Column(JSONB, default=[])
    statement_ids = Column(JSONB, default=[])
    results = Column(JSONB, default={})
    summary = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
