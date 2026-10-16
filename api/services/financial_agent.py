"""
Financial agent orchestrator: deep-dive drills on extracted statements.

Four parallel drill agents:
  - revenue_drill        — property tax composition, state aid, local revenue
  - expenditure_drill    — by function + by object
  - debt_drill           — outstanding debt, debt service ratio, statutory cap
  - fund_balance_drill   — multi-year trajectory, GASB54 / NJ regulatory

Then a synthesis agent ties them together. Schools (GAAP) and towns
(NJ regulatory basis) get different prompts.

Results land in FinancialStatement.drill_results JSONB:
  {
    "revenue":     {...findings, narrative, run_at, error?, error_trace?},
    "expenditure": {...},
    "debt":        {...},
    "fund_balance":{...},
    "synthesis":   {...},
    "_meta": {started_at, finished_at, duration_s, llm_model, success_count, error_count}
  }

Each drill catches its own exceptions and returns a structured error,
so a partial failure doesn't kill the others. The synthesis still runs
on whatever drills succeeded.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from config import ANTHROPIC_API_KEY, GEMINI_API_KEY

logger = logging.getLogger(__name__)


# ─── Drill prompts ───────────────────────────────────────────────────────────

REVENUE_PROMPT_SCHOOL = """You are an analyst examining REVENUE on a NJ school district financial statement
(GAAP/GASB basis under SFRA — School Funding Reform Act, P.L. 2007 c.260).

ENTITY: {entity_name}
FISCAL YEAR: {fiscal_year}  (NJ schools FY = July 1 to June 30)
TYPE: {statement_type}
TOTAL REVENUE: {total_revenue}

PRIOR YEAR REVENUE (context):
{prior_year_summary}

REVENUE LINE ITEMS:
{line_items_json}

NJ SCHOOL REVENUE STRUCTURE:
- Local Tax Levy (the main local source — board-set, capped at 2% by N.J.S.A. 18A:7F-37 with adjustment exclusions)
- State Aid (SFRA categorical aid):
    * Equalization Aid (largest, formula-based on local fair share)
    * Special Education Categorical Aid (per-pupil + census-based)
    * Security Aid
    * Transportation Aid
    * Preschool Education Aid (PEA) — only for districts with state-approved preschool
    * School Choice Aid — for Interdistrict Public School Choice districts
    * Extraordinary Special Education Aid — paid in arrears for individual SpEd students >$40K (lumpy)
    * Adjustment Aid — LARGELY PHASED OUT under S2 (2018); FY26 budget eliminated for most. Don't expect for HHRSD.
    * Stabilization / Hold-Harmless Aid (created FY25/FY26 to limit cuts to losing districts)
    * Educational Adequacy Aid (former Abbotts, narrow)
- Federal Aid: IDEA, Title I-IV, Perkins (in Special Revenue Fund 20, NOT counted as state aid)
- Other Local: Tuition received, interest, rental, miscellaneous

Produce JSON:
{{
  "composition": [
    {{"category": "Local Tax Levy|Equalization Aid|Special Ed Categorical|Security Aid|Transportation Aid|Preschool Ed Aid|Choice Aid|Extraordinary SpEd Aid|Stabilization Aid|Federal IDEA|Federal Title I|Tuition Received|Other",
      "amount": 0, "pct_of_total": 0, "yoy_change_pct": 0}}
  ],
  "tax_levy_within_2pct_cap": true,
  "tax_levy_cap_analysis": "narrative — was the 2% cap respected? any banked cap used?",
  "state_aid_total": 0,
  "state_aid_yoy_pct": 0,
  "key_findings": [{{"finding": "...", "evidence": "...", "concern_level": "info|warn|high"}}],
  "trends": "narrative paragraph",
  "questions": ["specific questions a board member or resident should ask"]
}}

Output ONLY valid JSON. No fences."""


REVENUE_PROMPT_MUNI = """You are an analyst examining REVENUE on a NJ MUNICIPAL financial statement (Borough of Atlantic Highlands).

CRITICAL: NJ municipalities use REGULATORY BASIS accounting (per N.J.A.C. 5:30, NOT GAAP). The Annual Financial Statement (AFS) is filed with DLGS each February 10 for calendar-year municipalities.

ENTITY: {entity_name}
FISCAL YEAR: {fiscal_year}  (NJ municipalities default to Calendar Year, Jan-Dec; AH is calendar year)
TYPE: {statement_type}
TOTAL REVENUE: {total_revenue}

PRIOR YEAR REVENUE (context):
{prior_year_summary}

REVENUE LINE ITEMS:
{line_items_json}

