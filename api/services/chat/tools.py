"""
Chat tools: the JSON schemas the model sees, and their executors.

One registry for every provider (Anthropic, Gemini, OpenAI). Executors take a
short-lived Session and plain-dict args and return JSON-able dicts.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from models.financial import FinancialStatement
from municipality import get_municipality
from services.rag_search import get_document_excerpt, list_recent_documents, search_documents

logger = logging.getLogger(__name__)


# ─── Tool definitions ────────────────────────────────────────────────────────

def _tool_defs() -> list[dict]:
    return [
        {
            "name": "share_plan",
            "description": (
                "Announce your plan BEFORE any other tool. Call this EXACTLY ONCE at the start "
                "of each user turn. The user sees the plan as a checklist that ticks off as you "
                "work. Required — do not skip even for simple questions."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 8,
                        "description": (
                            "Ordered list of short imperative phrases describing what you'll do. "
                            "Examples: 'Search for FY24 budget passages', 'Read the AHES audit', "
                            "'Compare YoY enrollment'."
                        ),
                    },
                    "rationale": {
                        "type": "string",
                        "description": "1-2 sentences on why this plan answers the question.",
                    },
                },
                "required": ["steps"],
            },
        },
        {
            "name": "search_documents",
            "description": "Search the document library by topic. Returns ranked documents with metadata and AI summaries. Use this when you need to know which documents exist on a topic.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Topic, question, or keywords."},
                    "fiscal_year": {"type": "string", "description": "Optional FY filter (e.g., '2024')."},
                    "category": {"type": "string", "enum": ["town", "school"], "description": "Optional category filter."},
                    "top_k": {"type": "integer", "default": 8, "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
        },
        {
            "name": "search_chunks",
            "description": "Search PASSAGES across all documents (keyword + semantic, reranked). Use this for fact-finding ('what did the 2023 budget allocate to roads?'). Returns the most relevant passages with their source document, title, date and page. Almost always the right first call. Narrow with filters instead of re-running broad queries.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "municipality": {"type": "string", "enum": get_municipality().entity_keys,
                                     "description": "Government that published the record:\n" + get_municipality().entities_prompt()},
                    "doc_type": {"type": "string", "description": "e.g. minutes, agenda, budget, audit, ordinance, resolution, bill_list, planning"},
                    "department": {"type": "string"},
                    "fiscal_year": {"type": "string"},
                    "date_from": {"type": "string", "description": "YYYY-MM-DD; only documents dated on/after"},
                    "date_to": {"type": "string", "description": "YYYY-MM-DD; only documents dated on/before"},
                    "top_k": {"type": "integer", "default": 8, "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
        },
        {
            "name": "read_document",
            "description": "Read a specific document by ID. Use this AFTER search_documents/search_chunks identifies a document you need to inspect more carefully.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 12000, "maximum": 30000},
                },
                "required": ["document_id"],
            },
        },
        {
            "name": "get_financial_summary",
            "description": "Get extracted financial figures (revenue, expenditures, fund balance, debt, key line items) for the borough or school district across fiscal years. Use this for any tax/budget/debt question.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "enum": ["town", "school", "all"], "default": "all"},
                    "fiscal_year": {"type": "string", "description": "Optional FY filter."},
                },
            },
        },
        {
            "name": "list_recent_documents",
            "description": "List recent documents, optionally filtered by doc_type (budget, audit, minutes, resolution, ordinance). Use for browsing-style questions.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "doc_type": {"type": "string"},
                    "limit": {"type": "integer", "default": 20, "maximum": 50},
                },
            },
        },
        {
            "name": "get_drill_results",
            "description": "Get the deep-drill analysis results for a specific financial statement: revenue composition, expenditure breakdown by function/object, debt analysis, fund-balance analysis, and an executive synthesis with red flags. Use this when the user asks for ANALYSIS rather than raw figures.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "statement_id": {"type": "string"},
                    "drill_type": {"type": "string", "enum": ["revenue", "expenditure", "debt", "fund_balance", "synthesis", "all"], "default": "all"},
                },
                "required": ["statement_id"],
            },
        },
        {
            "name": "get_anomalies",
            "description": "Get rule-based anomaly flags (operating deficit, fund balance over/under benchmark, large YoY swings, budget variances, debt/revenue ratio, salary concentration) for a statement, or across all statements if no ID given. Each flag has a severity (info|warn|high).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "statement_id": {"type": "string"},
                    "entity_type": {"type": "string", "enum": ["town", "school"]},
                    "min_severity": {"type": "string", "enum": ["info", "warn", "high"], "default": "info"},
                },
            },
        },
        {
            "name": "get_line_items",
            "description": "Fetch detailed extracted line items for a statement, optionally filtered by section (Revenue/Expenditures/Debt Service/...) or by NJ school account-code segments (program/function/object). Use this when the user wants to drill into a specific spending category like 'show me transportation costs' or 'how much was spent on salaries vs benefits'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "statement_id": {"type": "string"},
                    "section": {"type": "string"},
                    "fund": {"type": "string", "enum": ["general", "capital", "capital_projects", "debt_service", "special_revenue", "enterprise", "trust"]},
                    "function_code": {"type": "string", "description": "NJ school function code, e.g. '230' for Basic Skills"},
                    "object_code": {"type": "string", "description": "NJ object code, e.g. '100' for Salaries"},
                    "min_amount": {"type": "number"},
                    "limit": {"type": "integer", "default": 50, "maximum": 200},
                },
                "required": ["statement_id"],
            },
        },
        {
            "name": "search_contracts",
            "description": "Search municipal/school contracts by vendor, fiscal year, minimum amount, or entity. Returns contract title, amount, vendor, awarded date, authorizing resolution.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "vendor": {"type": "string"},
                    "entity_type": {"type": "string", "enum": ["town", "school"]},
                    "fiscal_year": {"type": "string"},
                    "min_amount": {"type": "number"},
                    "limit": {"type": "integer", "default": 30, "maximum": 100},
                },
            },
        },
        {
            "name": "get_vendor_summary",
            "description": "Summarize all activity for a single vendor — total contracts, total payments, fiscal years active, contract types. Use for 'How much has the borough paid X over the last 5 years?' style questions.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "vendor_name": {"type": "string"},
                    "entity_type": {"type": "string", "enum": ["town", "school"]},
                },
                "required": ["vendor_name"],
            },
        },
        {
            "name": "get_integrity_flags",
            "description": "Red flags the integrity engine raised from the records — near-threshold and split purchases, emergency and no-bid concentration, single bids, duplicate awards, payouts, change-order growth, official-surname hires, pay-to-play matches, and adverse oversight/audit findings. Each flag has evidence (document, page, quote). Use for 'anything suspicious?', 'who gets no-bid work?', 'red flags for vendor X'. Flags are leads for review, not findings — say so, and cite the evidence documents.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "rule": {"type": "string", "description": "e.g. split_purchases, emergency_procurement, noncompetitive_concentration, pay_to_play, oversight_findings"},
                    "subject": {"type": "string", "description": "vendor or person name (substring)"},
                    "min_severity": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
                    "limit": {"type": "integer", "default": 15, "maximum": 50},
                },
            },
        },
        {
            "name": "search_awards",
            "description": "Spending actions extracted from council/board resolutions: vendor, amount, date, procurement method (competitive_bid, rfp, state_contract, emergency, professional_services, no_bid, sole_source, unknown), bids received, account code, with the source document. Use for vendor histories and 'how was X procured?'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "vendor": {"type": "string"},
                    "procurement": {"type": "string"},
                    "action_type": {"type": "string", "description": "contract, purchase, payout, transfer, settlement, change_order, grant, lease"},
                    "min_amount": {"type": "number"},
                    "limit": {"type": "integer", "default": 25, "maximum": 100},
                },
            },
        },
    ]


def _propose_section_tool() -> dict:
    """Deck-mode only: lets Claude propose a new or rewritten section.
    The chat panel renders the proposal as an accept/reject card; the
    user applies it to the active presentation in one click."""
    return {
        "name": "propose_section",
        "description": (
            "DECK MODE ONLY. Propose a new section for the active presentation, or a rewrite "
            "of an existing one. The user accepts or rejects from the chat panel. Use this when "
            "the user asks you to draft, expand, or rewrite content for the deck they're editing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {
                    "type": "string",
                    "description": "If rewriting an existing section, its id (e.g. 'sec_abc123'). Omit to propose a new section.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["narrative", "table", "react_component"],
                    "description": "Section kind. Narrative = markdown prose with citations. Table = structured rows. react_component = sandboxed TSX (Recharts/Lucide).",
                },
                "title": {"type": "string"},
                "body": {"type": "string", "description": "Markdown body for narrative sections. Use [source: filename.pdf] citations."},
                "headers": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                "caption": {"type": "string"},
                "tsx": {"type": "string", "description": "TSX source for react_component sections — see deck_chat_service for the curated scope."},
                "rationale": {"type": "string", "description": "1-2 sentences explaining the proposal — shown to the user on the accept card."},
            },
            "required": ["kind", "title"],
        },
    }


def _web_search_tool() -> dict:
    """Anthropic's server-side web search tool. Claude executes the search on
    Anthropic's infrastructure (Brave-backed), so we don't need to run a local
    DDG/Tavily/etc. — and EC2 IPs aren't blocked the way they are by DDG.

    Pricing: $10 / 1000 searches (billed by Anthropic).
    Result blocks come back inline as `web_search_tool_result` — no client
    round-trip required. The model also emits citations as `citations` arrays
    on text blocks; our existing markdown stream still works.
    """
    return {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 5,
    }


# ─── Tool execution ──────────────────────────────────────────────────────────

def _exec_search_documents(db: Session, args: dict) -> dict:
    rows = search_documents(
        db,
        query=args["query"],
        top_k=int(args.get("top_k", 8)),
        fiscal_year=args.get("fiscal_year"),
        category=args.get("category"),
    )
    return {
        "count": len(rows),
        "results": [
            {
                "document_id": str(r["id"]),
                "filename": r["filename"],
                "title": r.get("title"),
                "doc_date": r.get("doc_date"),
                "municipality": r.get("municipality"),
                "doc_type": r.get("doc_type"),
                "fiscal_year": r.get("fiscal_year"),
                "summary": (r.get("notes") or "")[:600],
                "page_count": r.get("page_count"),
            }
            for r in rows
        ],
    }


CHUNK_CHARS = 1500


def _exec_search_chunks(db: Session, args: dict) -> dict:
    """Same ranking pipeline as /api/search (parse, expand, hybrid RRF, rerank)."""
    from routes.search import rank_chunks
    top_k = min(int(args.get("top_k", 8)), 20)
    rows, _, _, _ = rank_chunks(
        db, args["query"],
        fiscal_year=args.get("fiscal_year"),
        category=args.get("category"),
        doc_type=args.get("doc_type"),
        department=args.get("department"),
        municipality=args.get("municipality"),
        pool_size=max(top_k * 3, 30),
    )
    date_from, date_to = args.get("date_from"), args.get("date_to")
    if date_from or date_to:
        rows = [r for r in rows if r.get("doc_date")
                and (not date_from or r["doc_date"] >= date_from)
                and (not date_to or r["doc_date"] <= date_to)]
    rows = rows[:top_k]
    return {
        "count": len(rows),
        "chunks": [
            {
                "document_id": str(r["document_id"]),
                "filename": r["filename"],
                "title": r.get("title"),
                "doc_date": r.get("doc_date"),
                "municipality": r.get("municipality"),
                "doc_type": r.get("doc_type"),
                "page_start": r.get("page_start"),
                "page_end": r.get("page_end"),
                "content": (r.get("content") or "")[:CHUNK_CHARS],
            }
            for r in rows
        ],
    }


def _exec_read_document(db: Session, args: dict) -> dict:
    doc = get_document_excerpt(db, args["document_id"], max_chars=int(args.get("max_chars", 12000)))
    if not doc:
        return {"error": "not_found"}
    return {
        "document_id": str(doc["id"]),
        "filename": doc["filename"],
        "doc_type": doc.get("doc_type"),
        "fiscal_year": doc.get("fiscal_year"),
        "category": doc.get("category"),
        "page_count": doc.get("page_count"),
        "summary": doc.get("notes"),
        "excerpt": doc.get("excerpt") or "",
    }


def _exec_get_financial_summary(db: Session, args: dict) -> dict:
    entity = args.get("entity", "all")
    fy = args.get("fiscal_year")
    q = db.query(FinancialStatement)
    if entity != "all":
        q = q.filter(FinancialStatement.entity_type == entity)
    if fy:
        q = q.filter(FinancialStatement.fiscal_year == fy)
    q = q.order_by(FinancialStatement.entity_type, FinancialStatement.fiscal_year)

    out: list[dict] = []
    for s in q.all():
        d: dict = {
            "entity": s.entity_type,
            "fiscal_year": s.fiscal_year,
            "total_revenue": float(s.total_revenue) if s.total_revenue else None,
            "total_expenditures": float(s.total_expenditures) if s.total_expenditures else None,
            "fund_balance": float(s.fund_balance) if s.fund_balance else None,
            "total_debt": float(s.total_debt) if s.total_debt else None,
        }
        if s.total_revenue and s.total_expenditures:
            d["surplus_deficit"] = float(s.total_revenue) - float(s.total_expenditures)
        # Include selected raw-extraction line items for granularity.
        if isinstance(s.raw_extraction, dict):
            details: dict = {}
            for section in ("current_fund", "general_fund", "income_statement"):
                section_data = s.raw_extraction.get(section)
                if not isinstance(section_data, dict):
                    continue
                for k in ("property_tax_revenue", "property_tax_levy", "state_aid",
                         "salaries_wages", "debt_service", "county_taxes",
                         "school_taxes", "instruction_expenditures", "transportation"):
                    v = section_data.get(k)
                    if isinstance(v, (int, float)) and v > 100:
                        details[k] = float(v)
            if details:
                d["line_items"] = details
        out.append(d)

    return {"count": len(out), "statements": out}


def _exec_list_recent_documents(db: Session, args: dict) -> dict:
    rows = list_recent_documents(db, doc_type=args.get("doc_type"), limit=int(args.get("limit", 20)))
    return {
        "count": len(rows),
        "documents": [
            {
                "document_id": str(r["id"]),
                "filename": r["filename"],
                "doc_type": r.get("doc_type"),
                "category": r.get("category"),
                "fiscal_year": r.get("fiscal_year"),
                "summary": (r.get("notes") or "")[:400],
            }
            for r in rows
        ],
    }


def _exec_get_drill_results(db: Session, args: dict) -> dict:
    from models.financial import FinancialStatement
    sid = args.get("statement_id")
    if not sid:
        return {"error": "statement_id required"}
    stmt = db.query(FinancialStatement).filter(FinancialStatement.id == sid).first()
    if not stmt:
        return {"error": "statement_not_found"}
    drills = stmt.drill_results or {}
    drill_type = args.get("drill_type", "all")
    if drill_type != "all" and drill_type in drills:
        return {
            "statement_id": str(stmt.id), "fiscal_year": stmt.fiscal_year,
            "entity_type": stmt.entity_type, "drill_type": drill_type,
            "result": drills.get(drill_type),
        }
    return {
        "statement_id": str(stmt.id), "fiscal_year": stmt.fiscal_year,
        "entity_type": stmt.entity_type,
        "accounting_basis": stmt.accounting_basis,
        "reconcile_status": stmt.reconcile_status,
        "drills": drills,
    }


def _exec_get_anomalies(db: Session, args: dict) -> dict:
    from models.financial import FinancialStatement
    sid = args.get("statement_id")
    entity_type = args.get("entity_type")
    severity_rank = {"info": 0, "warn": 1, "high": 2}
    min_sev = severity_rank.get(args.get("min_severity", "info"), 0)

    q = db.query(FinancialStatement)
    if sid:
        q = q.filter(FinancialStatement.id == sid)
    if entity_type:
        q = q.filter(FinancialStatement.entity_type == entity_type)

    rows = []
    for s in q.all():
        flags = [f for f in (s.anomaly_flags or [])
                 if severity_rank.get(f.get("severity", "info"), 0) >= min_sev]
        if not flags:
            continue
        rows.append({
            "statement_id": str(s.id), "entity_type": s.entity_type,
            "fiscal_year": s.fiscal_year, "statement_type": s.statement_type,
            "flags": flags,
        })
    return {"count": len(rows), "statements": rows}


def _exec_get_line_items(db: Session, args: dict) -> dict:
    from models.financial import FinancialLineItem
    sid = args.get("statement_id")
    if not sid:
        return {"error": "statement_id required"}
    q = db.query(FinancialLineItem).filter(FinancialLineItem.statement_id == sid)
    if args.get("section"):
        q = q.filter(FinancialLineItem.section == args["section"])
    if args.get("fund"):
        q = q.filter(FinancialLineItem.fund == args["fund"])
    if args.get("function_code"):
        q = q.filter(FinancialLineItem.function_code == args["function_code"])
    if args.get("object_code"):
        q = q.filter(FinancialLineItem.object_code == args["object_code"])
    if args.get("min_amount") is not None:
        q = q.filter(FinancialLineItem.amount >= float(args["min_amount"]))
    items = q.order_by(FinancialLineItem.amount.desc().nullslast()).limit(int(args.get("limit", 50))).all()
    return {
        "count": len(items),
        "items": [
            {
                "line_name": it.line_name, "section": it.section, "subsection": it.subsection,
                "amount": it.amount, "prior_year_amount": it.prior_year_amount,
                "yoy_change_pct": it.yoy_change_pct, "variance_pct": it.variance_pct,
                "fund": it.fund, "account_code": it.account_code,
                "is_total_row": it.is_total_row,
            } for it in items
        ],
    }


def _exec_search_contracts(db: Session, args: dict) -> dict:
    from models.contract import Contract, Vendor
    q = db.query(Contract).join(Vendor)
    if args.get("entity_type"):
        q = q.filter(Contract.entity_type == args["entity_type"])
    if args.get("fiscal_year"):
        q = q.filter(Contract.fiscal_year == args["fiscal_year"])
    if args.get("vendor"):
        norm = " ".join(str(args["vendor"]).lower().split())
        q = q.filter(Vendor.normalized_name.ilike(f"%{norm}%"))
    if args.get("min_amount") is not None:
        q = q.filter(Contract.amount >= float(args["min_amount"]))
    rows = q.order_by(Contract.awarded_date.desc().nullslast()).limit(int(args.get("limit", 30))).all()
    return {
        "count": len(rows),
        "contracts": [
            {
                "id": str(c.id), "vendor_name": c.vendor.name, "title": c.title,
                "amount": c.amount, "fiscal_year": c.fiscal_year,
                "contract_type": c.contract_type, "entity_type": c.entity_type,
                "awarded_date": c.awarded_date.isoformat() if c.awarded_date else None,
                "authorizing_resolution": c.authorizing_resolution,
            } for c in rows
        ],
    }


def _exec_get_vendor_summary(db: Session, args: dict) -> dict:
    from sqlalchemy import func
    from models.contract import Vendor, Contract, Payment
    name = args.get("vendor_name", "")
    norm = " ".join(name.lower().split())
    if not norm:
        return {"error": "vendor_name required"}
    vendor = db.query(Vendor).filter(Vendor.normalized_name.ilike(f"%{norm}%")).first()
    if not vendor:
        return {"error": "vendor_not_found", "searched": name}

    contract_q = db.query(Contract).filter(Contract.vendor_id == vendor.id)
    if args.get("entity_type"):
        contract_q = contract_q.filter(Contract.entity_type == args["entity_type"])
    contracts = contract_q.order_by(Contract.awarded_date.desc().nullslast()).all()

    payment_q = db.query(Payment).filter(Payment.vendor_id == vendor.id)
    if args.get("entity_type"):
        payment_q = payment_q.filter(Payment.entity_type == args["entity_type"])

    total_contracts = sum(float(c.amount or 0) for c in contracts)
    payment_total = float(
        payment_q.with_entities(func.coalesce(func.sum(Payment.amount), 0.0)).scalar() or 0.0
    )

    fy_set = sorted({c.fiscal_year for c in contracts if c.fiscal_year}, reverse=True)
    return {
        "vendor": {"id": str(vendor.id), "name": vendor.name, "category": vendor.category},
        "contract_count": len(contracts),
        "total_contracts_awarded": total_contracts,
        "total_paid": payment_total,
        "fiscal_years_active": fy_set[:10],
        "recent_contracts": [
            {
                "title": c.title, "amount": c.amount, "fiscal_year": c.fiscal_year,
                "awarded_date": c.awarded_date.isoformat() if c.awarded_date else None,
                "contract_type": c.contract_type,
            } for c in contracts[:10]
        ],
    }


def _exec_web_search(args: dict) -> dict:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return {"error": "web_search_not_installed"}
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(args["query"], max_results=int(args.get("max_results", 5))))
    except Exception as exc:
        logger.warning("web_search failed: %s", exc)
        return {"error": str(exc)}
    return {
        "count": len(results),
        "results": [
            {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
            for r in results
        ],
    }


def _exec_get_integrity_flags(db: Session, args: dict) -> dict:
    from models.integrity import IntegrityFlag
    sev = {"low": ["low", "medium", "high"], "medium": ["medium", "high"], "high": ["high"]}[args.get("min_severity") or "medium"]
    q = db.query(IntegrityFlag).filter(IntegrityFlag.active == 1, IntegrityFlag.status != "dismissed",
                                       IntegrityFlag.severity.in_(sev))
    if args.get("rule"):
        q = q.filter(IntegrityFlag.rule == args["rule"])
    if args.get("subject"):
        q = q.filter(IntegrityFlag.subject.ilike(f"%{args['subject']}%"))
    rank = {"high": 0, "medium": 1, "low": 2}
    rows = sorted(q.all(), key=lambda f: (rank.get(f.severity, 3), -(f.score or 0)))[: min(int(args.get("limit") or 15), 50)]
    from models.document import Document
    ids = {e.get("document_id") for f in rows for e in (f.evidence or [])[:4] if e.get("document_id")}
    names = {str(i): fn for i, fn in db.query(Document.id, Document.filename).filter(Document.id.in_(list(ids))).all()} if ids else {}
    return {"count": len(rows), "note": "Red flags are leads for human review, not findings of wrongdoing.",
            "flags": [{"rule": f.rule, "severity": f.severity, "status": f.status, "title": f.title,
                       "explanation": f.explanation, "subject": f.subject, "amount": f.amount,
                       "evidence": [{**{k: e.get(k) for k in ("document_id", "title", "page", "date", "quote", "amount")},
                                     "filename": names.get(str(e.get("document_id")))}
                                    for e in (f.evidence or [])[:4]]} for f in rows]}


def _exec_search_awards(db: Session, args: dict) -> dict:
    from models.document import Document
    from models.integrity import Award
    q = db.query(Award, Document.filename).join(Document, Document.id == Award.document_id)
    if args.get("vendor"):
        q = q.filter(Award.vendor_name.ilike(f"%{args['vendor']}%"))
    if args.get("procurement"):
        q = q.filter(Award.procurement == args["procurement"])
    if args.get("action_type"):
        q = q.filter(Award.action_type == args["action_type"])
    if args.get("min_amount"):
        q = q.filter(Award.amount >= float(args["min_amount"]))
    rows = q.order_by(Award.meeting_date.desc().nullslast()).limit(min(int(args.get("limit") or 25), 100)).all()
    return {"count": len(rows), "awards": [
        {"date": str(a.meeting_date or ""), "vendor": a.vendor_name, "amount": a.amount, "action": a.action_type,
         "procurement": a.procurement, "bids_received": a.bids_received, "title": a.title, "account": a.account_code,
         "filename": fn, "page": a.page, "quote": (a.quote or "")[:300]} for a, fn in rows]}


def _execute_tool(name: str, args: dict, db: Session) -> dict:
    try:
        if name == "share_plan":
            # The plan is forwarded to the user as its own SSE event in the
            # streaming loop (see _stream_claude). The tool result is just an
            # ack so Claude continues with the actual work.
            return {"ok": True, "ack": "Plan recorded. Now executing it."}
        if name == "propose_section":
            # Same pattern as share_plan — the proposal payload is forwarded
            # via a dedicated SSE event; the tool result is a plain ack so
            # the model can narrate around it without echoing the full body.
            return {"ok": True, "ack": "Proposal sent to the deck editor."}
        if name == "search_documents":
            return _exec_search_documents(db, args)
        if name == "search_chunks":
            return _exec_search_chunks(db, args)
        if name == "read_document":
            return _exec_read_document(db, args)
        if name == "get_financial_summary":
            return _exec_get_financial_summary(db, args)
        if name == "list_recent_documents":
            return _exec_list_recent_documents(db, args)
        if name == "get_drill_results":
            return _exec_get_drill_results(db, args)
        if name == "get_anomalies":
            return _exec_get_anomalies(db, args)
        if name == "get_line_items":
            return _exec_get_line_items(db, args)
        if name == "search_contracts":
            return _exec_search_contracts(db, args)
        if name == "get_vendor_summary":
            return _exec_get_vendor_summary(db, args)
        if name == "web_search":
            return _exec_web_search(args)
        if name == "get_integrity_flags":
            return _exec_get_integrity_flags(db, args)
        if name == "search_awards":
            return _exec_search_awards(db, args)
        return {"error": f"unknown_tool:{name}"}
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        return {"error": str(exc)[:300]}


# ─── Chat history ────────────────────────────────────────────────────────────

def _describe_tool_call(name: str, input_args: dict) -> str:
    """Build a short, human-readable line for what a tool is about to do.
    Surfaces the actual query / document name so the user sees substance,
    not just a generic 'Searching…' string."""
    args = input_args or {}
    q = (args.get("query") or "").strip()
    if name in ("search_chunks", "search_documents") and q:
        return f"Searching: “{q[:80]}”"
    if name == "web_search" and q:
        return f"Web search: “{q[:80]}”"
    if name == "read_document":
        return "Reading document"
    if name == "get_financial_summary":
        ent = args.get("entity") or "all"
        fy = args.get("fiscal_year")
        return f"Loading financial summary ({ent}{', FY ' + fy if fy else ''})"
    if name == "list_recent_documents":
        return f"Listing recent {args.get('doc_type') or 'documents'}"
    if name == "get_drill_results":
        return f"Loading drill analysis ({args.get('drill_type', 'all')})"
    if name == "get_anomalies":
        return f"Loading anomalies ({args.get('min_severity', 'info')}+)"
    if name == "get_line_items":
        bits = [args.get("section"), args.get("fund"),
                args.get("function_code"), args.get("object_code")]
        f = ", ".join(b for b in bits if b)
        return f"Loading line items{(' — ' + f) if f else ''}"
    if name == "search_contracts":
        ent = args.get("entity_type") or ""
        v = args.get("vendor") or ""
        return f"Searching contracts{(' / ' + ent) if ent else ''}{(' / ' + v) if v else ''}"
    if name == "get_vendor_summary":
        return f"Loading vendor: {args.get('vendor_name', '?')}"
    if name == "get_integrity_flags":
        return f"Checking red flags{(' — ' + args['subject']) if args.get('subject') else ''}"
    if name == "search_awards":
        return f"Searching awards{(' — ' + args['vendor']) if args.get('vendor') else ''}"
    return name.replace("_", " ").title()


def _fit_json(res, limit: int = 30000) -> str:
    """Serialize a tool result under `limit` chars by trimming its largest
    lists/strings, never by slicing the JSON text (which produced invalid JSON)."""
    payload = json.dumps(res, default=str)
    if len(payload) <= limit:
        return payload
    if not isinstance(res, dict):
        return json.dumps({"text": payload[: limit - 100], "_truncated": True})
    import copy
    res = copy.deepcopy(res)
    for _ in range(60):
        sized = [(len(json.dumps(v, default=str)), k) for k, v in res.items()
                 if isinstance(v, (list, str)) and v]
        if not sized:
            break
        _, key = max(sized)
        v = res[key]
        if isinstance(v, list):
            res[key] = v[: max(1, len(v) * 2 // 3)] if len(v) > 1 else []
        else:
            res[key] = v[: len(v) * 2 // 3] + "…"
        res["_truncated"] = "result trimmed to fit; narrow the query for more"
        payload = json.dumps(res, default=str)
        if len(payload) <= limit:
            return payload
    return json.dumps({"error": "result too large", "hint": "narrow the query or filters"})


def _summarize_tool_result(name: str, res: dict) -> str:
    if "error" in res:
        return f"error: {res['error']}"
    if name in ("search_documents", "list_recent_documents"):
        n = res.get("count", 0)
        return f"Found {n} document{'s' if n != 1 else ''}"
    if name == "search_chunks":
        return f"Found {res.get('count', 0)} matching passages"
    if name == "read_document":
        return f"Read {res.get('filename', 'document')}"
    if name == "get_financial_summary":
        return f"Loaded {res.get('count', 0)} financial statement(s)"
    if name == "get_drill_results":
        if "drill_type" in res:
            return f"Loaded {res['drill_type']} drill"
        return f"Loaded full drill ({len(res.get('drills', {}))} sections)"
    if name == "get_anomalies":
        return f"Found anomalies in {res.get('count', 0)} statement(s)"
    if name == "get_line_items":
        return f"Loaded {res.get('count', 0)} line item(s)"
    if name == "search_contracts":
        return f"Found {res.get('count', 0)} contract(s)"
    if name == "get_vendor_summary":
        v = res.get("vendor") or {}
        return f"Loaded vendor: {v.get('name', '?')}"
    if name == "get_integrity_flags":
        return f"Found {res.get('count', 0)} red flag(s)"
    if name == "search_awards":
        return f"Found {res.get('count', 0)} award(s)"
    if name == "web_search":
        return f"Found {res.get('count', 0)} web results"
    return "ok"


