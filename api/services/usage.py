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


# Per-million pricing (USD). Approximate; update when providers change rates.
# Anthropic: https://www.anthropic.com/pricing
# Gemini: https://ai.google.dev/pricing
_RATES: dict[str, tuple[float, float]] = {
    # input, output
    "claude-opus-4-7": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.0-flash": (0.10, 0.40),
    "voyage-3-lite": (0.02, 0.0),  # embedding-only — output rate N/A
    "voyage-3": (0.06, 0.0),
    "tesseract": (0.0, 0.0),       # local, free
    "pdfplumber": (0.0, 0.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for `model` at the given token counts. Falls back
    to Sonnet pricing if the model isn't in the rate table — better to log a
    rough number than zero, but warn so we notice unknown models."""
    rates = _RATES.get(model)
    if rates is None:
        # Best-effort fuzzy match.
        m = model.lower()
        if "opus" in m:
            rates = _RATES["claude-opus-4-7"]
        elif "haiku" in m:
            rates = _RATES["claude-haiku-4-5-20251001"]
        elif "sonnet" in m or "claude" in m:
            rates = _RATES["claude-sonnet-4-6"]
        elif "gemini" in m and "pro" in m:
            rates = _RATES["gemini-2.5-pro"]
        elif "gemini" in m:
            rates = _RATES["gemini-2.5-flash"]
        else:
            logger.warning("estimate_cost: unknown model %r — defaulting to Sonnet rates", model)
            rates = _RATES["claude-sonnet-4-6"]
    in_rate, out_rate = rates
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


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
                 estimated_cost_usd, user_id, resource_type, resource_id, metadata)
            VALUES
                (:id, :source, :model, :in_t, :out_t,
                 :cost, :uid, :rt, :rid, CAST(:meta AS JSONB))
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
        })
        db.commit()
    except Exception as exc:
        logger.warning("record_usage failed (source=%s model=%s): %s", source, model, exc)
        try:
            db.rollback()
        except Exception:
            pass
