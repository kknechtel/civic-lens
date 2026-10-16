"""
Multi-pass financial extractor.

Strategy:
  Pass 1 — TOC: convert PDF to markdown, segment into sections by heading patterns
  Pass 2 — Per-section extraction in PARALLEL: each section gets its own LLM call
           with a focused prompt and the full output budget. Big budgets / CAFRs that
           overflow Gemini's 65K output cap in a single shot fit comfortably this way.
  Pass 3 — Header extract: entity, FY, top-level summary numbers
  Pass 4 — Code parse + fund classification: regex out NJ program-function-object
           codes and label each line with its fund.
  Pass 5 — Reconcile: sum line items by section, compare to summary totals.

Entry point: extract_financial_statement_v2(statement_id, s3_key)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from config import GEMINI_API_KEY, ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)


# ─── Section detection ───────────────────────────────────────────────────────

# Headings that mark a financial section. Order matters — earlier patterns win.
# Each entry: (regex, normalized_section_name).
#
# We accept three heading styles because OCR'd ACFRs vary:
#   1. Markdown ATX:   "## Revenues"
#   2. Bolded caps:    "**REVENUES**"   (Gemini and pymupdf4llm both produce this)
#   3. Plain ALL-CAPS line:  "REVENUES"  (no formatting at all)
# The keyword set is the same; only the heading-style prefix differs.
def _section_re(*keywords: str) -> re.Pattern:
    """Build a regex matching any of `keywords` as a heading line. Allows
    markdown-ATX (#+), bold-double-asterisk, or just an isolated UPPERCASE line."""
    kw_alt = "|".join(keywords)
    pattern = (
        r"(?:"
        r"^\s*#+\s*(?:" + kw_alt + r")\b"          # ## Revenues
        r"|^\s*\*\*\s*(?:" + kw_alt + r")\b"       # **Revenues
        r"|^[ \t]*(?:" + kw_alt + r")[ \t]*$"      # bare REVENUES line
        r")"
    )
    return re.compile(pattern, re.I | re.M)

SECTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (_section_re("revenues?", "anticipated revenues?", "estimated revenues?",
                 "statement of revenues, expenditures",
                 "statements of revenues, expenditures"), "Revenue"),
    (_section_re("expenditures?", "appropriations?", "operating expenditures?",
                 "statement of activities", "statements of activities"), "Expenditures"),
    (_section_re("debt service", "long[-\\s]term debt", "bonds payable",
                 "schedule of bond", "schedule of debt"), "Debt Service"),
    (_section_re("fund balances?", "net position", "equity",
                 "changes in fund balance"), "Fund Balance"),
    (_section_re("assets?", "statement of net position",
                 "statements of net position", "balance sheet"), "Assets"),
    (_section_re("liabilities", "deferred inflows"), "Liabilities"),
    (_section_re("capital outlay", "capital projects?", "capital assets?"), "Capital"),
    (_section_re("state aid", "federal aid", "grants?",
                 "intergovernmental"), "Aid"),
    (_section_re("salaries?", "personnel", "compensation"), "Personnel"),
    # NJ ACFR Exhibit codes — every standard exhibit maps to a known section.
    # Match "Exhibit B-1", "EXHIBIT C-2", "Schedule J-1" anywhere on a line.
    (re.compile(r"\b(?:Exhibit|EXHIBIT|Schedule|SCHEDULE)\s+A-1\b", re.M), "Assets"),
    (re.compile(r"\b(?:Exhibit|EXHIBIT|Schedule|SCHEDULE)\s+A-2\b", re.M), "Expenditures"),
    (re.compile(r"\b(?:Exhibit|EXHIBIT|Schedule|SCHEDULE)\s+B-1\b", re.M), "Assets"),
    (re.compile(r"\b(?:Exhibit|EXHIBIT|Schedule|SCHEDULE)\s+B-2\b", re.M), "Revenue"),
    (re.compile(r"\b(?:Exhibit|EXHIBIT|Schedule|SCHEDULE)\s+B-3\b", re.M), "Fund Balance"),
    (re.compile(r"\b(?:Exhibit|EXHIBIT|Schedule|SCHEDULE)\s+C-1\b", re.M), "Assets"),
    (re.compile(r"\b(?:Exhibit|EXHIBIT|Schedule|SCHEDULE)\s+C-2\b", re.M), "Revenue"),
    (re.compile(r"\b(?:Exhibit|EXHIBIT|Schedule|SCHEDULE)\s+C-3\b", re.M), "Fund Balance"),
    (re.compile(r"\b(?:Exhibit|EXHIBIT|Schedule|SCHEDULE)\s+I-\d+\b", re.M), "Debt Service"),
    (re.compile(r"\b(?:Exhibit|EXHIBIT|Schedule|SCHEDULE)\s+J-\d+\b", re.M), "Statistical"),
]

