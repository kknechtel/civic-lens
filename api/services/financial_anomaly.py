"""
Rule-based anomaly detection.

CRITICAL: NJ municipalities use regulatory basis (DLGS); NJ schools use GAAP.
Their fund-balance rules and deficit signals are OPPOSITES, so this module
branches on FinancialStatement.accounting_basis (or entity_type as fallback).

Rules differ by entity type:

NJ SCHOOLS (gaap):
  - Fund balance is statutorily capped (N.J.S.A. 18A:7F-7) at the GREATER of
    2% of general fund expenditures or $250,000. Flag fund balance ABOVE this
    cap with no obvious reserves established (suggests cap violation).
  - Salary ratio for small districts (~700 students) typically 65-78%.
    info if >75%, warn if >82%.
  - YoY swings on Special Revenue (fund 20) lines are inherently lumpy
    (one-time grants, ARP-ESSER spend-down) — raise threshold to 50%.

NJ MUNICIPALITIES (nj_regulatory):
  - Current Fund routinely "anticipates" fund balance as revenue, so a raw
    operating-deficit ratio is meaningless. Use surplus regenerated.
  - Fund balance benchmark: warn <5% of Current Fund expenditures, target ≥8%.
  - Debt cap: 3.5% of equalized valuation per N.J.S.A. 40A:2-6 (we don't have
    eq val data yet, so we still flag debt-to-revenue >1.0 as a softer signal).

Each rule emits {code, severity, message, line_id?, value?}.
Severity: info | warn | high
"""
from __future__ import annotations

import logging
from typing import Dict, List

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# NJ school surplus floor: $250K or 2%, whichever greater (N.J.S.A. 18A:7F-7).
SCHOOL_FUND_BALANCE_CAP_PCT = 0.02
SCHOOL_FUND_BALANCE_FLOOR_DOLLARS = 250_000

# NJ municipal Current Fund benchmarks (DLGS Best Practices guidance).
MUNICIPAL_FUND_BALANCE_WARN = 0.05
MUNICIPAL_FUND_BALANCE_TARGET = 0.08


