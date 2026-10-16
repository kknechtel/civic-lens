#!/usr/bin/env python3
"""Batch extract financial data from all audit/budget/FS documents.

For NJ school district ACFRs (AHSD/0130, HHRS/2120, HSD/2160), prefer
scripts/extract_school_acfrs.py — this generic script handles the town
case and any single-entity school case, but its entity_name inference is
weaker and its priority dedup is less surgical.
"""
import os, sys, json, time
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
cfg = types.GenerateContentConfig(temperature=0.1, max_output_tokens=65000,
    thinking_config=types.ThinkingConfig(thinking_budget=0))
db = SessionLocal()
s3 = S3Service()

PROMPT = """You are a municipal finance expert. Extract ALL financial data from this NJ government document.
This is a {doc_type} for {entity}.

<DOCUMENT>
{markdown}
</DOCUMENT>

Return JSON ONLY:
{{"entity_name":"...","fiscal_year":"YYYY",
"income_statement":{{"total_revenue":null,"property_tax_revenue":null,"state_aid":null,"federal_aid":null,
"miscellaneous_revenue":null,"other_revenue":null,"total_expenditures":null,"salaries_wages":null,
"other_expenses":null,"capital_improvements":null,"debt_service":null,"county_taxes":null,
"school_taxes":null,"open_space_tax":null,"surplus_deficit":null,
"revenue_line_items":[{{"name":"...","amount":0,"prior_year":null}}],
"expenditure_line_items":[{{"name":"...","amount":0,"prior_year":null,"budget":null}}]}},
"balance_sheet":{{"total_assets":null,"cash_and_investments":null,"receivables":null,
"total_liabilities":null,"accounts_payable":null,"bonds_payable":null,
"total_fund_balance":null,"unreserved_fund_balance":null,"reserved_fund_balance":null}},
"debt":{{"total_outstanding_debt":null,"bonds_outstanding":null,"notes_outstanding":null,"annual_debt_service":null}},
"tax_info":{{"tax_rate_per_100":null,"total_tax_levy":null,"tax_collection_rate":null,"assessed_valuation":null}},
"budget_comparison":{{"budgeted_revenue":null,"actual_revenue":null,"budgeted_expenditures":null,"actual_expenditures":null,"variance":null}}}}

CRITICAL: Extract REAL dollar amounts from the document. Do NOT return 0 unless document says 0."""

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
    # Include entity_name in the dedup key so multiple entities sharing
    # (category, fy) — e.g. AHSD + HHRS + HSD all tagged category='school'
    # for the same fiscal year — each get their own row instead of
    # collapsing to one.
    entity_key = (d.metadata_ or {}).get("entity_name")
    key = (d.category, entity_key, fy)
    if key not in best or prio.get(d.doc_type, 9) < prio.get(best[key].doc_type, 9):
        best[key] = d

unique = sorted(best.values(), key=lambda d: (d.category, d.fiscal_year or ""), reverse=True)
print(f"Extracting {len(unique)} documents...")

count = 0
for doc in unique:
    fy = (doc.fiscal_year or "")[:4]
    entity = (doc.metadata_ or {}).get("entity_name") or (
        "Borough of Atlantic Highlands" if doc.category == "town"
        else "Atlantic Highlands School District"
    )
    print(f"\n{doc.category} FY{fy}: {doc.filename[:55]} [{doc.doc_type}]")

    try:
        content = s3.download_file(doc.s3_key)
        pdf = pymupdf.open(stream=content, filetype="pdf")
        pages = [pdf[i].get_text() for i in range(pdf.page_count) if pdf[i].get_text().strip()]
        pdf.close()
        md = "\n\n---PAGE---\n\n".join(pages)
        if len(md) < 200: print("  SKIP: no text"); continue

        prompt = PROMPT.format(markdown=md[:60000], doc_type=doc.doc_type, entity=entity)
        r = client.models.generate_content(model="gemini-2.5-flash", contents=prompt, config=cfg)
        if not r or not r.text: print("  EMPTY"); continue

        text = r.text.strip()
        if text.startswith("```"): text = text.split("\n", 1)[1]
        if text.endswith("```"): text = text.rsplit("\n", 1)[0]
        if text.startswith("json"): text = text[4:]
        result = json.loads(text.strip())

        inc = result.get("income_statement", {})
        bs = result.get("balance_sheet", {})
        debt = result.get("debt", {})

        # Key by document_id so each source doc gets its own FinancialStatement
        # row. Filtering by (entity_type, fy) used to collapse multiple entities
        # in the same (category, fy) — see the dedup-key comment above.
        existing = db.query(FinancialStatement).filter(
            FinancialStatement.document_id == doc.id
        ).first()
        if existing:
            stmt = existing
            db.query(FinancialLineItem).filter(FinancialLineItem.statement_id == stmt.id).delete()
        else:
            stmt = FinancialStatement(document_id=doc.id, entity_type=doc.category or "town",
                                      statement_type=doc.doc_type or "audit", fiscal_year=fy)
            db.add(stmt)

        stmt.entity_name = result.get("entity_name") or entity
        stmt.total_revenue = inc.get("total_revenue")
        stmt.total_expenditures = inc.get("total_expenditures")
        stmt.surplus_deficit = inc.get("surplus_deficit")
        stmt.fund_balance = bs.get("total_fund_balance")
        stmt.total_debt = debt.get("total_outstanding_debt")
        stmt.raw_extraction = result
        stmt.status = "extracted"
        db.flush()

        order = 1
        for item in inc.get("revenue_line_items", [])[:50]:
            db.add(FinancialLineItem(statement_id=stmt.id, section="Revenue",
                line_name=item.get("name", f"Rev {order}"), amount=item.get("amount"),
                prior_year_amount=item.get("prior_year"), budget_amount=item.get("budget"), line_order=order))
            order += 1
        for item in inc.get("expenditure_line_items", [])[:50]:
            db.add(FinancialLineItem(statement_id=stmt.id, section="Expenditures",
                line_name=item.get("name", f"Exp {order}"), amount=item.get("amount"),
                prior_year_amount=item.get("prior_year"), budget_amount=item.get("budget"), line_order=order))
            order += 1
        for key, label in [("cash_and_investments", "Cash & Investments"), ("receivables", "Receivables"),
                           ("total_assets", "Total Assets"), ("total_liabilities", "Total Liabilities"),
                           ("total_fund_balance", "Total Fund Balance"), ("unreserved_fund_balance", "Unreserved Fund Balance")]:
            val = bs.get(key)
            if val:
                db.add(FinancialLineItem(statement_id=stmt.id, section="Balance Sheet",
                    line_name=label, amount=val, line_order=order))
                order += 1

        db.commit()
        rev = inc.get("total_revenue")
        exp = inc.get("total_expenditures")
        fb = bs.get("total_fund_balance")
        td = debt.get("total_outstanding_debt")
        print(f"  Rev={f'${rev:,.0f}' if rev else '-'} Exp={f'${exp:,.0f}' if exp else '-'} "
              f"FB={f'${fb:,.0f}' if fb else '-'} Debt={f'${td:,.0f}' if td else '-'} ({order-1} items)")
        count += 1
        time.sleep(0.5)
    except json.JSONDecodeError as e:
        print(f"  JSON ERR: {e}"); db.rollback()
    except Exception as e:
        print(f"  ERR: {e}"); db.rollback()

db.close()
print(f"\nDONE! Extracted {count}/{len(unique)} statements.")
