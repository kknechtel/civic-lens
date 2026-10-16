"""Financial analysis routes - extract, analyze, and compare financial statements."""
import asyncio
import logging
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models.document import Document
from models.financial import FinancialStatement, FinancialLineItem, FinancialAnalysis
from models.user import User
from auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _entity_name(entity_type: str | None) -> str:
    from municipality import get_municipality
    m = get_municipality()
    if entity_type == "school":
        school = next((e for e in m.entities if e.kind == "school"), None)
        return school.label if school else f"{m.short_name} School District"
    return m.name


class StatementResponse(BaseModel):
    id: str
    document_id: str
    entity_name: str
    entity_type: str
    statement_type: str
    fiscal_year: str
    total_revenue: float | None
    total_expenditures: float | None
    surplus_deficit: float | None
    fund_balance: float | None
    total_debt: float | None
    status: str
    created_at: str

    class Config:
        from_attributes = True


class LineItemResponse(BaseModel):
    id: str
    section: str | None
    subsection: str | None
    line_name: str
    amount: float | None
    prior_year_amount: float | None
    budget_amount: float | None
    variance: float | None

    class Config:
        from_attributes = True


class ExtractRequest(BaseModel):
    document_id: str
    entity_type: str  # "town" or "school"
    statement_type: str  # "budget", "audit", "cafr"


class AnalysisRequest(BaseModel):
    name: str
    entity_type: str
    analysis_type: str  # "trend", "comparison", "ratio", "variance"
    statement_ids: List[str]


class AnalysisResponse(BaseModel):
    id: str
    name: str
    entity_type: str
    analysis_type: str
    fiscal_years: list
    results: dict
    summary: str | None
    created_at: str

    class Config:
        from_attributes = True


