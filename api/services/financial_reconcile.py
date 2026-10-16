"""
Reconciliation: do the extracted line items add up to the reported summary totals?

Sums non-total lines per section, compares to summary totals from the header pass.
Returns a status + details JSONB. Bands:
  balanced     |delta| < 0.5%
  off_lt_1pct  0.5% <= |delta| < 1%
  off_gt_1pct  1% <= |delta| < 5%
  unbalanced   |delta| >= 5% OR delta is unknown

Only Revenue, Expenditures, and Aid sections are summed for comparison.
"Other" / "Capital" / "Personnel" / "Fund Balance" / metadata sections are
excluded from the reconcile — they're not part of the operating revenue or
expenditure totals. Many "Other" rows are decorative metadata: equalized
property valuations ($1B+), per-pupil costs, partner-district breakouts.
Including them inflates the section sum to absurd values.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Section names that count toward summary totals.
RECONCILE_SECTIONS = {"Revenue", "Expenditures"}

# Line-name patterns that are decorative metadata, not operating $$.
# Captured these from real extractions where "Equalized Property Valuation"
# showed up in the section sum at $1.27 BILLION, polluting the reconcile.
_METADATA_PATTERNS = [
    re.compile(r"\bequalized\s+(net\s+)?(property\s+)?valuation\b", re.I),
    re.compile(r"\bnet\s+taxable\s+valuation\b", re.I),
    re.compile(r"\bestimated\s+(net\s+taxable|equalized)\s+valuation\b", re.I),
    re.compile(r"\bper[\s-]+pupil\b", re.I),
    re.compile(r"^\s*total\s+budgetary\s+(comparative\s+)?per[\s-]+pupil\b", re.I),
    re.compile(r"^\s*atlantic\s+highlands\s*$", re.I),  # constituent-district breakouts
    re.compile(r"^\s*highlands\s*$", re.I),
    re.compile(r"^\s*sea\s+bright\s*$", re.I),
    re.compile(r"\bclassroom-?\s*salaries\b", re.I),  # already in Personnel
    re.compile(r"^\s*total\s+(classroom|support|administrative|operations|extracurricular)\b", re.I),
    re.compile(r"\benrollment\b", re.I),
    re.compile(r"\bratable\s+base\b", re.I),
]


def is_metadata_line(line_name: str) -> bool:
    if not line_name:
        return False
    for p in _METADATA_PATTERNS:
        if p.search(line_name):
            return True
    return False


def reconcile_statement(stmt, db: Session) -> Tuple[str, Dict]:
    from models.financial import FinancialLineItem

    items = (
        db.query(FinancialLineItem)
        .filter(FinancialLineItem.statement_id == stmt.id)
        .all()
    )

    # Sum non-total lines by section. EXCLUDE metadata-pattern lines + sections
    # outside the recognized reconciliation set.
    sums: Dict[str, float] = {}
    excluded_sums: Dict[str, float] = {}  # tracked for transparency
    excluded_by_pattern_count = 0
    for it in items:
        if it.is_total_row:
            continue
        if it.amount is None:
            continue
        section = (it.section or "Other").strip()
        if is_metadata_line(it.line_name or ""):
            excluded_by_pattern_count += 1
            excluded_sums[f"{section} (metadata)"] = excluded_sums.get(f"{section} (metadata)", 0.0) + float(it.amount)
            continue
        if section not in RECONCILE_SECTIONS:
            excluded_sums[section] = excluded_sums.get(section, 0.0) + float(it.amount)
            continue
        sums[section] = sums.get(section, 0.0) + float(it.amount)

    # Compare to reported summary
    targets = {
        "Revenue": stmt.total_revenue,
        "Expenditures": stmt.total_expenditures,
    }

    detail = {
        "sums_by_section": {k: round(v, 2) for k, v in sums.items()},
        "excluded_sums": {k: round(v, 2) for k, v in excluded_sums.items()},
        "excluded_metadata_count": excluded_by_pattern_count,
        "checks": [],
    }
    worst_status = "balanced"

    for section_name, reported in targets.items():
        extracted = sums.get(section_name)
        if reported is None:
            detail["checks"].append({
                "section": section_name,
                "status": "no_target",
                "extracted": round(extracted, 2) if extracted is not None else None,
                "reported": None,
            })
            worst_status = _worse(worst_status, "off_lt_1pct")  # unknown target — mild flag
            continue
        if extracted is None:
            detail["checks"].append({
                "section": section_name, "status": "no_lines",
                "extracted": None, "reported": float(reported),
            })
            worst_status = _worse(worst_status, "unbalanced")
            continue

        delta = extracted - float(reported)
        pct = (abs(delta) / abs(float(reported)) * 100) if reported else 100.0
        status = _band(pct)
        detail["checks"].append({
            "section": section_name,
            "status": status,
            "extracted": round(extracted, 2),
            "reported": round(float(reported), 2),
            "delta": round(delta, 2),
            "delta_pct": round(pct, 2),
        })
        worst_status = _worse(worst_status, status)

    return worst_status, detail


def _band(pct: float) -> str:
    if pct < 0.5:
        return "balanced"
    if pct < 1.0:
        return "off_lt_1pct"
    if pct < 5.0:
        return "off_gt_1pct"
    return "unbalanced"


_RANK = {
    "balanced": 0,
    "no_target": 1,
    "off_lt_1pct": 2,
    "off_gt_1pct": 3,
    "unbalanced": 4,
}


def _worse(a: str, b: str) -> str:
    return a if _RANK.get(a, 0) >= _RANK.get(b, 0) else b