# How big a section can be before we further split it (chars). Roughly 60-80K tokens.
MAX_SECTION_CHARS = 240_000


def segment_by_sections(markdown: str) -> List[Tuple[str, str]]:
    """Return [(section_name, section_text), ...]. Falls back to one chunk if no headings found."""
    matches = []
    for pattern, name in SECTION_PATTERNS:
        for m in pattern.finditer(markdown):
            matches.append((m.start(), name))

    if not matches:
        # No recognizable sections — split by character-budget chunks
        return _chunk_by_size(markdown, "Document")

    matches.sort(key=lambda x: x[0])

    out = []
    for i, (start, name) in enumerate(matches):
        end = matches[i + 1][0] if i + 1 < len(matches) else len(markdown)
        text = markdown[start:end].strip()
        if not text:
            continue
        if len(text) > MAX_SECTION_CHARS:
            for sub_name, sub_text in _chunk_by_size(text, name):
                out.append((sub_name, sub_text))
        else:
            out.append((name, text))

    # Prepend the head of the document (before any matched section) — usually has summary tables
    head_end = matches[0][0]
    if head_end > 200:
        out.insert(0, ("Header/Summary", markdown[:head_end].strip()))

    return out


def _chunk_by_size(text: str, base_name: str) -> List[Tuple[str, str]]:
    chunks = []
    pos = 0
    idx = 1
    while pos < len(text):
        end = min(pos + MAX_SECTION_CHARS, len(text))
        # Don't slice mid-line if we can avoid it
        if end < len(text):
            nl = text.rfind("\n\n", pos + MAX_SECTION_CHARS // 2, end)
            if nl > 0:
                end = nl
        chunks.append((f"{base_name} (part {idx})", text[pos:end]))
        pos = end
        idx += 1
    return chunks


# ─── Account code parsing (NJ schools — Uniform Minimum COA) ─────────────────

# NJ school account codes follow program-function-object[-suffix], with district
# extensions adding location/sub-program suffixes. Real codes range from 3 to 7
# numeric segments. Each segment is 2-5 digits. Hyphens, spaces, or dots may
# separate segments.
#
# Known fund prefixes (Uniform Minimum COA + NJDOE 2024-25 Audit Program):
#   11 = General Current Expense       12 = Capital Outlay
#   13 = Special Schools               20 = Special Revenue (federal/state grants)
#   30 = Capital Projects              40 = Debt Service
#   60 = Enterprise (Food Service)     63 = Cooperative Programs
#   70/80 = Trust & Agency / Fiduciary
ACCOUNT_CODE_RE = re.compile(
    r"\b(\d{2,3})[-.\s](\d{2,5})[-.\s](\d{2,5})(?:[-.\s](\d{2,5}))?(?:[-.\s](\d{2,5}))?\b"
)

NJ_SCHOOL_FUND_WHITELIST = {"11", "12", "13", "20", "30", "40", "60", "63", "70", "80", "90"}


def parse_account_code(line_name: str, raw_metadata: Optional[Dict] = None) -> Dict[str, Optional[str]]:
    """
    Extract NJ program-function-object[-loc][-subloc] code.
    Returns {account_code, program_code, function_code, object_code, fund}.
    Returns all None unless the leading segment matches the NJ fund whitelist —
    avoids false-positives on phone numbers, ZIPs, etc.
    """
    raw_metadata = raw_metadata or {}
    candidate = raw_metadata.get("account_code") or line_name or ""
    m = ACCOUNT_CODE_RE.search(str(candidate))
    if not m:
        return {"account_code": None, "program_code": None,
                "function_code": None, "object_code": None, "fund": None}

    program = m.group(1)
    if program not in NJ_SCHOOL_FUND_WHITELIST:
        return {"account_code": None, "program_code": None,
                "function_code": None, "object_code": None, "fund": None}

    segments = [g for g in m.groups() if g]
    full = "-".join(segments)
    return {
        "account_code": full,
        "program_code": segments[0] if len(segments) > 0 else None,
        "function_code": segments[1] if len(segments) > 1 else None,
        "object_code": segments[2] if len(segments) > 2 else None,
        "fund": _fund_from_program(program),
    }


def _fund_from_program(program: str) -> Optional[str]:
    """Map NJ school fund prefix to fund category. Sources: NJDOE 2024-25 Audit
    Program §1; NJASBO Uniform Minimum COA."""
    p = program.strip()
    return {
        "11": "general",          # General Current Expense
        "12": "capital",          # Capital Outlay (within General Fund)
        "13": "general",          # Special Schools (within General Fund)
        "20": "special_revenue",  # Federal/State Grants (Special Revenue Fund)
        "30": "capital_projects", # Capital Projects Fund
        "40": "debt_service",     # Debt Service Fund (separate from 30)
        "60": "enterprise",       # Food Service / Enterprise
        "63": "enterprise",       # Cooperative Programs
        "70": "trust",            # Trust & Agency / Fiduciary
        "80": "trust",
    }.get(p)


def classify_fund_from_section(section: Optional[str], subsection: Optional[str]) -> Optional[str]:
    """Best-effort fund classification when no account code is present."""
    s = (section or "").lower() + " " + (subsection or "").lower()
    if "debt" in s:
        return "debt_service"
    if "capital" in s:
        return "capital"
    if "trust" in s or "agency" in s:
        return "trust"
    if "enterprise" in s or "utility" in s or "water" in s or "sewer" in s:
        return "enterprise"
    if "special revenue" in s or "grant" in s:
        return "special_revenue"
    return "general"  # default


# ─── Prompts ─────────────────────────────────────────────────────────────────

HEADER_PROMPT = """Extract the document header info from this government financial statement.
Return ONLY JSON:
{{
  "entity_name": "...",
  "entity_type": "town|school",
  "statement_type": "budget|audit|cafr|annual_report|user_friendly_budget",
  "fiscal_year": "YYYY or YYYY-YYYY",
  "fiscal_year_start": "YYYY-MM-DD or null",
  "fiscal_year_end": "YYYY-MM-DD or null",
  "summary": {{
    "total_revenue": <number or null>,
    "total_expenditures": <number or null>,
    "surplus_deficit": <number or null>,
    "fund_balance": <number or null>,
    "total_debt": <number or null>
  }}
}}

DOCUMENT (first portion):
{markdown}

Output ONLY valid JSON. Use null for missing fields. All numbers as bare numbers (no $ or commas).
"""


TABLE_ROW_PROMPT = """Extract EVERY data row from this NJ government financial document.

The document is dominated by markdown tables (no section headings). It is most
likely a User-Friendly Budget (UFB), Annual Financial Statement (AFS), Annual
Debt Statement, or scanned town budget. Each meaningful row has a category
label, a description, possibly an account code, and one or more dollar amounts.

Return ONLY JSON:
{{
  "line_items": [
    {{
      "section": "Use the row's 'Budget Category', 'Fund', or first column header as the section. Common values: 'General Fund Revenues', 'General Fund Appropriations', 'Special Revenue Fund', 'Debt Service Fund', 'Capital Outlay', 'Current Fund', 'Water/Sewer Utility', 'Harbor Commission'.",
      "subsection": "Sub-category if present (e.g. 'Local Sources', 'State Aid', 'Regular Programs - Instruction')",
      "line_name": "EXACT description text from the row",
      "account_code": "NJ COA code from the row if present (e.g. '10-1210', '11-130-100-101'), else null",
      "amount": <most recent year's amount, or null>,
      "prior_year_amount": <prior year amount, or null>,
      "budget_amount": <budget/proposed amount, or null>,
      "is_total_row": <true for 'Total', 'Subtotal', 'Grand Total' rows>,
      "line_order": <integer>
    }}
  ]
}}

RULES:
1. Extract EVERY row with a numeric value. Do not skip rows just because the
   description is repetitive.
2. Strip $, commas, parentheses; preserve sign — (1,234) → -1234.
3. If a row spans multiple years (e.g. UFB has Actual/Revised/Proposed):
   - amount = most recent / proposed
   - prior_year_amount = older actual
   - budget_amount = the budget/revised column if distinct from amount
4. If section column is blank for a row but adjacent rows have a section,
   inherit that section.
5. Skip enrollment counts, demographic tables, and narrative — only financial
   rows (rows with a dollar/account code).

DOCUMENT TEXT:
{section_text}

Output ONLY the JSON object, no commentary or code fences.
"""


SECTION_PROMPT = """Extract EVERY line item from this section of a NJ government financial statement.
Section: {section_name}

Return ONLY JSON:
{{
  "line_items": [
    {{
      "section": "Revenue|Expenditures|Debt Service|Fund Balance|Assets|Liabilities|Capital|Aid|Personnel|Other",
      "subsection": "e.g. Property Taxes, Regular Programs - Instruction, General Administration",
      "line_name": "EXACT line item label as printed in the document",
      "account_code": "NJ code if present in the row e.g. 11-130-100-101 (or null)",
      "amount": <number or null>,
      "prior_year_amount": <number or null>,
      "budget_amount": <number or null>,
      "is_total_row": <true if this row is a subtotal/total/grand total>,
      "line_order": <integer>
    }}
  ]
}}

RULES:
1. Extract EVERY non-blank line that has a number. Do not skip.
2. Set is_total_row=true for "TOTAL", "Subtotal", "Grand Total", section sums.
3. Use positive numbers; preserve sign for credits/(parenthesized) values as negative.
4. Strip $, commas, and quotes from numbers.
5. If a row shows multiple years side-by-side, put the most recent in `amount` and prior in `prior_year_amount`.
6. Preserve the document's order in line_order.
7. If the row has a budget vs actual, put the budget figure in `budget_amount` and actual in `amount`.

SECTION TEXT:
{section_text}

Output ONLY the JSON object, no commentary or code fences.
"""


# ─── LLM helpers ─────────────────────────────────────────────────────────────


def _record_extractor_usage(model: str, in_t: int, out_t: int) -> None:
    """Best-effort usage record for batch financial extraction. user_id=None
    because these calls are typically batch jobs."""
    if not (in_t or out_t):
        return
    try:
        from database import SessionLocal
        from services.usage import record_usage
        sess = SessionLocal()
        try:
            record_usage(
                sess, source="financial_extraction", model=model,
                input_tokens=in_t, output_tokens=out_t,
            )
        finally:
            sess.close()
    except Exception:
        logger.debug("financial_extraction usage record skipped", exc_info=True)


async def _call_gemini(prompt: str, max_output: int = 65000) -> Optional[Dict]:
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        cfg_kwargs = dict(temperature=0.0, max_output_tokens=max_output)
        try:
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass  # SDK on EC2 may reject thinking_budget — skip
        config = types.GenerateContentConfig(**cfg_kwargs)
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt, config=config,
            ),
        )
        if not response or not response.text:
            return None
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            _record_extractor_usage(
                "gemini-2.5-flash",
                int(getattr(usage, "prompt_token_count", 0) or 0),
                int(getattr(usage, "candidates_token_count", 0) or 0),
            )
        return _parse_json(response.text)
    except Exception as exc:
        logger.warning("gemini call failed: %s", exc)
        return None