@router.get("/statements", response_model=List[StatementResponse])
def list_statements(
    entity_type: Optional[str] = Query(None),
    fiscal_year: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(FinancialStatement)
    if entity_type:
        query = query.filter(FinancialStatement.entity_type == entity_type)
    if fiscal_year:
        query = query.filter(FinancialStatement.fiscal_year == fiscal_year)
    stmts = query.order_by(FinancialStatement.fiscal_year.desc()).all()
    return [
        StatementResponse(
            id=str(s.id),
            document_id=str(s.document_id),
            entity_name=s.entity_name,
            entity_type=s.entity_type,
            statement_type=s.statement_type,
            fiscal_year=s.fiscal_year,
            total_revenue=s.total_revenue,
            total_expenditures=s.total_expenditures,
            surplus_deficit=s.surplus_deficit,
            fund_balance=s.fund_balance,
            total_debt=s.total_debt,
            status=s.status,
            created_at=s.created_at.isoformat(),
        )
        for s in stmts
    ]


@router.get("/statements/{statement_id}", response_model=StatementResponse)
def get_statement(statement_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
    if not stmt:
        raise HTTPException(status_code=404, detail="Statement not found")
    return StatementResponse(
        id=str(stmt.id),
        document_id=str(stmt.document_id),
        entity_name=stmt.entity_name,
        entity_type=stmt.entity_type,
        statement_type=stmt.statement_type,
        fiscal_year=stmt.fiscal_year,
        total_revenue=stmt.total_revenue,
        total_expenditures=stmt.total_expenditures,
        surplus_deficit=stmt.surplus_deficit,
        fund_balance=stmt.fund_balance,
        total_debt=stmt.total_debt,
        status=stmt.status,
        created_at=stmt.created_at.isoformat(),
    )


@router.get("/statements/{statement_id}/raw")
def get_raw_extraction(statement_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
    if not stmt:
        raise HTTPException(status_code=404, detail="Statement not found")
    return stmt.raw_extraction or {}


@router.get("/statements/{statement_id}/line-items", response_model=List[LineItemResponse])
def get_line_items(statement_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    items = (
        db.query(FinancialLineItem)
        .filter(FinancialLineItem.statement_id == statement_id)
        .order_by(FinancialLineItem.line_order)
        .all()
    )
    return [
        LineItemResponse(
            id=str(item.id),
            section=item.section,
            subsection=item.subsection,
            line_name=item.line_name,
            amount=item.amount,
            prior_year_amount=item.prior_year_amount,
            budget_amount=item.budget_amount,
            variance=item.variance,
        )
        for item in items
    ]


@router.post("/extract")
async def extract_financial_data(
    req: ExtractRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Extract financial data from an uploaded document using AI."""
    doc = db.query(Document).filter(Document.id == req.document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Create a pending statement record
    stmt = FinancialStatement(
        document_id=doc.id,
        entity_name=_entity_name(req.entity_type),
        entity_type=req.entity_type,
        statement_type=req.statement_type,
        fiscal_year=doc.fiscal_year or "Unknown",
        status="processing",
    )
    db.add(stmt)
    db.commit()
    db.refresh(stmt)

    # Run multi-pass extraction in background (v2 = sectioned, parallel, reconciled)
    from services.financial_extractor_v2 import extract_financial_statement_v2
    background_tasks.add_task(extract_financial_statement_v2, str(stmt.id), doc.s3_key)

    return {"statement_id": str(stmt.id), "status": "processing"}


@router.post("/analyze", response_model=AnalysisResponse)
async def create_analysis(
    req: AnalysisRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Run a financial analysis across selected statements."""
    statements = (
        db.query(FinancialStatement)
        .filter(FinancialStatement.id.in_(req.statement_ids))
        .all()
    )
    if not statements:
        raise HTTPException(status_code=404, detail="No statements found")

    fiscal_years = sorted(set(s.fiscal_year for s in statements))

    from services.financial_analyzer import run_analysis
    results, summary = await run_analysis(req.analysis_type, statements)

    analysis = FinancialAnalysis(
        name=req.name,
        entity_type=req.entity_type,
        analysis_type=req.analysis_type,
        fiscal_years=fiscal_years,
        statement_ids=[str(s.id) for s in statements],
        results=results,
        summary=summary,
        created_by=user.id,
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    return AnalysisResponse(
        id=str(analysis.id),
        name=analysis.name,
        entity_type=analysis.entity_type,
        analysis_type=analysis.analysis_type,
        fiscal_years=analysis.fiscal_years,
        results=analysis.results,
        summary=analysis.summary,
        created_at=analysis.created_at.isoformat(),
    )


@router.post("/statements/{statement_id}/drill")
async def run_drill(
    statement_id: UUID,
    background_tasks: BackgroundTasks,
    sync: bool = Query(False, description="Run drill SYNCHRONOUSLY and return results in the response (instead of background). Use for debugging."),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Run all four drill agents + synthesis.

    Default: kicks off in background (returns immediately, results poll via GET).
    sync=true: blocks for the full duration (~30-90s) and returns the drill_results
    so callers can see exactly what succeeded/failed without polling. Required for
    serverless deployments where BackgroundTasks may not complete after response.
    """
    stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
    if not stmt:
        raise HTTPException(status_code=404, detail="Statement not found")
    # Allow drill on extracted/drilled/verified — and ALSO on `error` so a re-drill
    # attempt can recover from a previous transient failure.
    if stmt.status not in ("extracted", "drilled", "verified", "error"):
        raise HTTPException(status_code=409,
                            detail=f"Statement not ready for drill (status={stmt.status}). "
                                   f"Run extract first.")

    from services.financial_agent import run_full_drill

    if sync:
        results = await run_full_drill(str(stmt.id))
        meta = results.get("_meta", {})
        return {
            "statement_id": str(stmt.id),
            "mode": "sync",
            "synthesis_ok": meta.get("synthesis_ok", False),
            "success_count": meta.get("success_count", 0),
            "error_count": meta.get("error_count", 0),
            "duration_s": meta.get("duration_s"),
            "drill_results": results,
        }

    background_tasks.add_task(run_full_drill, str(stmt.id))
    return {"statement_id": str(stmt.id), "mode": "background", "status": "drill_running"}


@router.post("/extract-all")
async def extract_all(
    background_tasks: BackgroundTasks,
    entity_type: Optional[str] = Query(None, description="town | school"),
    doc_type: Optional[str] = Query(None, description="budget | audit | financial_statement (default: all three)"),
    fiscal_year: Optional[str] = Query(None),
    re_extract: bool = Query(False, description="If true, also re-extract documents that already have an extracted statement"),
    concurrency: int = Query(2, ge=1, le=4),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Bulk extract: scan documents matching the filter, create a FinancialStatement
    for any that don't have one (or recreate if re_extract=true), and run v2 extraction
    in background.

    Pairs with /drill-all so the full pipeline is two API calls:
        POST /api/financial/extract-all?entity_type=school
        POST /api/financial/drill-all?entity_type=school
    """
    types_to_extract = [doc_type] if doc_type else ["budget", "audit", "financial_statement"]

    q = db.query(Document).filter(Document.doc_type.in_(types_to_extract))
    if entity_type:
        q = q.filter(Document.category == entity_type)
    if fiscal_year:
        q = q.filter(Document.fiscal_year.like(f"%{fiscal_year}%"))
    # Skip RFP / synopsis / presentation noise — same filter the historical scripts used
    q = q.filter(
        ~Document.filename.ilike("%RFP%"),
        ~Document.filename.ilike("%Synopsis%"),
        ~Document.filename.ilike("%Presentation%"),
        ~Document.filename.ilike("%INTRODUCED%"),
    )
    documents = q.all()

    queued: list[dict] = []
    skipped: list[dict] = []

    for doc in documents:
        existing = (
            db.query(FinancialStatement)
            .filter(FinancialStatement.document_id == doc.id)
            .first()
        )
        if existing and not re_extract:
            skipped.append({"document_id": str(doc.id), "filename": doc.filename,
                            "reason": f"already has statement (status={existing.status})"})
            continue

        # If re_extract: clear line items + reset the existing statement; else create new
        if existing and re_extract:
            db.query(FinancialLineItem).filter(FinancialLineItem.statement_id == existing.id).delete()
            existing.status = "processing"
            existing.notes = None
            existing.raw_extraction = {}
            existing.drill_results = {}
            existing.anomaly_flags = []
            existing.reconcile_status = "not_attempted"
            existing.reconcile_details = {}
            existing.extraction_pass = 0
            stmt = existing
        else:
            entity = doc.category or "town"
            stmt = FinancialStatement(
                document_id=doc.id,
                entity_name=_entity_name(entity),
                entity_type=entity,
                statement_type=doc.doc_type or "audit",
                fiscal_year=(doc.fiscal_year or "")[:4] or "Unknown",
                status="processing",
            )
            db.add(stmt)
            db.flush()

        queued.append({"document_id": str(doc.id), "statement_id": str(stmt.id),
                       "filename": doc.filename, "fiscal_year": stmt.fiscal_year,
                       "entity_type": stmt.entity_type, "doc_type": stmt.statement_type})

    db.commit()

    if not queued:
        return {"queued": 0, "skipped": len(skipped), "skipped_sample": skipped[:10],
                "message": "No documents to extract. Use re_extract=true to re-run on already-extracted docs."}

    # Concurrency-limited background extraction
    target_specs = [(q_["statement_id"],
                     db.query(Document).filter(Document.id == q_["document_id"]).first().s3_key)
                    for q_ in queued]

    async def _extract_batch(specs: list[tuple[str, str]], concurrency: int):
        from services.financial_extractor_v2 import extract_financial_statement_v2
        sem = asyncio.Semaphore(concurrency)

        async def _one(stmt_id: str, s3_key: str):
            async with sem:
                try:
                    await extract_financial_statement_v2(stmt_id, s3_key)
                except Exception:
                    logger.exception("extract_all: extraction failed for %s", stmt_id)

        await asyncio.gather(*[_one(sid, key) for sid, key in specs])

    background_tasks.add_task(_extract_batch, target_specs, concurrency)
    return {
        "queued": len(queued), "skipped": len(skipped), "concurrency": concurrency,
        "filters": {"entity_type": entity_type, "doc_type": doc_type,
                    "fiscal_year": fiscal_year, "re_extract": re_extract},
        "queued_sample": queued[:20],
        "skipped_sample": skipped[:10],
    }


@router.post("/drill-all")
async def drill_all(
    background_tasks: BackgroundTasks,
    entity_type: Optional[str] = Query(None, description="town | school"),
    fiscal_year: Optional[str] = Query(None),
    statement_type: Optional[str] = Query(None),
    redrill: bool = Query(False, description="If true, re-run drill even on already-drilled statements"),
    concurrency: int = Query(2, ge=1, le=4, description="Max statements drilled in parallel (LLM rate limit)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Run drills on every extracted statement matching the filters. Background only —
    drilling many statements is too long for a single HTTP response."""
    q = db.query(FinancialStatement)
    statuses = ["extracted", "error"] if not redrill else ["extracted", "drilled", "verified", "error"]
    q = q.filter(FinancialStatement.status.in_(statuses))
    if entity_type:
        q = q.filter(FinancialStatement.entity_type == entity_type)
    if fiscal_year:
        q = q.filter(FinancialStatement.fiscal_year == fiscal_year)
    if statement_type:
        q = q.filter(FinancialStatement.statement_type == statement_type)

    targets = q.order_by(FinancialStatement.fiscal_year.desc()).all()
    if not targets:
        return {"queued": 0, "message": "No statements match the filter."}

    target_ids = [str(s.id) for s in targets]

    async def _drill_batch(ids: list[str], concurrency: int):
        from services.financial_agent import run_full_drill
        sem = asyncio.Semaphore(concurrency)

        async def _one(sid: str):
            async with sem:
                try:
                    await run_full_drill(sid)
                except Exception:
                    logger.exception("drill_all: drill failed for %s", sid)

        await asyncio.gather(*[_one(sid) for sid in ids])

    background_tasks.add_task(_drill_batch, target_ids, concurrency)
    return {
        "queued": len(target_ids),
        "concurrency": concurrency,
        "filters": {"entity_type": entity_type, "fiscal_year": fiscal_year,
                    "statement_type": statement_type, "redrill": redrill},
        "statement_ids": target_ids[:50],  # truncate response
    }


@router.get("/statements/{statement_id}/drill")
def get_drill_results(
    statement_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
    if not stmt:
        raise HTTPException(status_code=404, detail="Statement not found")
    return {
        "statement_id": str(stmt.id),
        "status": stmt.status,
        "accounting_basis": stmt.accounting_basis,
        "fiscal_calendar": stmt.fiscal_calendar,
        "reconcile_status": stmt.reconcile_status,
        "reconcile_details": stmt.reconcile_details or {},
        "anomaly_flags": stmt.anomaly_flags or [],
        "drill_results": stmt.drill_results or {},
    }


@router.get("/statements/{statement_id}/anomalies")
def get_anomalies(
    statement_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
    if not stmt:
        raise HTTPException(status_code=404, detail="Statement not found")
    return {"statement_id": str(stmt.id), "anomaly_flags": stmt.anomaly_flags or []}


@router.get("/fy-view")
def fy_view(
    entity_type: str = Query(..., description="town | school"),
    fiscal_year: str = Query(..., description="exact match, e.g. '2026-2027' or '2024'"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Merged financial view for one entity + fiscal year, combining data from
    multiple source statements (advertised budget, adopted budget, presentation,
    audit, ACFR). Picks best-of-source for each metric and surfaces what's
    missing.

    Use this when several documents cover the same FY and the dashboard shows
    apparent duplicates — this returns the canonical merged view.
    """
    matches = (
        db.query(FinancialStatement)
        .filter(FinancialStatement.entity_type == entity_type,
                FinancialStatement.fiscal_year == fiscal_year)
        .all()
    )
    if not matches:
        raise HTTPException(status_code=404, detail=f"No statements for {entity_type} FY {fiscal_year}")

    # Source priority — most authoritative first
    PRIORITY = {
        "audit": 0, "acfr": 0, "annual_report": 1, "financial_statement": 1,
        "ufb": 2, "budget": 3,
    }

    def _variant(name: str | None) -> str:
        n = (name or "").lower()
        if "advertised" in n or "tentative" in n:
            return "advertised"
        if "adopted" in n or "final" in n:
            return "adopted"
        if "dlgs" in n or "monmouth" in n:  # NJ county-filed AFS copies  noqa: locality
            return "dlgs_filing"
        if "presentation" in n:
            return "presentation"
        return "primary"

    def _stmt_priority(s):
        type_pri = PRIORITY.get((s.statement_type or "").lower(), 9)
        # Among same type: adopted > dlgs > primary > advertised > presentation
        variant_pri = {"adopted": 0, "dlgs_filing": 1, "primary": 2, "advertised": 3, "presentation": 4}.get(_variant(s.entity_name), 5)
        return (type_pri, variant_pri)

    sorted_stmts = sorted(matches, key=_stmt_priority)
    primary = sorted_stmts[0]

    def best(field: str):
        for s in sorted_stmts:
            v = getattr(s, field, None)
            if v is not None:
                return v, str(s.id)
        return None, None

    rev, rev_src = best("total_revenue")
    exp, exp_src = best("total_expenditures")
    surplus, surp_src = best("surplus_deficit")
    fb, fb_src = best("fund_balance")
    debt, debt_src = best("total_debt")

    # Aggregate line items, deduped by (line_name, amount)
    from models.financial import FinancialLineItem
    seen: set[tuple] = set()
    merged_lines: list[dict] = []
    for s in sorted_stmts:
        items = db.query(FinancialLineItem).filter(FinancialLineItem.statement_id == s.id).all()
        for it in items:
            key = ((it.line_name or "").strip().lower(), float(it.amount) if it.amount else None, it.section)
            if key in seen:
                continue
            seen.add(key)
            merged_lines.append({
                "line_name": it.line_name, "section": it.section, "subsection": it.subsection,
                "amount": it.amount, "prior_year_amount": it.prior_year_amount,
                "budget_amount": it.budget_amount, "yoy_change_pct": it.yoy_change_pct,
                "fund": it.fund, "account_code": it.account_code, "is_total_row": it.is_total_row,
                "from_statement_id": str(s.id),
                "from_doc_variant": _variant(s.entity_name),
            })

    # What's missing? List authoritative document types we don't have
    expected_types = ["audit", "annual_report", "financial_statement", "budget"]
    have_types = {s.statement_type for s in matches}
    missing_types = [t for t in expected_types if t not in have_types]

    # Field-level missing
    missing_fields = []
    if rev is None: missing_fields.append("total_revenue")
    if exp is None: missing_fields.append("total_expenditures")
    if fb is None: missing_fields.append("fund_balance")
    if debt is None: missing_fields.append("total_debt")

    return {
        "entity_type": entity_type,
        "fiscal_year": fiscal_year,
        "primary_statement_id": str(primary.id),
        "primary_statement_type": primary.statement_type,
        "primary_entity_name": primary.entity_name,
        "accounting_basis": primary.accounting_basis,
        "fiscal_calendar": primary.fiscal_calendar,
        "merged": {
            "total_revenue": rev,
            "total_revenue_source": rev_src,
            "total_expenditures": exp,
            "total_expenditures_source": exp_src,
            "surplus_deficit": surplus,
            "surplus_deficit_source": surp_src,
            "fund_balance": fb,
            "fund_balance_source": fb_src,
            "total_debt": debt,
            "total_debt_source": debt_src,
        },
        "sources": [
            {
                "statement_id": str(s.id),
                "statement_type": s.statement_type,
                "entity_name": s.entity_name,
                "variant": _variant(s.entity_name),
                "status": s.status,
                "reconcile_status": s.reconcile_status,
                "has_revenue": s.total_revenue is not None,
                "has_expenditures": s.total_expenditures is not None,
                "has_fund_balance": s.fund_balance is not None,
                "has_debt": s.total_debt is not None,
                "line_item_count": db.query(FinancialLineItem).filter(FinancialLineItem.statement_id == s.id).count(),
            }
            for s in sorted_stmts
        ],
        "merged_line_item_count": len(merged_lines),
        "merged_line_items": merged_lines[:100],  # cap to keep response tractable
        "missing": {
            "doc_types": missing_types,
            "fields": missing_fields,
        },
    }


@router.get("/diagnostics")
def diagnostics(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """End-to-end pipeline health: counts by status, LLM keys configured,
    last drill error, statements without line items. Use to debug 'drills aren't running'."""
    from services.ai import deployment_key
    from collections import Counter

    counts_by_status = dict(
        db.query(FinancialStatement.status, func.count(FinancialStatement.id))
        .group_by(FinancialStatement.status).all()
    )
    counts_by_basis = dict(
        db.query(FinancialStatement.accounting_basis, func.count(FinancialStatement.id))
        .group_by(FinancialStatement.accounting_basis).all()
    )
    counts_by_entity = dict(
        db.query(FinancialStatement.entity_type, func.count(FinancialStatement.id))
        .group_by(FinancialStatement.entity_type).all()
    )

    # Statements with status=extracted but zero line items — extraction silently failed
    bad_extracts = (
        db.query(FinancialStatement)
        .outerjoin(FinancialLineItem, FinancialLineItem.statement_id == FinancialStatement.id)
        .filter(FinancialStatement.status.in_(["extracted", "drilled"]))
        .group_by(FinancialStatement.id)
        .having(func.count(FinancialLineItem.id) == 0)
        .all()
    )

    # Drills that completed with errors
    drill_errors = []
    for s in db.query(FinancialStatement).filter(FinancialStatement.drill_results != {}).all():
        dr = s.drill_results or {}
        per_drill = []
        for k in ("revenue", "expenditure", "debt", "fund_balance", "synthesis"):
            if isinstance(dr.get(k), dict) and "error" in dr[k]:
                per_drill.append({"drill": k, "error": dr[k].get("error"),
                                  "msg": (dr[k].get("error_message") or "")[:200]})
        if per_drill:
            drill_errors.append({"statement_id": str(s.id), "fiscal_year": s.fiscal_year,
                                 "entity_type": s.entity_type, "errors": per_drill})

    return {
        "llm_keys": {
            # Deployment keys (Admin → AI, else env) — what background extraction uses.
            "anthropic_api_key_set": deployment_key("anthropic", db) is not None,
            "gemini_api_key_set": deployment_key("gemini", db) is not None,
        },
        "statements": {
            "by_status": counts_by_status,
            "by_accounting_basis": counts_by_basis,
            "by_entity_type": counts_by_entity,
            "total": db.query(func.count(FinancialStatement.id)).scalar() or 0,
        },
        "extraction_issues": {
            "extracted_with_no_line_items": [
                {"id": str(s.id), "fiscal_year": s.fiscal_year, "entity_type": s.entity_type,
                 "filename": "see document"} for s in bad_extracts[:20]
            ],
            "extracted_with_no_line_items_count": len(bad_extracts),
        },
        "drill_issues": {
            "drills_with_errors_count": len(drill_errors),
            "drills_with_errors_sample": drill_errors[:20],
        },
        "next_steps_hint": _next_steps_hint(counts_by_status, drill_errors, bad_extracts),
    }


def _next_steps_hint(by_status: dict, drill_errors: list, bad_extracts: list) -> str:
    if not by_status:
        return "No statements yet. Upload a budget/audit + click Extract."
    if by_status.get("processing", 0) > 0:
        return f"{by_status['processing']} statements still processing — wait or check logs for stuck jobs."
    if by_status.get("error", 0) > 0:
        return f"{by_status['error']} statements in error state. Check `notes` field on each — likely PDF→markdown or LLM extraction failure."
    if bad_extracts:
        return f"{len(bad_extracts)} statements show 'extracted' but have ZERO line items — section detection failed for the document. Re-extract; document may need TOC manual hint."
    if drill_errors:
        return f"{len(drill_errors)} statements have drill errors. Use POST /api/financial/statements/{{id}}/drill?sync=true to see the exact error inline."
    extracted = by_status.get("extracted", 0)
    if extracted > 0:
        return f"{extracted} extracted statements are ready to drill. Use POST /api/financial/drill-all to drill them all."
    return "Pipeline healthy."


@router.get("/analyses", response_model=List[AnalysisResponse])
def list_analyses(
    entity_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(FinancialAnalysis)
    if entity_type:
        query = query.filter(FinancialAnalysis.entity_type == entity_type)
    analyses = query.order_by(FinancialAnalysis.created_at.desc()).all()
    return [
        AnalysisResponse(
            id=str(a.id),
            name=a.name,
            entity_type=a.entity_type,
            analysis_type=a.analysis_type,
            fiscal_years=a.fiscal_years,
            results=a.results,
            summary=a.summary,
            created_at=a.created_at.isoformat(),
        )
        for a in analyses
    ]
