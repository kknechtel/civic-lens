"""
Municipal AI Chat — Claude Sonnet 4.6 with hybrid pgvector RAG,
extended thinking, agentic tool loop, and SSE streaming.

Replaces the prior single-shot tsvector-only retrieval. Claude can now
iteratively call:

  - search_documents(query, fiscal_year?, category?)
  - search_chunks(query, fiscal_year?, category?)        ← passage-level
  - read_document(document_id, max_chars?)
  - get_financial_summary(entity?, fiscal_year?)
  - list_recent_documents(doc_type?)
  - web_search(query)                                    ← when enabled

Three modes (request body):
  - report_mode:   structured executive-style markdown report
  - deep_thinking: extended thinking + 60K thinking budget + opus-4-7
  - (default)      sonnet-4-6, no thinking, fast streaming

Routes:
  POST /api/chat/stream             — SSE chat (text + thinking + tool_use events)
  GET  /api/chat/history?session_id — full message thread
  GET  /api/chat/sessions           — recent sessions
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from municipality import get_municipality
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc, text as sql_text
from sqlalchemy.orm import Session

from auth import get_current_user
from config import ANTHROPIC_API_KEY, GEMINI_API_KEY
from database import SessionLocal, get_db
from models.financial import FinancialStatement
from models.user import User
from services.rag_search import (
    get_document_excerpt,
    list_recent_documents,
    search_chunks,
    search_documents,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Models ──────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    document_id: Optional[str] = None
    scope_type: str = "all"
    scope_id: Optional[str] = None
    model: str = "claude"
    web_search: bool = False
    deep_thinking: bool = False
    report_mode: bool = False
    # Deck-mode: when set, the chat is bound to a specific presentation. The
    # backend exposes a `propose_section` tool whose input is forwarded to
    # the frontend as a `proposal` SSE event for the user to accept/reject.
    presentation_id: Optional[str] = None
    presentation_summary: Optional[str] = None
    # Slide scope — when the operator picks a specific section in the chat's
    # section dropdown, this lands here and we focus the model on that section
    # (so propose_section defaults to editing it, not appending a new one).
    target_section_id: Optional[str] = None


# ─── System prompts ──────────────────────────────────────────────────────────

def _background() -> str:
    """Who the assistant is and what it knows — from the municipality profile."""
    muni = get_municipality()
    head = (f"You are the {muni.short_name} AI Expert — a deeply knowledgeable municipal analyst "
            f"specializing in the {muni.name}, {muni.state} and the governments that serve it.")
    parts = [head]
    if muni.ai.background.strip():
        parts.append(muni.ai.background.strip())
    return "\n\n".join(parts)


WORKING_INSTRUCTIONS = """## How to answer

You have powerful tools. USE THEM. Do not say "I cannot find" without calling at least two tools first.

## CITATION RULE (MANDATORY)

EVERY factual claim — every dollar amount, date, vote count, name, ordinance number — MUST be followed by `[source: filename.pdf]` using the EXACT `filename` field returned by your search/read tools. The frontend renders these as clickable buttons that open the source document. A response without citations is broken. Examples:

  - "The second most expensive out-of-district placement costs $142,388 [source: 2026-2027 Advertised Budget.pdf]."
  - "The board approved the resolution 5-0 [source: January 24, 2024 Regular BOE Meeting Minutes.pdf]."

If a fact came from multiple sources, cite each one separately: `[source: a.pdf] [source: b.pdf]`.

Search results also carry a human-readable `title`, `doc_date`, and `municipality`. Mention the title/date in prose when helpful, but citations must still use the exact `filename`.

## WHICH GOVERNMENT

{which_town}

## STOP NARRATING — JUST DO THE WORK

Do NOT say "Let me now search…" or "Now let me check…" between tool calls. The user sees a live tool indicator; they don't need a play-by-play. After tool calls, write the FINAL ANSWER. If you've made 4+ tool calls and still don't have what you need, write the answer with what you HAVE plus a one-line "couldn't find X" note — never keep searching past 6 tool calls.

## STEP 0: ANNOUNCE YOUR PLAN FIRST

