"""LLM usage recording.

One row per LLM call. Cheap to write, easy to aggregate. The admin cost
panel reads from `llm_usage`. user_id may be NULL for system jobs.

Cost rates live here so we have one source of truth — different call sites
all funnel through `record_usage()` and pass model + tokens.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD estimate from the deployment's rate table (services.ai.catalog,
    editable in Admin → AI). Unknown models use their family's rate."""
    from services.ai import estimate_cost as _est
    try:
        from database import SessionLocal
        from services.ai.router import settings
        with SessionLocal() as db:
            rates = settings(db)["rates"]
    except Exception:
        rates = None
    return _est(model, input_tokens, output_tokens, rates)[0]


def record_usage(
    db: Session,
    *,
    source: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost_usd: Optional[float] = None,
    user_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    payer: str = "deployment",
    provider: Optional[str] = None,
) -> None:
    """Persist one usage row. Swallows DB errors so cost-tracking failures
    never break the user-facing call. If estimated_cost_usd is omitted it's
    computed from rates; pass it explicitly when the provider returns the
    real cost (e.g. Gemini Vision OCR)."""
    if estimated_cost_usd is None:
        estimated_cost_usd = estimate_cost(model, input_tokens, output_tokens)
    try:
        import json
        db.execute(sql_text("""
            INSERT INTO llm_usage
                (id, source, model, input_tokens, output_tokens,
                 estimated_cost_usd, user_id, resource_type, resource_id, metadata, payer, provider)
            VALUES
                (:id, :source, :model, :in_t, :out_t,
                 :cost, :uid, :rt, :rid, CAST(:meta AS JSONB), :payer, :provider)
        """), {
            "id": str(uuid.uuid4()),
            "source": source,
            "model": model,
            "in_t": int(input_tokens or 0),
            "out_t": int(output_tokens or 0),
            "cost": float(estimated_cost_usd or 0.0),
            "uid": str(user_id) if user_id else None,
            "rt": resource_type,
            "rid": str(resource_id) if resource_id else None,
            "meta": json.dumps(metadata or {}),
            "payer": payer,
            "provider": provider,
        })
        db.commit()
    except Exception as exc:
        logger.warning("record_usage failed (source=%s model=%s): %s", source, model, exc)
        try:
            db.rollback()
        except Exception:
            pass
