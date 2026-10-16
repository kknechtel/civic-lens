#!/usr/bin/env python3
"""
Targeted financial extraction - extracts key financial pages from audits/budgets.
Focuses on Exhibit A-1 (Statement of Operations) and Balance Sheet pages.
"""
import os, sys, json, time, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google import genai
from google.genai import types
from config import GEMINI_API_KEY
from services.s3_service import S3Service
from database import SessionLocal
from models.document import Document
from models.financial import FinancialStatement, FinancialLineItem
import pymupdf

client = genai.Client(api_key=GEMINI_API_KEY)
cfg = types.GenerateContentConfig(temperature=0.1, max_output_tokens=16000,
    thinking_config=types.ThinkingConfig(thinking_budget=0))
db = SessionLocal()
s3 = S3Service()

PROMPT = """You are extracting financial data from a New Jersey {entity_type} {doc_type} for fiscal year {fy}.
These are the KEY FINANCIAL PAGES from the document.

<FINANCIAL_PAGES>
{text}
</FINANCIAL_PAGES>

Extract ALL financial figures into this JSON structure. Use actual dollar amounts from the document.
For the {fy} year column only. Return null for missing data, never 0 unless document says 0.

{{
  "entity_name": "...",
  "fiscal_year": "{fy}",
  "current_fund": {{
    "total_revenue": null,
    "total_expenditures": null,
    "surplus_deficit": null,
    "fund_balance_beginning": null,
    "fund_balance_ending": null,
    "property_tax_revenue": null,
    "delinquent_tax_revenue": null,
    "state_aid": null,
    "miscellaneous_revenue": null,
    "non_budget_revenue": null,
    "salaries_wages": null,
    "other_expenses_within_caps": null,
    "operations_excluded_caps": null,
    "capital_improvements": null,
    "debt_service": null,
    "deferred_charges": null,
    "county_taxes": null,
    "school_taxes": null,
    "regional_school_tax": null,
    "open_space_tax": null
  }},
  "balance_sheet": {{
    "total_assets": null,
    "cash_treasurer": null,
    "tax_receivables": null,
    "tax_liens": null,
    "total_liabilities": null,
    "fund_balance": null,
    "reserve_for_encumbrances": null
  }},
  "water_sewer": {{
    "total_revenue": null,
    "total_expenditures": null,
    "fund_balance": null
  }},
  "harbor": {{
    "total_revenue": null,
    "total_expenditures": null,
    "fund_balance": null
  }},
  "debt": {{
    "total_general_debt": null,
    "total_water_sewer_debt": null,
    "total_all_debt": null,
    "annual_debt_service": null
  }},
  "fixed_assets_total": null,
  "key_line_items": [
    {{"section": "Revenue|Expenditures|Balance Sheet", "name": "...", "amount": 0, "prior_year": null, "budget": null}}
  ]
}}"""

SCHOOL_PROMPT = """You are extracting financial data from a New Jersey school district financial document for fiscal year {fy}.
This is the Atlantic Highlands School District (AHES).

<FINANCIAL_PAGES>
{text}
</FINANCIAL_PAGES>

Extract ALL financial figures. Use actual dollar amounts. Return null for missing, never 0 unless document says 0.

{{
  "entity_name": "Atlantic Highlands School District",
  "fiscal_year": "{fy}",
  "general_fund": {{
    "total_revenue": null,
    "total_expenditures": null,
    "surplus_deficit": null,
    "fund_balance_beginning": null,
    "fund_balance_ending": null,
    "property_tax_levy": null,
    "state_aid": null,
    "federal_aid": null,
    "tuition_revenue": null,
    "miscellaneous_revenue": null,
    "instruction_expenditures": null,
    "support_services": null,
    "administrative_expenditures": null,
    "operations_maintenance": null,
    "transportation": null,
    "debt_service": null,
    "capital_outlay": null
  }},
  "net_position": {{
    "total_assets": null,
    "total_liabilities": null,
    "net_position": null,
    "invested_in_capital_assets": null,
    "restricted": null,
    "unrestricted": null
  }},
  "debt": {{
    "total_outstanding": null,
    "bonds": null,
    "compensated_absences": null,
    "pension_liability": null
  }},
  "enrollment": null,
  "key_line_items": [
    {{"section": "Revenue|Expenditures|Balance Sheet", "name": "...", "amount": 0, "prior_year": null, "budget": null}}
  ]
}}"""