On EVERY user turn, before any other tool call, you MUST call the `share_plan` tool. Pass:
  - `steps`: an ordered list of short imperative actions you'll take next (1-8 items).
    Examples:
      "Search for FY24 budget passages"
      "Read the school district audit and pull instructional spend"
      "Compare YoY enrollment 2020-2024"
      "Draft executive summary"
  - `rationale`: 1-2 sentences on why this plan answers the question.

This is mandatory even for simple questions (single-step plans are fine). The user sees your plan as a checklist that ticks off as you work — skipping share_plan leaves them in the dark.

After share_plan, follow the plan: call the search/read tools, then write the final response.

## Tool usage

For every non-trivial question:
1. Call `search_chunks` with the user's question (verbatim or rephrased) — this returns the most relevant passages from across the entire document corpus.
2. If the chunks point to a document worth reading in full, call `read_document` with that document's ID.
3. If the question concerns budgets, taxes, debt, or fund balances:
   - For HEADLINE figures, call `get_financial_summary`.
   - For ANALYSIS (revenue composition, expenditure breakdown, fund-balance health, debt position, red flags), call `get_drill_results` for the relevant statement_id. The drill is far richer than the summary — use it.
   - For SPECIFIC line items ("show me transportation costs", "salaries vs benefits"), call `get_line_items` with section/fund/function_code/object_code filters.
   - To surface FINANCIAL CONCERNS proactively, call `get_anomalies` — pre-computed rule-based flags.
4. For VENDOR / CONTRACT questions, call `search_awards` (resolution-level spending with procurement method) and/or `search_contracts` / `get_vendor_summary`. For "anything suspicious / red flags / who gets no-bid work", call `get_integrity_flags`, then read the evidence documents. Present flags as leads with innocent explanations — never as proof of corruption — and cite the evidence.
5. MEETING RECORDINGS — `search_chunks` already covers them. They show up with a `doc_type` starting with `recording_` (e.g. `recording_council`, `recording_school_board`). Each chunk's text is prefixed with `[HH:MM:SS]` markers from the transcript. When citing a moment from a meeting, INCLUDE the timestamp in your answer (e.g. "At [01:23:45] the council voted 5-0…"). The frontend turns these into seek-to links on the meeting page. Use `read_document` to scroll the full transcript when you need broader context than the chunks give.
6. JURISDICTION-SPECIFIC AWARENESS:
{jurisdiction_notes}
7. Cite every fact with `[source: filename.pdf]` — these become clickable links.

## Output style

- ALWAYS extract concrete numbers, dates, names from the documents. Never tell the user to "consult the document" — you have its text.
- Show year-over-year deltas, percentages, and trends when comparing periods.
- When presenting numerical data with 3+ data points, embed a chart in this exact format:

  ```chart
  {"type":"bar","data":{"labels":["FY2022","FY2023","FY2024"],"datasets":[{"label":"Revenue","data":[100,200,300],"backgroundColor":"#385854"}]},"options":{"responsive":true}}
  ```

  Supported types: bar, line, pie. Use `#385854` (AH brand) for the primary series.
- For tabular data, use proper markdown tables.
- Reference specific resolutions, ordinances, meeting dates.
- If you genuinely cannot find something after searching, say so plainly and suggest which doc types might contain it.
"""


REPORT_MODE_SUFFIX = """

## REPORT MODE ACTIVE

Structure your response as a formal analytical report:

**# Title**
One-line headline that captures the answer.

**## Executive Summary**
3-5 bullets. The decision-relevant takeaways.

**## Findings**
Numbered findings, each with:
- A clear claim
- Supporting numbers/citations
- Year-over-year context where relevant

**## Data**
Tables and charts. Always include at least one chart for any trend.

**## Sources**
Bulleted list of every document cited, in `[source: filename.pdf]` format.

Tone: authoritative, measured, evidence-driven. Lead with findings, not process."""


DEEP_THINKING_SUFFIX = """

## DEEP THINKING ACTIVE

You have extended reasoning enabled. Use it to:
- Cross-reference figures across multiple years/documents
- Detect inconsistencies between budgets and actuals
- Reason about the implications of policy changes
- Build the strongest analytical chain you can before writing the response.