NJ MUNICIPAL CURRENT FUND REVENUE STRUCTURE:
- Local Property Tax (current year levy — this is the largest line for most NJ towns)
- "Surplus Anticipated" — fund balance USED as anticipated revenue (don't double-count as new revenue)
- State Aid:
    * Energy Tax Receipts (ETR) — historically split with CMPTRA but for FY26+ CMPTRA was FULLY CONSOLIDATED INTO ETR; expect $0 CMPTRA on FY26+ AFS without flagging it as a decline
    * Garden State Trust (small, dedicated)
    * Transitional Aid (only for distressed municipalities — NOT typical for AH)
    * Highlands Protection Fund Aid (NW NJ Highlands Council municipalities ONLY — NOT Atlantic Highlands; the name is coincidence)
- Local Revenue / Misc Anticipated:
    * Licenses, Fees, Permits, Construction Code, Beach/Harbor fees, Rental
    * Interest on Investments / Deposits
    * PILOT (Payment in Lieu of Taxes) from tax-exempt entities
    * Court fines, parking enforcement
- Dedicated Grants (small but recurring):
    * Recycling Tonnage Grant
    * Clean Communities Grant
    * Body Armor Replacement
    * Drunk Driving Enforcement Fund
    * NJDOT Local Aid / Municipal Aid (capital grants)
- Receivables / Reserve for Receivables (regulatory-basis quirk; not new revenue)

Produce JSON:
{{
  "composition": [
    {{"category": "Local Property Tax|Surplus Anticipated|ETR/CMPTRA|Local Permits & Fees|PILOT|Interest|Recycling Grant|Clean Communities|NJDOT Local Aid|Other",
      "amount": 0, "pct_of_total": 0, "yoy_change_pct": 0}}
  ],
  "etr_cmptra_status": "Note CMPTRA fully consolidated into ETR for FY26+; no separate CMPTRA expected.",
  "key_findings": [{{"finding": "...", "evidence": "...", "concern_level": "info|warn|high"}}],
  "trends": "narrative paragraph",
  "questions": ["specific questions"]
}}

Output ONLY valid JSON. No fences."""


EXPENDITURE_PROMPT = """You are a municipal finance analyst examining EXPENDITURES on a NJ {entity_type} financial statement.

ENTITY: {entity_name}
FISCAL YEAR: {fiscal_year}
TOTAL EXPENDITURES: {total_expenditures}

PRIOR YEAR (context if available):
{prior_year_summary}

EXPENDITURE LINE ITEMS:
{line_items_json}

Produce JSON:
{{
  "by_function": [
    {{"function": "Regular Programs - Instruction", "amount": 0, "pct_of_total": 0, "yoy_change_pct": 0}}
  ],
  "by_object": [
    {{"object": "Salaries", "amount": 0, "pct_of_total": 0}},
    {{"object": "Benefits", "amount": 0, "pct_of_total": 0}},
    {{"object": "Purchased Services", "amount": 0, "pct_of_total": 0}},
    {{"object": "Supplies", "amount": 0, "pct_of_total": 0}},
    {{"object": "Capital Outlay", "amount": 0, "pct_of_total": 0}}
  ],
  "salary_to_total_ratio": 0.0,
  "benefits_to_salary_ratio": 0.0,
  "key_findings": [{{"finding": "...", "evidence": "...", "concern_level": "info|warn|high"}}],
  "trends": "narrative paragraph",
  "questions": ["..."]
}}

NJ SCHOOL FUNCTION CODES (parse from `function_code` field if present):
  100=Regular Instruction, 200=Special Ed, 218=Speech, 230=Basic Skills,
  240=Bilingual, 401=Co-curricular, 402=Athletics,
  211=Tuition, 213=Health Services, 216=Speech/OT/PT,
  301=Guidance, 302=Child Study Team, 303=Improvement of Instruction,
  310=Educational Media/Library, 320=General Admin, 321=School Admin,
  330=Central Services, 331=Admin Info Tech, 332=Required Maint, 333=Other Plant,
  340=Care/Upkeep of Grounds, 350=Security, 360=Pupil Transportation

NJ OBJECT CODES:
  100/101=Salaries, 200/270=Benefits/FICA, 300=Purchased Professional Services,
  400=Repairs/Rentals, 500=Other Purchased Services, 600=Supplies/Materials,
  700=Capital Outlay/Equipment, 800=Other Objects (debt service)

Output ONLY valid JSON. No fences."""


DEBT_PROMPT = """Analyze DEBT and CAPITAL position of this NJ {entity_type} financial statement ({accounting_basis}).

ENTITY: {entity_name}
FY: {fiscal_year}
TOTAL DEBT: {total_debt}
FUND BALANCE: {fund_balance}
TOTAL REVENUE: {total_revenue}

DEBT-RELATED LINE ITEMS:
{line_items_json}

NJ STATUTORY DEBT CAPS (use these, not generic ratios):
- NJ MUNICIPALITIES: Net debt cannot exceed 3.5% of Equalized Valuation (3-year average) — N.J.S.A. 40A:2-6. Excess requires Local Finance Board approval.
- NJ SCHOOLS: Net debt cannot exceed 4% of Equalized Valuation (county-equalized) — N.J.S.A. 18A:24-19.
- ANNUAL DEBT STATEMENT: Towns file an ADS by Jan 31; SDS before each new bond ordinance.
- For schools, debt service is in Fund 40 (Debt Service Fund), separate from operating Fund 11 — do NOT mix them.

Produce JSON:
{{
  "outstanding_debt": 0,
  "annual_debt_service": 0,
  "debt_to_revenue_ratio": 0.0,
  "debt_to_eq_valuation_pct": null,
  "statutory_cap_pct": 3.5,
  "within_statutory_cap": "true|false|unknown",
  "debt_per_capita": 0,
  "capital_reserve": 0,
  "debt_components": [
    {{"name": "General Obligation Bonds|BANs|Refunding Bonds|Capital Lease|Loan", "amount": 0, "maturity": "YYYY"}}
  ],
  "deferred_charges_or_emergency_authorizations": 0,
  "key_findings": [{{"finding": "...", "evidence": "...", "concern_level": "info|warn|high"}}],
  "trends": "narrative",
  "questions": ["..."]
}}

For Atlantic Highlands borough use ~4318 population; for HHRSD report debt per pupil (use ~725 students).

Output ONLY valid JSON. No fences."""


FUND_BALANCE_PROMPT_SCHOOL = """Analyze FUND BALANCE on a NJ SCHOOL DISTRICT financial statement (GAAP/GASB 54).

ENTITY: {entity_name}
FY: {fiscal_year}
FUND BALANCE: {fund_balance}
TOTAL EXPENDITURES: {total_expenditures}

FUND BALANCE / EQUITY LINE ITEMS:
{line_items_json}

PRIOR YEAR FUND BALANCES:
{prior_fund_balances}

CRITICAL NJ RULES (do NOT use GFOA's 16% benchmark for schools):
- Unrestricted general fund balance is STATUTORILY CAPPED at the GREATER of 2% of general fund expenditures OR $250,000 (N.J.S.A. 18A:7F-7).
- For a small district like Henry Hudson Regional (~725 students, ~$15-20M budget), the $250K floor often binds, not the 2% percent.
- Excess can be moved (with board action / commissioner approval where required) to:
    * Capital Reserve (N.J.S.A. 18A:21-2; -3; -4 / N.J.A.C. 6A:23A-14.1)
    * Maintenance Reserve (N.J.S.A. 18A:7F-41 / N.J.A.C. 6A:23A-14.2)
    * Current Expense Emergency Reserve (capped at $250K or 1% of GF up to $1M)
    * Tuition Reserve (sending districts, 2-year rolling)
    * Impact Aid Reserve (federal, narrow)

Produce JSON:
{{
  "total_fund_balance": 0,
  "gasb54_components": {{
    "nonspendable": 0, "restricted": 0, "committed": 0, "assigned": 0, "unassigned": 0
  }},
  "reserves": {{
    "capital_reserve": 0, "maintenance_reserve": 0, "emergency_reserve": 0,
    "tuition_reserve": 0, "impact_aid_reserve": 0
  }},
  "unrestricted_fund_balance": 0,
  "statutory_cap": 0,
  "cap_status": "under_cap|at_cap|over_cap_with_reserves|over_cap_violation",
  "cap_analysis": "narrative — calc 2% of expenditures vs $250K floor; identify if excess moved to reserves",
  "trajectory": "growing|stable|declining|volatile",
  "appropriated_for_next_year": 0,
  "key_findings": [{{"finding": "...", "evidence": "...", "concern_level": "info|warn|high"}}],
  "narrative": "Plain-English assessment",
  "questions": ["..."]
}}

Output ONLY valid JSON. No fences."""


FUND_BALANCE_PROMPT_MUNI = """Analyze FUND BALANCE on a NJ MUNICIPAL financial statement (regulatory basis, NOT GAAP).

ENTITY: {entity_name}
FY: {fiscal_year}
CURRENT FUND BALANCE: {fund_balance}
TOTAL EXPENDITURES: {total_expenditures}

FUND BALANCE / RESERVE LINE ITEMS:
{line_items_json}

PRIOR YEAR FUND BALANCES:
{prior_fund_balances}

NJ MUNICIPAL CONTEXT:
- NJ towns are NOT on GASB 54. They report regulatory-basis "Fund Balance" in the Current Fund as a single number, with reservations (e.g., "Reserve for Receivables" — a regulatory-basis quirk that doesn't represent unspent cash).
- Key health metric: ratio of UNRESERVED Current Fund balance to expenditures. DLGS Best Practices guidance flags <5% as concerning; healthy NJ Current Fund typically 8-15%.
- "Surplus regenerated" = excess of revenue realized over expenditures + reserves canceled. This is the operational signal — NOT raw revenue minus expenditures, because towns intentionally anticipate fund balance as revenue.
- "Surplus anticipated as revenue" in next year's budget = how much fund balance is being USED. Pay attention to whether the town is depleting or building.
- Atlantic Highlands operates a Water/Sewer Utility — that has SEPARATE fund balance (Utility Operating + Utility Capital) which should be analyzed separately.

Produce JSON:
{{
  "total_current_fund_balance": 0,
  "reserved_components": [
    {{"name": "Reserve for Receivables|Reserve for Encumbrances|Other", "amount": 0}}
  ],
  "unreserved_fund_balance": 0,
  "fund_balance_to_expenditure_ratio": 0.0,
  "surplus_regenerated_current_year": 0,
  "surplus_anticipated_next_year": 0,
  "fund_balance_health": "strong|adequate|concerning|critical",
  "utility_fund_balance_separate": "if water/sewer fund balance is shown, summarize separately",
  "trajectory": "growing|stable|declining|volatile",
  "key_findings": [{{"finding": "...", "evidence": "...", "concern_level": "info|warn|high"}}],
  "narrative": "Plain-English assessment using NJ municipal benchmarks (warn <5%, target ≥8% of Current Fund expenditures)",
  "questions": ["..."]
}}

Output ONLY valid JSON. No fences."""


SYNTHESIS_PROMPT = """You are senior municipal finance analyst writing an executive summary for community members reviewing the financial health of {entity_name} for FY{fiscal_year}.

You have four drill reports below. Synthesize them.

REVENUE DRILL:
{revenue_drill}

EXPENDITURE DRILL:
{expenditure_drill}

DEBT DRILL:
{debt_drill}

FUND BALANCE DRILL:
{fund_balance_drill}

DETECTED RULE-BASED ANOMALIES:
{anomaly_flags}

RECONCILIATION STATUS:
{reconcile_status}

Produce JSON:
{{
  "headline": "One-line description of the entity's financial position",
  "executive_summary": "3-5 sentence plain-English summary",
  "strengths": ["...", "..."],
  "concerns": ["...", "..."],
  "red_flags": [
    {{"flag": "...", "evidence": "...", "severity": "info|warn|high"}}
  ],
  "questions_to_ask": [
    "Specific questions a board member or resident should ask the {entity_type} administration"
  ],
  "opra_followups": [
    "Specific records a citizen could request via OPRA to investigate further"
  ]
}}

Be specific. Cite numbers. Output ONLY valid JSON. No fences."""


# ─── Prompt sanity check at import time ──────────────────────────────────────
# Catches any unescaped `{...}` brace bug before the agent is ever invoked.

_PROMPT_FIELDS = {
    "REVENUE_PROMPT_SCHOOL": dict(entity_name="x", fiscal_year="x", statement_type="x",
                                  total_revenue="x", prior_year_summary="x", line_items_json="x"),
    "REVENUE_PROMPT_MUNI": dict(entity_name="x", fiscal_year="x", statement_type="x",
                                total_revenue="x", prior_year_summary="x", line_items_json="x"),
    "EXPENDITURE_PROMPT": dict(entity_type="x", entity_name="x", fiscal_year="x",
                               total_expenditures="x", prior_year_summary="x", line_items_json="x"),
    "DEBT_PROMPT": dict(entity_type="x", entity_name="x", accounting_basis="x", fiscal_year="x",
                        total_debt="x", fund_balance="x", total_revenue="x", line_items_json="x"),
    "FUND_BALANCE_PROMPT_SCHOOL": dict(entity_name="x", fiscal_year="x", fund_balance="x",
                                       total_expenditures="x", line_items_json="x", prior_fund_balances="x"),
    "FUND_BALANCE_PROMPT_MUNI": dict(entity_name="x", fiscal_year="x", fund_balance="x",
                                     total_expenditures="x", line_items_json="x", prior_fund_balances="x"),
    "SYNTHESIS_PROMPT": dict(entity_name="x", entity_type="x", fiscal_year="x",
                             revenue_drill="x", expenditure_drill="x", debt_drill="x",
                             fund_balance_drill="x", anomaly_flags="x", reconcile_status="x"),
}


def _validate_prompts():
    """Raise loudly at import time if any prompt has a brace bug."""
    import sys
    module = sys.modules[__name__]
    for name, kwargs in _PROMPT_FIELDS.items():
        template = getattr(module, name)
        try:
            template.format(**kwargs)
        except (KeyError, IndexError, ValueError) as exc:
            raise RuntimeError(
                f"Prompt template `{name}` failed format validation: {exc!r}. "
                f"Likely an unescaped `{{...}}` brace inside the JSON example. "
                f"Fix by doubling braces (e.g. `{{{{` for `{{`)."
            ) from exc


_validate_prompts()


# ─── LLM call ────────────────────────────────────────────────────────────────


async def _call_llm(prompt: str, max_output: int = 16000, label: str = "drill") -> Tuple[Optional[Dict], Optional[str]]:
    """
    Try Claude first, fall back to Gemini.

    Returns (parsed_json | None, error_message | None).
    The error message is the LAST failure encountered; it bubbles up so the
    drill can report exactly what went wrong instead of just "drill failed".
    """
    last_error: Optional[str] = None
    raw_response: Optional[str] = None

    def _record(model: str, in_t: int, out_t: int) -> None:
        if not (in_t or out_t):
            return
        try:
            from database import SessionLocal
            from services.usage import record_usage
            sess = SessionLocal()
            try:
                record_usage(
                    sess, source="financial_agent", model=model,
                    input_tokens=in_t, output_tokens=out_t,
                    metadata={"label": label},
                )
            finally:
                sess.close()
        except Exception:
            logger.debug("financial_agent usage record skipped", exc_info=True)

    if ANTHROPIC_API_KEY:
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
            # Anthropic SDK requires .stream() for ANY request with max_tokens that
            # could plausibly take >10 min. Use streaming unconditionally so we don't
            # need to guess the threshold.
            text_parts: list[str] = []
            in_t = out_t = 0
            async with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=max_output,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for text_chunk in stream.text_stream:
                    text_parts.append(text_chunk)
                try:
                    final = await stream.get_final_message()
                    in_t = getattr(final.usage, "input_tokens", 0) or 0
                    out_t = getattr(final.usage, "output_tokens", 0) or 0
                except Exception:
                    pass
            _record("claude-sonnet-4-6", in_t, out_t)
            raw_response = "".join(text_parts)
            if raw_response:
                parsed = _parse_json(raw_response)
                if parsed is not None:
                    return parsed, None
                last_error = f"claude_returned_unparseable_json (first 300 chars): {raw_response[:300]}"
                logger.warning("[%s] %s", label, last_error)
            else:
                last_error = "claude_returned_empty_response"
                logger.warning("[%s] %s", label, last_error)
        except Exception as exc:
            last_error = f"claude_api_error: {type(exc).__name__}: {exc}"
            logger.warning("[%s] %s", label, last_error)

    if GEMINI_API_KEY:
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=GEMINI_API_KEY)
            # ThinkingConfig.thinking_budget is rejected by older SDK versions on
            # the deployed EC2. Build the config without it and try setting it
            # only if the SDK accepts. (We don't actually need thinking for these
            # structured-extraction drills.)
            cfg_kwargs = dict(temperature=0.1, max_output_tokens=max_output)
            try:
                cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            except Exception:
                pass  # SDK doesn't accept thinking_budget — skip
            cfg = types.GenerateContentConfig(**cfg_kwargs)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.models.generate_content(model="gemini-2.5-flash", contents=prompt, config=cfg),
            )
            if response:
                usage = getattr(response, "usage_metadata", None)
                if usage is not None:
                    _record(
                        "gemini-2.5-flash",
                        int(getattr(usage, "prompt_token_count", 0) or 0),
                        int(getattr(usage, "candidates_token_count", 0) or 0),
                    )
            if response and response.text:
                raw_response = response.text
                parsed = _parse_json(raw_response)
                if parsed is not None:
                    return parsed, None
                last_error = f"gemini_returned_unparseable_json (first 300 chars): {raw_response[:300]}"
                logger.warning("[%s] %s", label, last_error)
            else:
                last_error = "gemini_returned_empty_response"
                logger.warning("[%s] %s", label, last_error)
        except Exception as exc:
            last_error = f"gemini_api_error: {type(exc).__name__}: {exc}"
            logger.warning("[%s] %s", label, last_error)

    if last_error is None:
        last_error = "no_llm_api_key_configured"

    return None, last_error


def _parse_json(text: str) -> Optional[Dict]:
    if not text:
        return None
    text = text.strip()
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
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                return None
        return None


# ─── Helpers to assemble inputs ──────────────────────────────────────────────


def _serialize_lines(items, max_items: int = 200) -> str:
    rows = []
    for it in items[:max_items]:
        rows.append({
            "section": it.section, "subsection": it.subsection,
            "line_name": it.line_name[:120] if it.line_name else "",
            "amount": it.amount, "prior_year_amount": it.prior_year_amount,
            "budget_amount": it.budget_amount, "variance_pct": it.variance_pct,
            "fund": it.fund, "function_code": it.function_code, "object_code": it.object_code,
            "yoy_change_pct": it.yoy_change_pct,
            "is_total_row": it.is_total_row,
        })
    return json.dumps(rows, default=str)


def _prior_year_summary(stmt, db: Session) -> str:
    from models.financial import FinancialStatement
    fy = stmt.fiscal_year
    if not fy:
        return "N/A"
    try:
        prior_fy = str(int(re.sub(r"[^0-9]", "", fy)[:4]) - 1)
    except Exception:
        return "N/A"
    prior = (
        db.query(FinancialStatement)
        .filter(
            FinancialStatement.entity_type == stmt.entity_type,
            FinancialStatement.statement_type == stmt.statement_type,
            FinancialStatement.fiscal_year.like(f"%{prior_fy}%"),
        )
        .first()
    )
    if not prior:
        return "N/A"
    return json.dumps({
        "fiscal_year": prior.fiscal_year,
        "total_revenue": prior.total_revenue,
        "total_expenditures": prior.total_expenditures,
        "fund_balance": prior.fund_balance,
        "total_debt": prior.total_debt,
    })


def _prior_fund_balances(stmt, db: Session, n: int = 5) -> str:
    from models.financial import FinancialStatement
    rows = (
        db.query(FinancialStatement)
        .filter(
            FinancialStatement.entity_type == stmt.entity_type,
            FinancialStatement.id != stmt.id,
            FinancialStatement.fund_balance.isnot(None),
        )
        .order_by(FinancialStatement.fiscal_year.desc())
        .limit(n)
        .all()
    )
    return json.dumps([{"fy": r.fiscal_year, "fund_balance": r.fund_balance} for r in rows])


def _is_school(stmt) -> bool:
    return (stmt.accounting_basis or "").lower() == "gaap" or (stmt.entity_type or "").lower() == "school"


# ─── Drill runners ───────────────────────────────────────────────────────────
#
# IMPORTANT: each runner takes `statement_id` (string) and opens its OWN DB
# session. Do NOT share a session across asyncio.gather coroutines —
# SQLAlchemy Session is not safe for concurrent use, even if the queries
# happen to be quick.


async def _run_in_session(label: str, statement_id: str, build_inputs, prompt_template, max_output: int = 16000) -> Dict:
    """Common drill runner: open session, build inputs, call LLM, return structured result.

    `build_inputs(stmt, db) -> dict` returns the kwargs for prompt_template.format().
    """
    from database import SessionLocal
    from models.financial import FinancialStatement

    started_at = time.monotonic()
    db = SessionLocal()
    try:
        stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
        if not stmt:
            return {"error": "statement_not_found", "label": label,
                    "run_at": datetime.utcnow().isoformat()}

        try:
            kwargs = build_inputs(stmt, db)
        except Exception as exc:
            tb = traceback.format_exc()
            logger.exception("[%s] build_inputs failed for %s", label, statement_id)
            return {"error": "build_inputs_failed", "error_message": str(exc),
                    "error_trace": tb[:2000], "run_at": datetime.utcnow().isoformat(),
                    "duration_s": round(time.monotonic() - started_at, 2)}

        try:
            prompt = prompt_template.format(**kwargs)
        except KeyError as exc:
            tb = traceback.format_exc()
            logger.exception("[%s] prompt format error for %s", label, statement_id)
            return {"error": "prompt_format_error", "missing_key": str(exc),
                    "error_trace": tb[:2000], "run_at": datetime.utcnow().isoformat()}

        result, llm_error = await _call_llm(prompt, max_output=max_output, label=label)
        duration_s = round(time.monotonic() - started_at, 2)

        if result is None:
            return {"error": "llm_call_failed", "error_message": llm_error,
                    "run_at": datetime.utcnow().isoformat(),
                    "duration_s": duration_s, "label": label}

        return {**result, "run_at": datetime.utcnow().isoformat(),
                "duration_s": duration_s, "label": label}

    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("[%s] unexpected error for %s", label, statement_id)
        return {"error": "unexpected_exception", "error_message": str(exc),
                "error_trace": tb[:2000], "run_at": datetime.utcnow().isoformat(),
                "duration_s": round(time.monotonic() - started_at, 2)}
    finally:
        db.close()


_REVENUE_SECTION_NAMES = (
    "Revenue", "Aid", "Revenues",
    "Anticipated Revenue", "Realized Revenue",
)
_REVENUE_KEYWORDS = (
    "revenue", "tax levy", " aid", "grant", "fee", "license", "permit",
    "fines", "interest income", "rental", "anticipated revenue",
    "fund balance utilized", "miscellaneous revenue",
)

_EXPENDITURE_SECTION_NAMES = (
    "Expenditures", "Personnel", "Capital",
    "Operations", "Salaries", "Benefits", "Operating Appropriations",
)
_EXPENDITURE_KEYWORDS = (
    "salar", "wages", "benefit", "expenditure", "appropriation",
    "operations", "instruction", "support service", "maintenance",
    "utilit", "supplies", "insurance",
)


def _query_lines_with_fallback(
    db, stmt_id, canonical_sections, keywords,
) -> list:
    """Look up line items first by canonical section names. If that returns
    empty (likely because the doc uses NJ-regulatory or fund-named sections),
    fall back to a keyword match on line_name. Last resort: return ALL line
    items for the statement so the LLM can do the classification itself.

    This is the "agents are smarter when detail is null" path: each layer of
    fallback is logged so we can see when sections didn't match upstream."""
    from models.financial import FinancialLineItem

    base = db.query(FinancialLineItem).filter(FinancialLineItem.statement_id == stmt_id)

    items = base.filter(FinancialLineItem.section.in_(canonical_sections)).all()
    if items:
        return items

    if keywords:
        kw_filter = None
        for kw in keywords:
            cond = FinancialLineItem.line_name.ilike(f"%{kw}%")
            kw_filter = cond if kw_filter is None else (kw_filter | cond)
        items = base.filter(kw_filter).all()
        if items:
            logger.info(
                "drill fallback: section match empty for stmt %s, keyword match returned %d items",
                stmt_id, len(items),
            )
            return items

    # Total fallback — give the LLM all line items and let it sort
    items = base.all()
    if items:
        logger.info(
            "drill fallback: returning ALL %d line items for stmt %s (section + keyword empty)",
            len(items), stmt_id,
        )
    return items


def _build_revenue_inputs(stmt, db):
    items = _query_lines_with_fallback(
        db, stmt.id, _REVENUE_SECTION_NAMES, _REVENUE_KEYWORDS,
    )
    return dict(
        entity_name=stmt.entity_name or "?",
        fiscal_year=stmt.fiscal_year, statement_type=stmt.statement_type,
        total_revenue=stmt.total_revenue,
        prior_year_summary=_prior_year_summary(stmt, db),
        line_items_json=_serialize_lines(items),
    )


def _build_expenditure_inputs(stmt, db):
    items = _query_lines_with_fallback(
        db, stmt.id, _EXPENDITURE_SECTION_NAMES, _EXPENDITURE_KEYWORDS,
    )
    return dict(
        entity_type=stmt.entity_type, entity_name=stmt.entity_name or "?",
        fiscal_year=stmt.fiscal_year,
        total_expenditures=stmt.total_expenditures,
        prior_year_summary=_prior_year_summary(stmt, db),
        line_items_json=_serialize_lines(items, max_items=300),
    )


def _build_debt_inputs(stmt, db):
    from models.financial import FinancialLineItem
    items = (db.query(FinancialLineItem)
             .filter(FinancialLineItem.statement_id == stmt.id)
             .filter((FinancialLineItem.section == "Debt Service") |
                     (FinancialLineItem.subsection.ilike("%debt%")) |
                     (FinancialLineItem.line_name.ilike("%debt%")) |
                     (FinancialLineItem.line_name.ilike("%bond%")))
             .all())
    return dict(
        entity_type=stmt.entity_type, entity_name=stmt.entity_name or "?",
        accounting_basis=stmt.accounting_basis or ("gaap" if _is_school(stmt) else "nj_regulatory"),
        fiscal_year=stmt.fiscal_year,
        total_debt=stmt.total_debt, fund_balance=stmt.fund_balance, total_revenue=stmt.total_revenue,
        line_items_json=_serialize_lines(items),
    )


def _build_fund_balance_inputs(stmt, db):
    from models.financial import FinancialLineItem
    items = (db.query(FinancialLineItem)
             .filter(FinancialLineItem.statement_id == stmt.id)
             .filter((FinancialLineItem.section == "Fund Balance") |
                     (FinancialLineItem.line_name.ilike("%fund balance%")) |
                     (FinancialLineItem.line_name.ilike("%reserve%")) |
                     (FinancialLineItem.line_name.ilike("%surplus%")))
             .all())
    return dict(
        entity_name=stmt.entity_name or "?",
        fiscal_year=stmt.fiscal_year,
        fund_balance=stmt.fund_balance, total_expenditures=stmt.total_expenditures,
        line_items_json=_serialize_lines(items),
        prior_fund_balances=_prior_fund_balances(stmt, db),
    )


async def run_revenue_drill_by_id(statement_id: str) -> Dict:
    from database import SessionLocal
    from models.financial import FinancialStatement
    db = SessionLocal()
    try:
        stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
        if not stmt:
            return {"error": "statement_not_found", "run_at": datetime.utcnow().isoformat()}
        template = REVENUE_PROMPT_SCHOOL if _is_school(stmt) else REVENUE_PROMPT_MUNI
    finally:
        db.close()
    return await _run_in_session("revenue", statement_id, _build_revenue_inputs, template)


async def run_expenditure_drill_by_id(statement_id: str) -> Dict:
    return await _run_in_session("expenditure", statement_id, _build_expenditure_inputs,
                                 EXPENDITURE_PROMPT, max_output=24000)


async def run_debt_drill_by_id(statement_id: str) -> Dict:
    return await _run_in_session("debt", statement_id, _build_debt_inputs, DEBT_PROMPT)


async def run_fund_balance_drill_by_id(statement_id: str) -> Dict:
    from database import SessionLocal
    from models.financial import FinancialStatement
    db = SessionLocal()
    try:
        stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
        if not stmt:
            return {"error": "statement_not_found", "run_at": datetime.utcnow().isoformat()}
        template = FUND_BALANCE_PROMPT_SCHOOL if _is_school(stmt) else FUND_BALANCE_PROMPT_MUNI
    finally:
        db.close()
    return await _run_in_session("fund_balance", statement_id, _build_fund_balance_inputs, template)


async def run_synthesis_by_id(statement_id: str, drills: Dict[str, Dict]) -> Dict:
    """Synthesis runs only on drills that succeeded (have no `error` key).
    If all four failed, synthesis still runs but the LLM gets the error JSON
    so it can report meta-failure honestly."""
    from database import SessionLocal
    from models.financial import FinancialStatement

    started_at = time.monotonic()
    db = SessionLocal()
    try:
        stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
        if not stmt:
            return {"error": "statement_not_found", "run_at": datetime.utcnow().isoformat()}

        prompt = SYNTHESIS_PROMPT.format(
            entity_name=stmt.entity_name or "?",
            entity_type=stmt.entity_type,
            fiscal_year=stmt.fiscal_year,
            revenue_drill=json.dumps(drills.get("revenue", {}))[:8000],
            expenditure_drill=json.dumps(drills.get("expenditure", {}))[:8000],
            debt_drill=json.dumps(drills.get("debt", {}))[:6000],
            fund_balance_drill=json.dumps(drills.get("fund_balance", {}))[:6000],
            anomaly_flags=json.dumps(stmt.anomaly_flags or [])[:4000],
            reconcile_status=stmt.reconcile_status or "unknown",
        )
    finally:
        db.close()

    result, err = await _call_llm(prompt, max_output=12000, label="synthesis")
    duration_s = round(time.monotonic() - started_at, 2)
    if result is None:
        return {"error": "llm_call_failed", "error_message": err,
                "run_at": datetime.utcnow().isoformat(),
                "duration_s": duration_s, "label": "synthesis"}
    return {**result, "run_at": datetime.utcnow().isoformat(),
            "duration_s": duration_s, "label": "synthesis"}


# ─── Public entry point ──────────────────────────────────────────────────────


async def run_full_drill(statement_id: str) -> Dict[str, Any]:
    """Run all four drill agents in parallel + synthesis. Persists to drill_results.

    Even if every drill fails, the function completes cleanly and writes the
    error structures into drill_results so the UI / caller can see exactly what
    happened. Status only flips to 'drilled' if synthesis succeeded.
    """
    from database import SessionLocal
    from models.financial import FinancialStatement

    started = time.monotonic()
    started_iso = datetime.utcnow().isoformat()
    logger.info("[run_full_drill] start statement_id=%s", statement_id)

    # Each drill opens its own session — safe under asyncio.gather
    revenue, expenditure, debt, fund_balance = await asyncio.gather(
        run_revenue_drill_by_id(statement_id),
        run_expenditure_drill_by_id(statement_id),
        run_debt_drill_by_id(statement_id),
        run_fund_balance_drill_by_id(statement_id),
        return_exceptions=False,
    )

    drills = {
        "revenue": revenue, "expenditure": expenditure,
        "debt": debt, "fund_balance": fund_balance,
    }

    success_count = sum(1 for d in drills.values() if "error" not in d)
    error_count = 4 - success_count
    logger.info("[run_full_drill] drills done success=%d error=%d statement_id=%s",
                success_count, error_count, statement_id)

    synthesis = await run_synthesis_by_id(statement_id, drills)
    drills["synthesis"] = synthesis

    drills["_meta"] = {
        "started_at": started_iso,
        "finished_at": datetime.utcnow().isoformat(),
        "duration_s": round(time.monotonic() - started, 2),
        "success_count": success_count,
        "error_count": error_count,
        "synthesis_ok": "error" not in synthesis,
        "llm_models_attempted": [m for m, k in [("claude-sonnet-4-6", ANTHROPIC_API_KEY),
                                                 ("gemini-2.5-flash", GEMINI_API_KEY)] if k],
    }

    # Persist regardless of success — caller needs to see errors.
    db = SessionLocal()
    try:
        stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
        if stmt:
            stmt.drill_results = drills
            # Only flip status to "drilled" if synthesis succeeded.
            if "error" not in synthesis:
                stmt.status = "drilled"
            db.commit()
            logger.info("[run_full_drill] persisted statement_id=%s status=%s",
                        statement_id, stmt.status)
    except Exception:
        logger.exception("[run_full_drill] persist failed for %s", statement_id)
    finally:
        db.close()

    return drills