async def _call_claude(prompt: str, max_output: int = 16000) -> Optional[Dict]:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        # Use streaming unconditionally — non-streaming .create() rejects
        # max_tokens above the 10-minute estimate threshold.
        text_parts: list[str] = []
        in_t = out_t = 0
        async with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=max_output,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for chunk in stream.text_stream:
                text_parts.append(chunk)
            try:
                final = await stream.get_final_message()
                in_t = getattr(final.usage, "input_tokens", 0) or 0
                out_t = getattr(final.usage, "output_tokens", 0) or 0
            except Exception:
                pass
        _record_extractor_usage("claude-sonnet-4-6", in_t, out_t)
        text = "".join(text_parts)
        if not text:
            return None
        return _parse_json(text)
    except Exception as exc:
        logger.warning("claude call failed: %s", exc)
        return None


async def _call_llm(prompt: str, max_output: int = 32000) -> Optional[Dict]:
    """Try Gemini (cheap, fast) first, fall back to Claude."""
    out = await _call_gemini(prompt, max_output=max(max_output, 32000))
    if out is not None:
        return out
    return await _call_claude(prompt, max_output=min(max_output, 16000))


def _result_is_empty_or_useless(out: Optional[Dict], expected_key: str = "line_items") -> bool:
    """An LLM response counts as 'useless' if it's None, empty dict, or contains
    only zeros/nulls under the expected key. Used by retry-with-other-model logic
    so that a Gemini hallucination of '[]' doesn't kill the section."""
    if not out:
        return True
    items = out.get(expected_key)
    if items is None:
        # Header pass — useless if entity_name/fiscal_year both null
        if expected_key == "line_items":
            return True
        return False
    if not items:
        return True
    # Items exist — sanity-check at least one has a usable amount
    real = [
        i for i in items
        if isinstance(i, dict) and (i.get("amount") not in (None, 0, "0", "", "null"))
    ]
    return len(real) == 0