After thinking, the user only sees your final response — make it crisp."""


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
    return {"count": len(rows), "note": "Red flags are leads for human review, not findings of wrongdoing.",
            "flags": [{"rule": f.rule, "severity": f.severity, "status": f.status, "title": f.title,
                       "explanation": f.explanation, "subject": f.subject, "amount": f.amount,
                       "evidence": [{k: e.get(k) for k in ("title", "page", "date", "quote", "amount")}
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

@router.get("/history")
def get_chat_history(
    session_id: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Verify the session belongs to this user (or admin can read any).
    if not user.is_admin:
        owner = db.execute(sql_text(
            "SELECT user_id FROM chat_history WHERE session_id = :sid LIMIT 1"
        ), {"sid": session_id}).fetchone()
        if owner is None:
            return {"session_id": session_id, "messages": []}
        if owner[0] is None or str(owner[0]) != str(user.id):
            raise HTTPException(status_code=404, detail="Session not found")

    rows = db.execute(sql_text(
        "SELECT role, content, metadata, created_at FROM chat_history "
        "WHERE session_id = :sid ORDER BY created_at"
    ), {"sid": session_id}).fetchall()
    return {
        "session_id": session_id,
        "messages": [
            {"role": r.role, "content": r.content, "metadata": r.metadata,
             "timestamp": r.created_at.isoformat()}
            for r in rows
        ],
    }


@router.get("/sessions")
def list_chat_sessions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.is_admin:
        rows = db.execute(sql_text("""
            SELECT session_id, scope_type, scope_id,
                   count(*) AS message_count,
                   max(created_at) AS last_activity,
                   (SELECT content FROM chat_history ch2
                    WHERE ch2.session_id = ch.session_id AND ch2.role = 'user'
                    ORDER BY created_at DESC LIMIT 1) AS last_query
            FROM chat_history ch
            GROUP BY session_id, scope_type, scope_id
            ORDER BY max(created_at) DESC
            LIMIT 30
        """)).fetchall()
    else:
        rows = db.execute(sql_text("""
            SELECT session_id, scope_type, scope_id,
                   count(*) AS message_count,
                   max(created_at) AS last_activity,
                   (SELECT content FROM chat_history ch2
                    WHERE ch2.session_id = ch.session_id AND ch2.role = 'user'
                    ORDER BY created_at DESC LIMIT 1) AS last_query
            FROM chat_history ch
            WHERE user_id = :uid
            GROUP BY session_id, scope_type, scope_id
            ORDER BY max(created_at) DESC
            LIMIT 30
        """), {"uid": str(user.id)}).fetchall()
    return [
        {
            "session_id": r.session_id,
            "scope_type": r.scope_type,
            "scope_id": r.scope_id,
            "message_count": r.message_count,
            "last_activity": r.last_activity.isoformat() if r.last_activity else None,
            "last_query": (r.last_query[:120] if r.last_query else None),
        }
        for r in rows
    ]


def _save_message(db: Session, session_id: str, role: str, content: str,
                  scope_type: str = "all", scope_id: Optional[str] = None,
                  user_id: Optional[str] = None) -> None:
    db.execute(sql_text(
        "INSERT INTO chat_history (id, session_id, scope_type, scope_id, role, content, user_id) "
        "VALUES (:id, :sid, :st, :si, :role, :content, :uid)"
    ), {"id": str(uuid.uuid4()), "sid": session_id, "st": scope_type,
        "si": scope_id, "role": role, "content": content, "uid": user_id})
    db.commit()



def _which_town() -> str:
    muni = get_municipality()
    if len(muni.entities) == 1:
        return f"All records belong to {muni.primary_entity.label}."
    return (
        "Results are tagged with `municipality` — the government that published the record:\n"
        f"{muni.entities_prompt()}\n"
        "When the user asks about one of these, pass the `municipality` filter to search_chunks, "
        "and never attribute one government's record to another (neighbouring towns and the school "
        "district are separate bodies with separate budgets)."
    )


def _jurisdiction_notes() -> str:
    muni = get_municipality()
    notes = list(muni.state_profile.ai_notes) if muni.state_profile else []
    fy = muni.fiscal_year
    if fy.start_month == 1:
        notes.append(f"{muni.short_name} runs on a calendar fiscal year.")
    else:
        notes.append(f"{muni.short_name}'s fiscal year starts in month {fy.start_month} "
                     f"(label fiscal years like 2025-26).")
    notes.append(f"School districts run on a fiscal year starting in month {fy.school_start_month}.")
    notes += muni.ai.notes
    return "\n".join(f"   - {n}" for n in notes)


def _working_instructions() -> str:
    # str.replace, not .format — the instructions contain literal JSON braces.
    return (WORKING_INSTRUCTIONS
            .replace("{which_town}", _which_town())
            .replace("{jurisdiction_notes}", _jurisdiction_notes()))

# ─── Streaming chat ─────────────────────────────────────────────────────────

def _split_system_parts(req: ChatRequest, doc_context: str) -> tuple[str, str]:
    """Return (stable_text, variable_text).

    `stable` is identical across iterations of an agentic loop and across
    requests with the same mode flags — it caches well. `variable` carries
    per-request context (attached doc, presentation summary, focused slide)
    so it gets its own cache breakpoint that hits on follow-up turns about
    the same target.
    """
    stable_parts = [_background(), _working_instructions()]
    if req.report_mode:
        stable_parts.append(REPORT_MODE_SUFFIX)
    if req.deep_thinking:
        stable_parts.append(DEEP_THINKING_SUFFIX)

    variable_parts: list[str] = []
    if doc_context:
        variable_parts.append(doc_context)
    if req.presentation_id and req.presentation_summary:
        variable_parts.append(
            "## ACTIVE PRESENTATION (deck mode)\n\n"
            f"{req.presentation_summary}\n\n"
            "When proposing changes, use the `propose_section` tool. To "
            "REPLACE an existing section, include its `section_id` from "
            "the list above. To APPEND a new section, omit `section_id`."
        )
    if req.presentation_id and req.target_section_id:
        variable_parts.append(
            "## FOCUSED SLIDE\n\n"
            f"The operator is currently focused on section "
            f"`{req.target_section_id}`. Default to editing THIS section "
            f"(pass `section_id={req.target_section_id}` to propose_section) "
            f"unless the request clearly asks to add a new section or edit a "
            f"different one."
        )

    return "\n\n".join(stable_parts), "\n\n".join(variable_parts)


def _build_system_prompt(req: ChatRequest, doc_context: str) -> str:
    """String form — used by Gemini fallback (no caching API)."""
    stable, variable = _split_system_parts(req, doc_context)
    return stable + ("\n\n" + variable if variable else "")


def _build_system_blocks(req: ChatRequest, doc_context: str) -> list[dict]:
    """Anthropic system message as cacheable content blocks.

    Block 1 (stable) gets cache_control so the long BACKGROUND +
    WORKING_INSTRUCTIONS payload is reused across iterations of an agentic
    loop — Anthropic's 5-min ephemeral cache means iterations 2-8 read the
    cached prefix at ~10% of the standard input rate.

    Block 2 (variable, if present) gets its own breakpoint so follow-up
    turns about the same attached doc or presentation also hit cache.
    """
    stable, variable = _split_system_parts(req, doc_context)
    blocks: list[dict] = [{
        "type": "text",
        "text": stable,
        "cache_control": {"type": "ephemeral"},
    }]
    if variable:
        blocks.append({
            "type": "text",
            "text": variable,
            "cache_control": {"type": "ephemeral"},
        })
    return blocks


def _attached_doc_context(db: Session, document_id: Optional[str]) -> str:
    """When the user attaches a single document, inline its first 30K chars
    so Claude doesn't need to call `read_document` first."""
    if not document_id:
        return ""
    doc = get_document_excerpt(db, document_id, max_chars=30000)
    if not doc:
        return ""
    return (
        f"## ATTACHED DOCUMENT (inline)\n\n"
        f"**{doc['filename']}** (FY {doc.get('fiscal_year') or '?'}, "
        f"{doc.get('doc_type') or 'document'})\n\n"
        f"{doc.get('excerpt') or ''}\n"
    )


