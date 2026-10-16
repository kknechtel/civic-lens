"""Report generation - AI-generated financial and document analysis reports."""
import logging
import json
import asyncio
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models.document import Document
from models.financial import FinancialStatement
from models.user import User
from auth import get_current_user
from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)
router = APIRouter()


def _entity_name(entity_type: str | None) -> str:
    """Full name of the town ("town") or its first school district ("school")."""
    from municipality import get_municipality
    muni = get_municipality()
    if entity_type == "school":
        school = next((e for e in muni.entities if e.kind == "school"), None)
        return school.label if school else "the school district"
    return muni.name


class ReportRequest(BaseModel):
    report_type: str  # "financial_overview", "budget_analysis", "school_district", "custom"
    entity_type: str | None = "town"
    custom_prompt: str | None = None


@router.post("/generate")
async def generate_report(
    req: ReportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate a comprehensive AI report. Streams the response."""
    # Build context
    stmts = db.query(FinancialStatement).filter(
        FinancialStatement.entity_type == req.entity_type if req.entity_type else True
    ).order_by(FinancialStatement.fiscal_year).all()

    financial_data = []
    for s in stmts:
        entry = f"FY{s.fiscal_year}:"
        if s.total_revenue and isinstance(s.total_revenue, (int, float)) and s.total_revenue > 100:
            entry += f" Rev=${s.total_revenue:,.0f}"
        if s.total_expenditures and isinstance(s.total_expenditures, (int, float)) and s.total_expenditures > 100:
            entry += f" Exp=${s.total_expenditures:,.0f}"
        if s.fund_balance and isinstance(s.fund_balance, (int, float)):
            entry += f" FB=${s.fund_balance:,.0f}"
        if s.total_debt and isinstance(s.total_debt, (int, float)):
            entry += f" Debt=${s.total_debt:,.0f}"
        financial_data.append(entry)

    # Get document summaries for context
    docs = db.query(Document).filter(
        Document.notes.isnot(None),
        Document.category == req.entity_type if req.entity_type else True,
    ).order_by(Document.fiscal_year.desc()).limit(40).all()

    doc_context = "\n".join(f"- [{d.doc_type}] {d.filename}: {d.notes[:150]}" for d in docs)

    prompts = {
        "financial_overview": f"""Write a comprehensive financial overview report for the {_entity_name(req.entity_type)}.

Include:
1. Executive Summary
2. Revenue Analysis (trends, sources, year-over-year changes)
3. Expenditure Analysis (trends, major categories, growth rates)
4. Fund Balance Analysis (adequacy, trends)
5. Debt Analysis (outstanding debt, debt service burden)
6. Key Financial Ratios
7. Risks and Concerns
8. Recommendations

Use these actual financial figures:
{chr(10).join(financial_data)}

Recent documents:
{doc_context[:5000]}

Format as a professional report with headers, bullet points, and specific dollar amounts. Cite source documents.""",

        "budget_analysis": f"""Write a detailed budget analysis for {_entity_name(req.entity_type)}.
Compare budgeted vs actual amounts where available. Identify trends, concerns, and opportunities.

Financial data:
{chr(10).join(financial_data)}

Documents:
{doc_context[:5000]}""",

        "school_district": f"""Write a comprehensive analysis of the {_entity_name("school")} finances and operations.
Cover enrollment trends, per-pupil spending, budget trends, fund balance, debt, and comparisons with state averages.

Financial data:
{chr(10).join(financial_data)}

Documents:
{doc_context[:5000]}""",
    }

    prompt = prompts.get(req.report_type, req.custom_prompt or "Write a financial report.")

    if not GEMINI_API_KEY:
        async def no_key():
            yield f"data: {json.dumps({'type': 'delta', 'content': 'Configure GEMINI_API_KEY to generate reports.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return StreamingResponse(no_key(), media_type="text/event-stream")

    user_id_str = str(user.id)

    async def stream_report():
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        config = types.GenerateContentConfig(temperature=0.3, max_output_tokens=16000)

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt, config=config,
            ),
        )

        if response and response.text:
            text = response.text
            chunk_size = 30
            for i in range(0, len(text), chunk_size):
                yield f"data: {json.dumps({'type': 'delta', 'content': text[i:i+chunk_size]})}\n\n"
                await asyncio.sleep(0.01)

        try:
            in_t = out_t = 0
            usage = getattr(response, "usage_metadata", None) if response else None
            if usage is not None:
                in_t = int(getattr(usage, "prompt_token_count", 0) or 0)
                out_t = int(getattr(usage, "candidates_token_count", 0) or 0)
            if in_t or out_t:
                from database import SessionLocal
                from services.usage import record_usage
                sess = SessionLocal()
                try:
                    record_usage(
                        sess, source="reports", model="gemini-2.5-flash",
                        input_tokens=in_t, output_tokens=out_t,
                        user_id=user_id_str,
                        metadata={"report_type": getattr(req, "report_type", None)},
                    )
                finally:
                    sess.close()
        except Exception:
            pass

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(stream_report(), media_type="text/event-stream")