async def _call_llm_with_retry(
    prompt: str,
    max_output: int = 32000,
    expected_key: str = "line_items",
    label: str = "",
) -> Optional[Dict]:
    """Call _call_llm. If the result is null/empty/all-zero, retry once with
    the OTHER provider — Gemini sometimes returns "[]" for tables it should
    have parsed; Claude sometimes does the inverse on dense ACFR pages."""
    first = await _call_llm(prompt, max_output=max_output)
    if not _result_is_empty_or_useless(first, expected_key):
        return first
    logger.info("retry-with-claude (%s): primary returned empty/null", label or "section")
    second = await _call_claude(prompt, max_output=min(max_output, 16000))
    if not _result_is_empty_or_useless(second, expected_key):
        return second
    # Fall back to whichever is non-None even if "useless" so caller sees structure
    return first or second


def _parse_json(text: str) -> Optional[Dict]:
    if not text:
        return None
    text = text.strip()
    # Strip ``` fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.startswith("json"):
            text = text[4:].strip()
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Best-effort: find first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError as exc:
                logger.error("could not salvage JSON: %s", exc)
                return None
        logger.error("not JSON")
        return None


# ─── PDF → markdown ──────────────────────────────────────────────────────────

def _pdf_to_markdown_sync(content: bytes) -> Optional[str]:
    """CPU-heavy work — runs in thread pool, must NOT be awaited directly."""
    try:
        import pymupdf
        import pymupdf4llm
        doc = pymupdf.open(stream=content, filetype="pdf")
        md = pymupdf4llm.to_markdown(doc)
        doc.close()
        return md
    except Exception as exc:
        logger.error("pdf->md failed: %s", exc)
        return None