def _sse(event: str, data: dict) -> str:
    """Format a Server-Sent Event with both the event name and JSON payload.
    The frontend listens by event name AND reads the type field (we send both
    so older clients that only parse `data:` frames keep working)."""
    payload = {"type": event, **data}
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


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


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    """SSE streaming chat with thinking + tool use + RAG.

    NOTE: this endpoint deliberately does NOT take `db: Session = Depends(get_db)`.
    A request-scoped session held across an SSE stream causes Postgres connection
    leaks: any auto-BEGUN transaction will sit "idle in transaction" if the
    request task is killed mid-stream. Each DB op below opens a short-lived
    SessionLocal() and closes it immediately.
    """
    if not ANTHROPIC_API_KEY and not GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="No LLM API key configured")

    session_id = req.session_id or str(uuid.uuid4())
    user_id_str = str(user.id)

    # Build the prompt with attached-doc context (small, opens/closes its own session).
    setup_db = SessionLocal()
    try:
        # If continuing a session, ensure it belongs to this user.
        if req.session_id and not user.is_admin:
            owner = setup_db.execute(sql_text(
                "SELECT user_id FROM chat_history WHERE session_id = :sid LIMIT 1"
            ), {"sid": session_id}).fetchone()
            if owner is not None and owner[0] is not None and str(owner[0]) != user_id_str:
                raise HTTPException(status_code=404, detail="Session not found")

        doc_context = _attached_doc_context(setup_db, req.document_id)
        history_rows = setup_db.execute(sql_text(
            "SELECT role, content FROM chat_history WHERE session_id = :sid ORDER BY created_at"
        ), {"sid": session_id}).fetchall() if req.session_id else []
        # Save the user message before streaming so it's persisted even if the
        # client disconnects mid-stream.
        setup_db.execute(sql_text(
            "INSERT INTO chat_history (id, session_id, scope_type, scope_id, role, content, user_id) "
            "VALUES (:id, :sid, :st, :si, :role, :content, :uid)"
        ), {"id": str(uuid.uuid4()), "sid": session_id, "st": req.scope_type,
            "si": req.scope_id, "role": "user", "content": req.query,
            "uid": user_id_str})
        setup_db.commit()
    finally:
        setup_db.close()

    history = [{"role": r.role, "content": r.content} for r in history_rows]

    # If only Gemini is configured, fall through to a simpler streamer (no tools).
    if not ANTHROPIC_API_KEY and GEMINI_API_KEY:
        return StreamingResponse(
            _stream_gemini(_build_system_prompt(req, doc_context), req.query, history,
                           session_id, req.scope_type, req.scope_id, user_id_str),
            media_type="text/event-stream",
            headers={"X-Session-Id": session_id, "Cache-Control": "no-cache",
                     "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    system_blocks = _build_system_blocks(req, doc_context)
    return StreamingResponse(
        _stream_claude(req, system_blocks, history, session_id, request, user_id_str),
        media_type="text/event-stream",
        headers={"X-Session-Id": session_id, "Cache-Control": "no-cache",
                 "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


async def _stream_claude(
    req: ChatRequest,
    system_blocks: list[dict],
    history: list[dict],
    session_id: str,
    request: Request,
    user_id: Optional[str] = None,
):
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    tools = _tool_defs()
    if req.web_search:
        tools.append(_web_search_tool())
    # Cache the tool definitions block — they're identical across iterations
    # of the agentic loop, so iterations 2-8 hit the prompt cache.
    if tools:
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    if req.presentation_id:
        tools.append(_propose_section_tool())

    messages: list[dict] = list(history[-30:])
    messages.append({"role": "user", "content": req.query})

    accumulated_text = ""
    tool_calls_made = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read = 0
    total_cache_write = 0
    continuation_count = 0
    max_continuations = 3

    # Deep thinking pumps the budget/model up; cheap mode keeps sonnet-4-6 fast.
    # Opus 4.7 removed the old {type: "enabled", budget_tokens: N} shape — the
    # model decides depth on its own under adaptive, and effort/intelligence
    # is steered via output_config.effort (low/medium/high/xhigh/max).
    output_config_cfg: Optional[dict] = None
    if req.deep_thinking:
        model = "claude-opus-4-7"
        max_tokens = 32000
        thinking_cfg: Optional[dict] = {"type": "adaptive"}
        output_config_cfg = {"effort": "high"}
    else:
        model = "claude-sonnet-4-6"
        max_tokens = 16000
        thinking_cfg = None

    try:
        yield _sse("session", {"session_id": session_id})
        yield _sse("status", {"text": "Building context…", "stage": "context"})

        for iteration in range(8):
            if await request.is_disconnected():
                return

            yield _sse("status", {
                "text": ("Calling Claude (deep thinking)…" if req.deep_thinking
                         else "Calling Claude…"),
                "stage": "model_call",
                "iteration": iteration + 1,
            })

            _mark_last_message_for_cache(messages)
            api_kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system_blocks,
                "tools": tools,
                "messages": messages,
                # 1M-token context window beta. Tiered pricing — first 200K at
                # standard rate, the rest at premium. Anthropic's policy.
                "extra_headers": {"anthropic-beta": "context-1m-2025-08-07"},
            }
            if thinking_cfg:
                api_kwargs["thinking"] = thinking_cfg
            if output_config_cfg:
                api_kwargs["output_config"] = output_config_cfg

            iter_text = ""
            thinking_active = False
            last_keepalive = asyncio.get_event_loop().time()

            async with client.messages.stream(**api_kwargs) as stream:
                async for event in stream:
                    if await request.is_disconnected():
                        return

                    et = getattr(event, "type", None)
                    if et == "content_block_start":
                        block = event.content_block
                        bt = getattr(block, "type", None)
                        if bt == "thinking":
                            thinking_active = True
                            yield _sse("thinking", {"status": "started"})
                        elif bt == "text" and thinking_active:
                            thinking_active = False
                            yield _sse("thinking", {"status": "done"})
                        elif bt == "tool_use":
                            yield _sse("tool_use", {
                                "name": getattr(block, "name", ""),
                                "id": getattr(block, "id", ""),
                            })
                    elif et == "content_block_delta":
                        delta = event.delta
                        dt = getattr(delta, "type", None)
                        if dt == "thinking_delta":
                            now = asyncio.get_event_loop().time()
                            if now - last_keepalive >= 20.0:
                                yield ": keepalive\n\n"
                                last_keepalive = now
                        elif dt == "text_delta":
                            chunk = getattr(delta, "text", "")
                            iter_text += chunk
                            accumulated_text += chunk
                            yield _sse("delta", {"content": chunk, "session_id": session_id})

                final_message = await stream.get_final_message()
                if thinking_active:
                    yield _sse("thinking", {"status": "done"})

                # Accumulate token usage across iterations.
                if getattr(final_message, "usage", None):
                    u = final_message.usage
                    total_input_tokens += getattr(u, "input_tokens", 0) or 0
                    total_output_tokens += getattr(u, "output_tokens", 0) or 0
                    total_cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
                    total_cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0

                stop_reason = final_message.stop_reason
                tool_blocks = [b for b in final_message.content if getattr(b, "type", None) == "tool_use"]

                # Auto-continue: if Claude hit max_tokens during plain text
                # generation (no pending tool_use), feed the partial response
                # back and let it resume seamlessly. We bound this to
                # max_continuations to avoid runaway loops.
                if stop_reason == "max_tokens":
                    has_tool_use = any(
                        getattr(b, "type", None) == "tool_use" for b in final_message.content
                    )
                    if (iter_text and not has_tool_use
                            and continuation_count < max_continuations):
                        continuation_count += 1
                        messages.append({"role": "assistant", "content": iter_text})
                        messages.append({
                            "role": "user",
                            "content": "Continue exactly where you left off. Do not repeat or summarize what you've already written.",
                        })
                        yield _sse("continuation", {
                            "count": continuation_count,
                            "max": max_continuations,
                        })
                        continue
                    # Out of continuations or had a pending tool_use — wrap up.
                    if iter_text:
                        truncation_note = "\n\n---\n\n*Response reached the token limit. Ask a follow-up to continue, or open a new chat for more depth.*"
                        accumulated_text += truncation_note
                        yield _sse("delta", {"content": truncation_note, "session_id": session_id})

                if not tool_blocks or stop_reason != "tool_use":
                    # Final answer reached.
                    save_db = SessionLocal()
                    try:
                        _save_message(save_db, session_id, "assistant", accumulated_text,
                                      req.scope_type, req.scope_id, user_id=user_id)
                        from services.usage import record_usage
                        record_usage(
                            save_db, source="chat", model=model,
                            input_tokens=total_input_tokens,
                            output_tokens=total_output_tokens,
                            estimated_cost_usd=_estimate_cost(
                                model, total_input_tokens, total_output_tokens,
                                total_cache_read, total_cache_write),
                            user_id=user_id,
                            resource_type="chat_session", resource_id=session_id,
                            metadata={"tool_calls": tool_calls_made,
                                      "stop_reason": stop_reason,
                                      "cache_read_tokens": total_cache_read,
                                      "cache_write_tokens": total_cache_write},
                        )
                    finally:
                        save_db.close()
                    cost = _estimate_cost(model, total_input_tokens, total_output_tokens,
                                          total_cache_read, total_cache_write)
                    yield _sse("done", {"session_id": session_id,
                                        "tool_calls_made": tool_calls_made,
                                        "stop_reason": stop_reason,
                                        "input_tokens": total_input_tokens,
                                        "output_tokens": total_output_tokens,
                                        "estimated_cost_usd": round(cost, 4)})
                    return

                tool_calls_made += len(tool_blocks)

                # Run tools (each in its own short-lived session, in a thread).
                def run_tool(name: str, args: dict) -> dict:
                    s = SessionLocal()
                    try:
                        return _execute_tool(name, args, s)
                    finally:
                        s.close()

                yield _sse("status", {
                    "text": f"Running {len(tool_blocks)} tool{'s' if len(tool_blocks) != 1 else ''}…",
                    "stage": "tools",
                })

                # Announce every call first, then run them concurrently:
                # Claude often asks for several independent searches at once.
                for tb in tool_blocks:
                    # share_plan / propose_section are special — emit dedicated
                    # SSE events and don't render the generic tool_call spinner.
                    if tb.name == "share_plan":
                        plan_input = dict(tb.input or {})
                        yield _sse("plan", {
                            "steps": plan_input.get("steps") or [],
                            "rationale": plan_input.get("rationale") or "",
                        })
                    elif tb.name == "propose_section":
                        prop_input = dict(tb.input or {})
                        yield _sse("proposal", {"input": prop_input})
                    else:
                        yield _sse("tool_call", {
                            "name": tb.name,
                            "input": tb.input,
                            "description": _describe_tool_call(tb.name, dict(tb.input or {})),
                        })
                outputs = await asyncio.gather(
                    *(asyncio.to_thread(run_tool, tb.name, tb.input) for tb in tool_blocks)
                )
                results = []
                for tb, res in zip(tool_blocks, outputs):
                    if tb.name not in ("share_plan", "propose_section"):
                        summary = _summarize_tool_result(tb.name, res)
                        yield _sse("tool_result", {"name": tb.name, "summary": summary})
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": tb.id,
                        "content": _fit_json(res),
                    })

                messages.append({"role": "assistant", "content": final_message.content})
                messages.append({"role": "user", "content": results})

        # Hit the iteration cap — emit what we have.
        save_db = SessionLocal()
        try:
            _save_message(save_db, session_id, "assistant", accumulated_text,
                          req.scope_type, req.scope_id, user_id=user_id)
            from services.usage import record_usage
            record_usage(
                save_db, source="chat", model=model,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                estimated_cost_usd=_estimate_cost(
                    model, total_input_tokens, total_output_tokens,
                    total_cache_read, total_cache_write),
                user_id=user_id,
                resource_type="chat_session", resource_id=session_id,
                metadata={"tool_calls": tool_calls_made,
                          "stop_reason": "iteration_limit",
                          "cache_read_tokens": total_cache_read,
                          "cache_write_tokens": total_cache_write},
            )
        finally:
            save_db.close()
        cost = _estimate_cost(model, total_input_tokens, total_output_tokens,
                              total_cache_read, total_cache_write)
        yield _sse("done", {"session_id": session_id,
                            "tool_calls_made": tool_calls_made,
                            "stop_reason": "iteration_limit",
                            "input_tokens": total_input_tokens,
                            "output_tokens": total_output_tokens,
                            "estimated_cost_usd": round(cost, 4)})

    except anthropic.APIStatusError as exc:
        logger.error("Anthropic API error: %s", exc)
        msg = "AI service error. "
        if getattr(exc, "status_code", 0) == 429:
            msg += "Rate limit reached, try again in a moment."
        elif getattr(exc, "status_code", 0) >= 500:
            msg += "Service temporarily unavailable."
        else:
            msg += getattr(exc, "message", str(exc))
        yield _sse("error", {"content": msg})
    except Exception as exc:
        logger.exception("chat_stream failed")
        yield _sse("error", {"content": str(exc)[:300]})


def _estimate_cost(model: str, input_tokens: int, output_tokens: int,
                   cache_read: int = 0, cache_write: int = 0) -> float:
    """Approximate USD. input_tokens excludes cached tokens (Anthropic reports
    them separately): cache reads bill at 0.1x input, cache writes at 1.25x."""
    if "opus" in model.lower():
        in_rate, out_rate = 15.0, 75.0
    else:
        in_rate, out_rate = 3.0, 15.0
    return (input_tokens * in_rate + cache_write * in_rate * 1.25
            + cache_read * in_rate * 0.1 + output_tokens * out_rate) / 1_000_000


def _mark_last_message_for_cache(messages: list) -> None:
    """Move the conversation cache breakpoint to the end of the newest message,
    so each tool-loop iteration re-reads prior tool results from cache (0.1x)
    instead of re-billing them at full price. One breakpoint here + system +
    tools stays within Anthropic's limit of 4."""
    for m in messages:
        if isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict):
                    b.pop("cache_control", None)
    last = messages[-1]
    if isinstance(last.get("content"), str):
        last["content"] = [{"type": "text", "text": last["content"]}]
    if isinstance(last.get("content"), list) and last["content"] and isinstance(last["content"][-1], dict):
        last["content"][-1]["cache_control"] = {"type": "ephemeral"}


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


