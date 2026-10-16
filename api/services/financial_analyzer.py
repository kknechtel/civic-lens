"""
Financial analysis service.
Compares financial statements over time, calculates ratios, and generates summaries.
"""
import logging
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger(__name__)


async def run_analysis(
    analysis_type: str,
    statements: list,
) -> Tuple[Dict, Optional[str]]:
    """
    Run a financial analysis across multiple statements.

    Returns:
        Tuple of (results_dict, ai_summary_text)
    """
    if analysis_type == "trend":
        return _trend_analysis(statements)
    elif analysis_type == "comparison":
        return _comparison_analysis(statements)
    elif analysis_type == "ratio":
        return _ratio_analysis(statements)
    elif analysis_type == "variance":
        return _variance_analysis(statements)
    else:
        return {"error": f"Unknown analysis type: {analysis_type}"}, None


def _trend_analysis(statements: list) -> Tuple[Dict, Optional[str]]:
    """Analyze trends across fiscal years."""
    sorted_stmts = sorted(statements, key=lambda s: s.fiscal_year)

    years = []
    revenue = []
    expenditures = []
    surplus = []
    fund_balance = []

    for s in sorted_stmts:
        years.append(s.fiscal_year)
        revenue.append(s.total_revenue)
        expenditures.append(s.total_expenditures)
        surplus.append(s.surplus_deficit)
        fund_balance.append(s.fund_balance)

    # Calculate year-over-year changes
    yoy_revenue = _calc_yoy_changes(revenue)
    yoy_expenditures = _calc_yoy_changes(expenditures)

    results = {
        "years": years,
        "revenue": revenue,
        "expenditures": expenditures,
        "surplus_deficit": surplus,
        "fund_balance": fund_balance,
        "yoy_revenue_change": yoy_revenue,
        "yoy_expenditure_change": yoy_expenditures,
    }

    summary = _generate_trend_summary(results)
    return results, summary


def _comparison_analysis(statements: list) -> Tuple[Dict, Optional[str]]:
    """Side-by-side comparison of two or more statements."""
    comparisons = []
    for s in statements:
        comparisons.append({
            "fiscal_year": s.fiscal_year,
            "entity_name": s.entity_name,
            "total_revenue": s.total_revenue,
            "total_expenditures": s.total_expenditures,
            "surplus_deficit": s.surplus_deficit,
            "fund_balance": s.fund_balance,
            "total_debt": s.total_debt,
        })

    return {"comparisons": comparisons}, None


def _ratio_analysis(statements: list) -> Tuple[Dict, Optional[str]]:
    """Calculate financial health ratios."""
    ratios = []
    for s in statements:
        r = {"fiscal_year": s.fiscal_year, "entity_name": s.entity_name}

        if s.total_revenue and s.total_expenditures:
            r["operating_ratio"] = round(s.total_expenditures / s.total_revenue, 4)

        if s.fund_balance and s.total_expenditures:
            r["fund_balance_ratio"] = round(s.fund_balance / s.total_expenditures, 4)

        if s.total_debt and s.total_revenue:
            r["debt_to_revenue"] = round(s.total_debt / s.total_revenue, 4)

        ratios.append(r)

    return {"ratios": ratios}, None


def _variance_analysis(statements: list) -> Tuple[Dict, Optional[str]]:
    """Budget vs. actual variance analysis."""
    # This works best with line item data
    variances = []
    for s in statements:
        if s.line_items:
            for item in s.line_items:
                if item.budget_amount and item.amount:
                    variance = item.amount - item.budget_amount
                    variance_pct = (variance / item.budget_amount * 100) if item.budget_amount != 0 else 0
                    variances.append({
                        "fiscal_year": s.fiscal_year,
                        "section": item.section,
                        "line_name": item.line_name,
                        "budget": item.budget_amount,
                        "actual": item.amount,
                        "variance": round(variance, 2),
                        "variance_pct": round(variance_pct, 2),
                    })

    # Sort by absolute variance descending
    variances.sort(key=lambda v: abs(v.get("variance", 0)), reverse=True)

    return {"variances": variances}, None


def _calc_yoy_changes(values: List[Optional[float]]) -> List[Optional[float]]:
    """Calculate year-over-year percentage changes."""
    changes = [None]  # First year has no prior
    for i in range(1, len(values)):
        if values[i] is not None and values[i - 1] is not None and values[i - 1] != 0:
            change = ((values[i] - values[i - 1]) / abs(values[i - 1])) * 100
            changes.append(round(change, 2))
        else:
            changes.append(None)
    return changes


def _generate_trend_summary(results: Dict) -> str:
    """Generate a plain-text summary of trend analysis."""
    years = results.get("years", [])
    if len(years) < 2:
        return "Insufficient data for trend analysis."

    lines = [f"Financial Trend Analysis: {years[0]} to {years[-1]}"]

    revenue = results.get("revenue", [])
    if revenue[0] and revenue[-1]:
        total_change = ((revenue[-1] - revenue[0]) / abs(revenue[0])) * 100
        lines.append(f"Revenue changed {total_change:+.1f}% over the period.")

    expenditures = results.get("expenditures", [])
    if expenditures[0] and expenditures[-1]:
        total_change = ((expenditures[-1] - expenditures[0]) / abs(expenditures[0])) * 100
        lines.append(f"Expenditures changed {total_change:+.1f}% over the period.")

    fund_balance = results.get("fund_balance", [])
    if fund_balance[-1]:
        lines.append(f"Current fund balance: ${fund_balance[-1]:,.2f}")

    return "\n".join(lines)