async def pdf_to_markdown(content: bytes) -> Optional[str]:
    """Run pymupdf parsing in a worker thread. Calling pymupdf directly from an
    async function blocks the uvicorn event loop for the duration of the parse
    (often 30-60s on a 100+ page PDF) — gunicorn master sees the worker as
    unresponsive and sends SIGTERM. Threadpool keeps the loop alive."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _pdf_to_markdown_sync, content)


# ─── Main entry point ────────────────────────────────────────────────────────


async def extract_financial_statement_v2(statement_id: str, s3_key: str):
    """Background task. Multi-pass extraction with reconcile + anomaly detection."""
    from database import SessionLocal
    from models.financial import FinancialStatement, FinancialLineItem
    from services.s3_service import S3Service
    from services.financial_reconcile import reconcile_statement
    from services.financial_anomaly import detect_anomalies_for_statement

    db = SessionLocal()
    s3 = S3Service()

    try:
        stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
        if not stmt:
            logger.error("statement %s not found", statement_id)
            return

        stmt.status = "processing"
        db.commit()

        # Prefer the document's already-OCR'd text over re-running pymupdf.
        # The document.extracted_text column is populated by the OCR pipeline
        # (services.ocr_pipeline: text layer + Tesseract) — scanned
        # PDFs would fail pymupdf4llm here ("pdf->markdown failed") even though
        # we already have good OCR markdown for them.
        from models.document import Document
        doc = db.query(Document).filter(Document.id == stmt.document_id).first()
        markdown = None
        if doc and doc.extracted_text and len(doc.extracted_text) > 1000:
            markdown = doc.extracted_text
            logger.info("using cached OCR text (%d chars) for stmt %s",
                        len(markdown), statement_id)
        else:
            # No cached OCR — run pymupdf4llm against the S3 PDF.
            loop = asyncio.get_event_loop()
            content = await loop.run_in_executor(None, s3.download_file, s3_key)
            markdown = await pdf_to_markdown(content)
        if not markdown:
            stmt.status = "error"
            stmt.notes = "pdf->markdown failed"
            db.commit()
            return

        # Pass 1: header. Retry-with-other-provider if entity_name + fiscal_year
        # both come back null — that's the failure mode that left 15 statements
        # in `processing` with `fiscal_year=Unknown` after the May 5 ingest.
        head_text = markdown[:40_000]
        header = await _call_llm(HEADER_PROMPT.format(markdown=head_text), max_output=8000)
        header_useless = (
            not header
            or (not header.get("entity_name") and not header.get("fiscal_year"))
        )
        if header_useless:
            logger.info("header retry: primary LLM returned no entity/year for stmt %s", statement_id)
            retry = await _call_claude(HEADER_PROMPT.format(markdown=head_text), max_output=8000)
            if retry and (retry.get("entity_name") or retry.get("fiscal_year")):
                header = retry
        if header:
            summary = header.get("summary") or {}
            stmt.entity_name = header.get("entity_name") or stmt.entity_name
            stmt.fiscal_year = header.get("fiscal_year") or stmt.fiscal_year
            stmt.total_revenue = _num(summary.get("total_revenue"))
            stmt.total_expenditures = _num(summary.get("total_expenditures"))
            stmt.surplus_deficit = _num(summary.get("surplus_deficit"))
            stmt.fund_balance = _num(summary.get("fund_balance"))
            stmt.total_debt = _num(summary.get("total_debt"))
            # Infer accounting basis + fiscal calendar from entity_type + statement_type
            stmt.accounting_basis = _infer_accounting_basis(stmt.entity_type, stmt.statement_type)
            stmt.fiscal_calendar = _infer_fiscal_calendar(stmt.entity_type)
            db.commit()

        # Pass 2: section-by-section extraction (parallel)
        sections = segment_by_sections(markdown)
        logger.info("extractor v2: %d sections for statement %s", len(sections), statement_id)

        # If section detection collapsed (only 1 generic chunk) and the markdown
        # is mostly tables (heavy use of `|` row separators), use the table-row
        # prompt instead of the section prompt — UFB-format budgets and old
        # scanned AFS docs have no section headings, just tables.
        is_table_only = (
            len(sections) == 1
            and sections[0][0].startswith("Document")
            and markdown.count("|") > 200
            and (markdown.count("|") / max(len(markdown), 1)) > 0.005
        )
        if is_table_only:
            logger.info(
                "stmt %s using TABLE_ROW_PROMPT (table-only doc, %d pipes, no headings)",
                statement_id, markdown.count("|"),
            )

        async def extract_section(name: str, text: str) -> List[Dict]:
            prompt_template = TABLE_ROW_PROMPT if is_table_only else SECTION_PROMPT
            if is_table_only:
                prompt = prompt_template.format(section_text=text)
            else:
                prompt = prompt_template.format(section_name=name, section_text=text)
            # Retry-with-other-provider on empty results — a single Gemini "[]"
            # for a dense table page used to silently drop the entire section.
            # max_output=60000 covers UFB-style budgets where the whole doc is
            # one giant markdown table (a 200-row table can produce ~30K JSON).
            data = await _call_llm_with_retry(
                prompt, max_output=60000, expected_key="line_items", label=name,
            )
            if not data:
                return []
            items = data.get("line_items") or []
            for item in items:
                item.setdefault("section", name)
            return items

        # Limit concurrency to avoid burning rate limits
        sem = asyncio.Semaphore(4)

        async def guarded(name: str, text: str) -> List[Dict]:
            async with sem:
                return await extract_section(name, text)

        section_results = await asyncio.gather(*[guarded(n, t) for n, t in sections])

        # Pass 3-4: persist + parse codes + classify fund
        order = 0
        all_items: List[Dict] = []
        for items in section_results:
            for raw in items:
                order += 1
                code_info = parse_account_code(raw.get("account_code") or raw.get("line_name") or "", raw)
                fund = code_info["fund"] or classify_fund_from_section(raw.get("section"), raw.get("subsection"))

                amount = _num(raw.get("amount"))
                prior = _num(raw.get("prior_year_amount"))
                budget = _num(raw.get("budget_amount"))

                yoy = None
                if amount is not None and prior is not None and prior != 0:
                    yoy = round(((amount - prior) / abs(prior)) * 100, 2)

                variance = None
                variance_pct = None
                if amount is not None and budget is not None:
                    variance = round(amount - budget, 2)
                    if budget != 0:
                        variance_pct = round((variance / abs(budget)) * 100, 2)

                line = FinancialLineItem(
                    statement_id=stmt.id,
                    section=raw.get("section"),
                    subsection=raw.get("subsection"),
                    line_name=(raw.get("line_name") or "")[:500] or "Unnamed",
                    amount=amount,
                    prior_year_amount=prior,
                    budget_amount=budget,
                    variance=variance,
                    variance_pct=variance_pct,
                    fund=fund,
                    account_code=code_info["account_code"],
                    program_code=code_info["program_code"],
                    function_code=code_info["function_code"],
                    object_code=code_info["object_code"],
                    is_total_row=bool(raw.get("is_total_row")),
                    line_order=raw.get("line_order") or order,
                    yoy_change_pct=yoy,
                )
                db.add(line)
                all_items.append({
                    "section": line.section, "amount": line.amount,
                    "is_total_row": line.is_total_row,
                })

        # Persist raw extraction blob too (useful for debugging + chat tool retrieval)
        stmt.raw_extraction = {
            "header": header or {},
            "section_count": len(sections),
            "line_item_count": order,
        }
        stmt.extraction_pass = 4
        stmt.extraction_model = "gemini-2.5-flash+claude-sonnet-4-6"
        db.commit()

        # Pass 5: reconcile
        reconcile_status, reconcile_details = reconcile_statement(stmt, db)
        stmt.reconcile_status = reconcile_status
        stmt.reconcile_details = reconcile_details

        # Anomaly detection
        flags = detect_anomalies_for_statement(stmt, db)
        stmt.anomaly_flags = flags

        # Yield check — fewer than 5 line items from a budget/audit/financial
        # statement usually means PDF→markdown produced near-empty output (e.g.
        # scanned PDF with no text layer) or section detection collapsed the
        # whole doc into one heading. Mark as `needs_reprocess` so the caller
        # can retry with a different segmentation strategy or OCR upgrade.
        stmt.status = "needs_reprocess" if order < 5 else "extracted"
        if stmt.status == "needs_reprocess":
            note = f"low_yield: only {order} line items extracted from {len(sections)} sections"
            stmt.notes = (stmt.notes + "; " if stmt.notes else "") + note
            logger.warning("statement %s flagged needs_reprocess: %s", statement_id, note)
        stmt.extraction_pass = 5
        db.commit()

        logger.info("extractor v2 done for %s: %d line items, reconcile=%s, %d anomalies",
                    statement_id, order, reconcile_status, len(flags))

    except Exception as exc:
        logger.exception("extractor v2 failed for %s", statement_id)
        try:
            stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
            if stmt:
                stmt.status = "error"
                stmt.notes = str(exc)[:500]
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _infer_accounting_basis(entity_type: Optional[str], statement_type: Optional[str]) -> str:
    """NJ schools use GAAP (GASB). NJ municipalities use NJ regulatory basis on
    AFS, with auditor's GAAP-departure paragraph on the audit. Few NJ towns
    issue full GAAP CAFRs."""
    if (entity_type or "").lower() == "school":
        return "gaap"
    return "nj_regulatory"


def _infer_fiscal_calendar(entity_type: Optional[str]) -> str:
    """Atlantic Highlands borough = calendar year (Jan-Dec).
    Henry Hudson Regional School District = school year (Jul-Jun)."""
    if (entity_type or "").lower() == "school":
        return "school_year"
    return "calendar_year"


def _num(v: Any) -> Optional[float]:
    """Coerce strings/ints/floats to float, handling $, commas, parentheses (negative)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s in ("-", "—", "n/a", "N/A"):
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()$, ").replace(",", "")
    try:
        n = float(s)
        return -n if negative else n
    except ValueError:
        return None