async def _stream_gemini(system_prompt, query, history, session_id, scope_type, scope_id, user_id=None):
    """Fallback streamer when only Gemini is configured. No tool use — single shot."""
    try:
        from google import genai
        from google.genai import types as gtypes

        client = genai.Client(api_key=GEMINI_API_KEY)
        conv = [system_prompt + "\n\n"]
        for msg in history[-10:]:
            conv.append(("User: " if msg["role"] == "user" else "Assistant: ") + msg["content"] + "\n\n")
        conv.append(f"User: {query}")

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.models.generate_content(
                model="gemini-2.5-flash",
                contents="".join(conv),
                config=gtypes.GenerateContentConfig(temperature=0.3, max_output_tokens=8192),
            ),
        )
        full = response.text or "" if response else ""
        yield _sse("session", {"session_id": session_id})
        for i in range(0, len(full), 30):
            yield _sse("delta", {"content": full[i:i + 30], "session_id": session_id})
            await asyncio.sleep(0.005)

        # Pull token counts from Gemini's usage_metadata when present.
        in_t = out_t = 0
        try:
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                in_t = int(getattr(usage, "prompt_token_count", 0) or 0)
                out_t = int(getattr(usage, "candidates_token_count", 0) or 0)
        except Exception:
            pass

        save_db = SessionLocal()
        try:
            _save_message(save_db, session_id, "assistant", full, scope_type, scope_id, user_id=user_id)
            from services.usage import record_usage
            record_usage(
                save_db, source="chat", model="gemini-2.5-flash",
                input_tokens=in_t, output_tokens=out_t,
                user_id=user_id,
                resource_type="chat_session", resource_id=session_id,
            )
        finally:
            save_db.close()
        yield _sse("done", {"session_id": session_id})
    except Exception as exc:
        logger.exception("gemini stream failed")
        yield _sse("error", {"content": str(exc)[:300]})