def find_financial_pages(pdf, doc_type):
    """Find the pages most likely to contain financial statement data."""
    financial_keywords = [
        'total revenue', 'total expenditure', 'fund balance',
        'statement of operations', 'balance sheet', 'comparative',
        'exhibit a', 'exhibit b', 'exhibit c', 'exhibit d',
        'statement of revenues', 'appropriation', 'surplus',
        'net position', 'statement of activities',
    ]

    scored_pages = []
    for i in range(pdf.page_count):
        text = pdf[i].get_text()
        if not text.strip():
            continue
        lower = text.lower()
        # Score based on financial keywords + number density
        score = 0
        for kw in financial_keywords:
            if kw in lower:
                score += 2
        amounts = re.findall(r'[\d,]{4,}\.\d{2}', text)
        score += len(amounts)
        if score > 3:
            scored_pages.append((i, score, text))

    # Sort by score, take top pages
    scored_pages.sort(key=lambda x: -x[1])
    return scored_pages[:25]


# Get all financial docs
docs = db.query(Document).filter(
    Document.doc_type.in_(["audit", "financial_statement", "budget"]),
    ~Document.filename.like("%RFP%"), ~Document.filename.like("%Synopsis%"),
    ~Document.filename.like("%Presentation%"), ~Document.filename.like("%INTRODUCED%"),
).order_by(Document.fiscal_year.desc()).all()

best = {}
prio = {"audit": 0, "financial_statement": 1, "budget": 2}
for d in docs:
    fy = (d.fiscal_year or "")[:4]
    if not fy or not fy.isdigit(): continue
    key = (d.category, fy)
    if key not in best or prio.get(d.doc_type, 9) < prio.get(best[key].doc_type, 9):
        best[key] = d

unique = sorted(best.values(), key=lambda d: (d.category, d.fiscal_year or ""), reverse=True)
print(f"Extracting {len(unique)} documents with targeted page selection...")

