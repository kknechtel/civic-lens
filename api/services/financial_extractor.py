"""
Financial statement extraction service.
Uses Gemini or Claude to extract structured data from financial documents.
"""
import logging
import json
import asyncio
from typing import Dict, List, Optional

from config import GEMINI_API_KEY, ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Extract ALL financial data from this government financial statement document.

<DOCUMENT>
{markdown}
</DOCUMENT>

This is a financial statement for a municipal government or school district. Extract the data into JSON format.

OUTPUT FORMAT (JSON):
{{
  "entity_name": "Full name of the government entity",
  "statement_type": "budget|audit|cafr|annual_report",
  "fiscal_year": "YYYY or YYYY-YYYY",
  "fiscal_year_start": "YYYY-MM-DD",
  "fiscal_year_end": "YYYY-MM-DD",
  "summary": {{
    "total_revenue": 0.00,
    "total_expenditures": 0.00,
    "surplus_deficit": 0.00,
    "fund_balance": 0.00,
    "total_debt": 0.00
  }},
  "line_items": [
    {{
      "section": "Revenue|Expenditures|Assets|Liabilities|Fund Balance",
      "subsection": "e.g. Property Taxes, Public Safety, Education",
      "line_name": "Specific line item name",
      "amount": 0.00,
      "prior_year_amount": null,
      "budget_amount": null,
      "line_order": 1
    }}
  ]
}}

RULES:
- Extract EVERY line item, do not skip any
- Use positive numbers for all amounts
- Section should be one of: Revenue, Expenditures, Assets, Liabilities, Fund Balance, Other
- Include prior year amounts and budget amounts when available
- For school districts, include categories like Instruction, Support Services, Administration
- For municipalities, include categories like Public Safety, Public Works, General Government
- Output ONLY the JSON object, no explanations"""


async def extract_financial_statement(statement_id: str, s3_key: str):
    """Background task to extract financial data from a document."""
    from database import SessionLocal
    from models.financial import FinancialStatement, FinancialLineItem
    from services.s3_service import S3Service

    db = SessionLocal()
    s3 = S3Service()

    try:
        stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
        if not stmt:
            logger.error(f"Statement {statement_id} not found")
            return

        # Download document from S3
        content = s3.download_file(s3_key)

        # Convert PDF to text
        markdown = await pdf_to_markdown(content)
        if not markdown:
            stmt.status = "error"
            stmt.notes = "Failed to extract text from document"
            db.commit()
            return

        # Extract financial data using AI
        prompt = EXTRACTION_PROMPT.format(markdown=markdown)
        extraction = await call_extraction_llm(prompt)

        if not extraction:
            stmt.status = "error"
            stmt.notes = "AI extraction failed"
            db.commit()
            return

        # Update statement with summary data
        summary = extraction.get("summary", {})
        stmt.entity_name = extraction.get("entity_name", stmt.entity_name)
        stmt.fiscal_year = extraction.get("fiscal_year", stmt.fiscal_year)
        stmt.total_revenue = summary.get("total_revenue")
        stmt.total_expenditures = summary.get("total_expenditures")
        stmt.surplus_deficit = summary.get("surplus_deficit")
        stmt.fund_balance = summary.get("fund_balance")
        stmt.total_debt = summary.get("total_debt")
        stmt.raw_extraction = extraction
        stmt.status = "extracted"

        # Create line items
        for i, item_data in enumerate(extraction.get("line_items", [])):
            item = FinancialLineItem(
                statement_id=stmt.id,
                section=item_data.get("section"),
                subsection=item_data.get("subsection"),
                line_name=item_data.get("line_name", f"Item {i+1}"),
                amount=item_data.get("amount"),
                prior_year_amount=item_data.get("prior_year_amount"),
                budget_amount=item_data.get("budget_amount"),
                line_order=item_data.get("line_order", i + 1),
            )
            db.add(item)

        db.commit()
        logger.info(
            f"Extracted {len(extraction.get('line_items', []))} line items "
            f"from statement {statement_id}"
        )

    except Exception as e:
        logger.error(f"Extraction failed for {statement_id}: {e}", exc_info=True)
        try:
            stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
            if stmt:
                stmt.status = "error"
                stmt.notes = str(e)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


async def pdf_to_markdown(content: bytes) -> Optional[str]:
    """Convert PDF bytes to markdown text using PyMuPDF."""
    try:
        import pymupdf
        import pymupdf4llm

        doc = pymupdf.open(stream=content, filetype="pdf")
        markdown = pymupdf4llm.to_markdown(doc)
        doc.close()
        return markdown
    except Exception as e:
        logger.error(f"PDF to markdown failed: {e}")
        return None


async def call_extraction_llm(prompt: str) -> Optional[Dict]:
    """Call Gemini or Claude to extract structured financial data."""
    # Try Gemini first
    if GEMINI_API_KEY:
        try:
            return await _call_gemini(prompt)
        except Exception as e:
            logger.warning(f"Gemini extraction failed, falling back to Claude: {e}")

    # Fallback to Claude
    if ANTHROPIC_API_KEY:
        try:
            return await _call_claude(prompt)
        except Exception as e:
            logger.error(f"Claude extraction also failed: {e}")

    return None


def _record_v1_usage(model: str, in_t: int, out_t: int) -> None:
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
        pass


async def _call_gemini(prompt: str) -> Optional[Dict]:
    """Extract using Gemini 2.5 Flash."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    config = types.GenerateContentConfig(
        temperature=0.1,
        max_output_tokens=65000,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=config,
        ),
    )

    if not response or not response.text:
        return None

    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        _record_v1_usage(
            "gemini-2.5-flash",
            int(getattr(usage, "prompt_token_count", 0) or 0),
            int(getattr(usage, "candidates_token_count", 0) or 0),
        )

    return _parse_json_response(response.text)


async def _call_claude(prompt: str) -> Optional[Dict]:
    """Extract using Claude."""
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        ),
    )

    if not response or not response.content:
        return None

    _record_v1_usage(
        "claude-sonnet-4-6",
        getattr(response.usage, "input_tokens", 0) or 0,
        getattr(response.usage, "output_tokens", 0) or 0,
    )

    return _parse_json_response(response.content[0].text)


def _parse_json_response(text: str) -> Optional[Dict]:
    """Parse JSON from an LLM response, handling code blocks."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0] if "\n" in text else text[:-3]
    text = text.strip()
    if text.startswith("json"):
        text = text[4:].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse extraction JSON: {e}")
        return None