def detect_anomalies_for_statement(stmt, db: Session) -> List[Dict]:
    from models.financial import FinancialLineItem

    flags: List[Dict] = []
    basis = stmt.accounting_basis or ("gaap" if (stmt.entity_type or "").lower() == "school" else "nj_regulatory")

    rev = stmt.total_revenue
    exp = stmt.total_expenditures
    fb = stmt.fund_balance
    debt = stmt.total_debt

    # ── Fund balance rules — branch by accounting basis ──────────────────────
    if basis == "gaap":
        # Schools: flag EXCEEDING the 2%/$250K cap (suggests cap violation
        # absent a reserve transfer) — opposite of the GFOA framing.
        if fb is not None and exp and exp > 0:
            cap = max(SCHOOL_FUND_BALANCE_CAP_PCT * exp, SCHOOL_FUND_BALANCE_FLOOR_DOLLARS)
            if fb > cap:
                flags.append({
                    "code": "school_fund_balance_over_cap", "severity": "warn",
                    "message": (f"Unrestricted fund balance ${fb:,.0f} exceeds NJ surplus cap "
                                f"(greater of 2% of expenditures or $250K = ${cap:,.0f}). "
                                f"Verify excess was moved to a reserve (capital, maintenance, emergency)."),
                    "value": float(fb),
                })
        # GFOA 16% rule explicitly NOT applied to schools.
    else:
        # NJ Municipal: warn under 5%; target ≥8%.
        if fb is not None and exp and exp > 0:
            fbr = fb / exp
            if fbr < MUNICIPAL_FUND_BALANCE_WARN:
                flags.append({
                    "code": "municipal_fund_balance_low", "severity": "warn",
                    "message": (f"Current Fund balance is {fbr * 100:.1f}% of expenditures "
                                f"(target ≥{int(MUNICIPAL_FUND_BALANCE_TARGET * 100)}%, warn <{int(MUNICIPAL_FUND_BALANCE_WARN * 100)}%)."),
                    "value": round(fbr, 4),
                })
        # Negative fund balance is always a hard flag, regardless of basis
        if fb is not None and fb < 0:
            flags.append({
                "code": "negative_fund_balance", "severity": "high",
                "message": f"Fund balance is negative: ${fb:,.0f}",
                "value": fb,
            })

    # ── Operating ratio: only meaningful for GAAP (schools) ──────────────────
    if basis == "gaap" and rev and exp and rev > 0:
        ratio = exp / rev
        if ratio > 1.05:
            flags.append({"code": "operating_ratio_high", "severity": "high",
                          "message": f"Expenditures exceed revenue by {(ratio - 1) * 100:.1f}%",
                          "value": round(ratio, 4)})
        elif ratio > 1.0:
            flags.append({"code": "operating_deficit", "severity": "warn",
                          "message": f"Operating deficit ({(ratio - 1) * 100:.1f}% of revenue)",
                          "value": round(ratio, 4)})
    # NB: For nj_regulatory, deficit signal needs surplus-regenerated calc — TODO when we
    # have current-year-operations line. We do NOT raw-compare exp vs rev for towns.

    # ── Debt to revenue (soft signal; statutory cap is debt vs equalized valuation) ──
    if debt is not None and rev and rev > 0:
        dtr = debt / rev
        if dtr > 1.0:
            flags.append({"code": "debt_to_revenue_high", "severity": "warn",
                          "message": (f"Debt is {dtr * 100:.0f}% of annual revenue. "
                                      f"NJ statutory cap is 3.5% of equalized valuation (boroughs) / "
                                      f"4% (schools) — verify against equalized valuation, not just revenue."),
                          "value": round(dtr, 4)})

    # ── Per-line checks ──────────────────────────────────────────────────────
    items = (
        db.query(FinancialLineItem)
        .filter(FinancialLineItem.statement_id == stmt.id)
        .all()
    )

    # YoY swings: 25% threshold for general fund, 50% for Special Revenue (fund 20)
    for it in items:
        if it.is_total_row or it.yoy_change_pct is None or it.amount is None:
            continue
        if abs(it.amount) < 25_000:
            continue
        threshold = 50.0 if it.fund == "special_revenue" else 25.0
        if abs(it.yoy_change_pct) >= threshold:
            flags.append({
                "code": "yoy_swing_large", "severity": "warn",
                "message": f"{it.line_name}: {it.yoy_change_pct:+.1f}% YoY (${it.amount:,.0f}, fund={it.fund or '?'})",
                "line_id": str(it.id), "value": it.yoy_change_pct,
            })
            it.anomaly_flags = list(it.anomaly_flags or []) + [{
                "code": "yoy_swing_large", "severity": "warn",
                "message": f"YoY change {it.yoy_change_pct:+.1f}%",
            }]

    # Budget variance >10% (schools); for municipalities, encumbrances/cancellations
    # mean raw variance is misleading — only flag for GAAP.
    if basis == "gaap":
        for it in items:
            if it.is_total_row or it.variance_pct is None or it.amount is None:
                continue
            if abs(it.amount) < 25_000:
                continue
            if abs(it.variance_pct) >= 10:
                sign = "over" if it.variance_pct > 0 else "under"
                flags.append({
                    "code": "budget_variance_large", "severity": "warn",
                    "message": (f"{it.line_name}: {sign} budget by {abs(it.variance_pct):.1f}% "
                                f"(${it.amount:,.0f} vs ${it.budget_amount or 0:,.0f})"),
                    "line_id": str(it.id), "value": it.variance_pct,
                })
                it.anomaly_flags = list(it.anomaly_flags or []) + [{
                    "code": "budget_variance_large", "severity": "warn",
                    "message": f"{sign} budget by {abs(it.variance_pct):.1f}%",
                }]

    # Salary concentration — only schools, with NJ small-district-tuned thresholds
    if basis == "gaap":
        salary_total = sum(
            (it.amount or 0) for it in items
            if not it.is_total_row and it.object_code in ("100", "101", "110") and it.amount is not None
        )
        if salary_total and exp and exp > 0:
            sal_pct = salary_total / exp
            if sal_pct > 0.82:
                flags.append({
                    "code": "salary_concentration_high", "severity": "warn",
                    "message": (f"Salaries are {sal_pct * 100:.1f}% of expenditures — high even for "
                                f"a small NJ district (typical small-district range 65-78%)."),
                    "value": round(sal_pct, 4),
                })
            elif sal_pct > 0.75:
                flags.append({
                    "code": "salary_concentration_elevated", "severity": "info",
                    "message": f"Salaries are {sal_pct * 100:.1f}% of expenditures (typical small-NJ range 65-78%).",
                    "value": round(sal_pct, 4),
                })

    return flags