count = 0
errors = 0
for doc in unique:
    fy = (doc.fiscal_year or "")[:4]
    entity = "Borough of Atlantic Highlands" if doc.category == "town" else "Atlantic Highlands School District"
    print(f"\n{doc.category} FY{fy}: {doc.filename[:55]} [{doc.doc_type}]")

    try:
        content = s3.download_file(doc.s3_key)
        pdf = pymupdf.open(stream=content, filetype="pdf")

        # Find key financial pages
        key_pages = find_financial_pages(pdf, doc.doc_type)
        if not key_pages:
            # Fallback: take first 30 pages
            key_pages = [(i, 0, pdf[i].get_text()) for i in range(min(30, pdf.page_count)) if pdf[i].get_text().strip()]

        if not key_pages:
            print(f"  SKIP: no extractable text (scanned PDF)")
            pdf.close()
            continue

        combined = "\n\n".join(f"--- PAGE {p[0]+1} ---\n{p[2]}" for p in key_pages)
        pdf.close()

        # Choose prompt based on entity
        if doc.category == "school":
            prompt = SCHOOL_PROMPT.format(text=combined[:50000], fy=fy)
        else:
            prompt = PROMPT.format(text=combined[:50000], fy=fy, entity_type="municipal", doc_type=doc.doc_type)

        r = client.models.generate_content(model="gemini-2.5-flash", contents=prompt, config=cfg)
        if not r or not r.text:
            print("  EMPTY response")
            continue

        text = r.text.strip()
        if text.startswith("```"): text = text.split("\n", 1)[1]
        if text.endswith("```"): text = text.rsplit("\n", 1)[0]
        if text.startswith("json"): text = text[4:]

        result = json.loads(text.strip())

        # Extract key figures based on entity type
        if doc.category == "school":
            gf = result.get("general_fund", {})
            np_data = result.get("net_position", {})
            debt_data = result.get("debt", {})
            total_rev = gf.get("total_revenue")
            total_exp = gf.get("total_expenditures")
            surplus = gf.get("surplus_deficit")
            fund_bal = gf.get("fund_balance_ending")
            total_debt = debt_data.get("total_outstanding")
        else:
            cf = result.get("current_fund", {})
            bs = result.get("balance_sheet", {})
            debt_data = result.get("debt", {})
            total_rev = cf.get("total_revenue")
            total_exp = cf.get("total_expenditures")
            surplus = cf.get("surplus_deficit")
            fund_bal = cf.get("fund_balance_ending") or bs.get("fund_balance")
            total_debt = debt_data.get("total_all_debt") or debt_data.get("total_general_debt")

        # Validate - skip if total_revenue is a dict (multi-fund)
        if isinstance(total_rev, dict):
            total_rev = sum(v for v in total_rev.values() if isinstance(v, (int, float)))
        if isinstance(total_exp, dict):
            total_exp = sum(v for v in total_exp.values() if isinstance(v, (int, float)))
        if isinstance(fund_bal, dict):
            fund_bal = sum(v for v in fund_bal.values() if isinstance(v, (int, float)))

        # Update database
        existing = db.query(FinancialStatement).filter(
            FinancialStatement.entity_type == doc.category,
            FinancialStatement.fiscal_year == fy,
        ).first()

        if existing:
            stmt = existing
            db.query(FinancialLineItem).filter(FinancialLineItem.statement_id == stmt.id).delete()
        else:
            stmt = FinancialStatement(
                document_id=doc.id, entity_type=doc.category or "town",
                statement_type=doc.doc_type or "audit", fiscal_year=fy,
            )
            db.add(stmt)

        stmt.entity_name = result.get("entity_name") or entity
        stmt.total_revenue = total_rev if isinstance(total_rev, (int, float)) else None
        stmt.total_expenditures = total_exp if isinstance(total_exp, (int, float)) else None
        stmt.surplus_deficit = surplus if isinstance(surplus, (int, float)) else None
        stmt.fund_balance = fund_bal if isinstance(fund_bal, (int, float)) else None
        stmt.total_debt = total_debt if isinstance(total_debt, (int, float)) else None
        stmt.raw_extraction = result
        stmt.status = "extracted"
        db.flush()

        # Create line items from key_line_items
        order = 1
        for item in result.get("key_line_items", [])[:100]:
            if not item.get("name"):
                continue
            amt = item.get("amount")
            if isinstance(amt, dict):
                continue
            db.add(FinancialLineItem(
                statement_id=stmt.id,
                section=item.get("section", "Other"),
                line_name=item.get("name"),
                amount=amt if isinstance(amt, (int, float)) else None,
                prior_year_amount=item.get("prior_year") if isinstance(item.get("prior_year"), (int, float)) else None,
                budget_amount=item.get("budget") if isinstance(item.get("budget"), (int, float)) else None,
                line_order=order,
            ))
            order += 1

        db.commit()

        rv = f"${total_rev:,.0f}" if isinstance(total_rev, (int, float)) and total_rev else "-"
        ex = f"${total_exp:,.0f}" if isinstance(total_exp, (int, float)) and total_exp else "-"
        fb = f"${fund_bal:,.0f}" if isinstance(fund_bal, (int, float)) and fund_bal else "-"
        dt = f"${total_debt:,.0f}" if isinstance(total_debt, (int, float)) and total_debt else "-"
        print(f"  Rev={rv} Exp={ex} FB={fb} Debt={dt} ({order-1} items)")
        count += 1
        time.sleep(0.5)

    except json.JSONDecodeError as e:
        print(f"  JSON ERR: {e}")
        errors += 1
        db.rollback()
    except Exception as e:
        print(f"  ERR: {e}")
        errors += 1
        db.rollback()

db.close()
print(f"\nDONE! Extracted {count}/{len(unique)} statements, {errors} errors.")
