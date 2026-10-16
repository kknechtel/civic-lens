"""
One structured Gemini call → dict. Schema-constrained JSON at temperature 0,
retried on transient errors, usage recorded to llm_usage.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from config import GEMINI_API_KEY
from services.llm_models import GEMINI_FLASH_LITE

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not configured")
        from google import genai
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


@dataclass
class JSONResult:
    data: Optional[dict]
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error: Optional[str] = None


def _parse(text: str) -> Optional[dict]:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        t = t.rsplit("```", 1)[0]
    try:
        out = json.loads(t)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


def generate_json(
    prompt: str,
    schema: dict[str, Any],
    *,
    model: str = GEMINI_FLASH_LITE,
    source: str,
    resource_id: Optional[str] = None,
    max_output_tokens: int = 1500,
    attempts: int = 3,
) -> JSONResult:
    """Synchronous. Call via asyncio.to_thread from async code."""
    from google.genai import types
    from services.usage import estimate_cost, record_usage

    cfg = dict(
        temperature=0,
        max_output_tokens=max_output_tokens,
        response_mime_type="application/json",
        response_schema=schema,
    )
    # Older google-genai builds (prod) reject thinking_budget; flash-lite
    # doesn't think by default, so it's only an optimization when available.
    try:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    except Exception:
        pass
    config = types.GenerateContentConfig(**cfg)
    last_err = None
    total_cost = 0.0
    for attempt in range(attempts):
        try:
            resp = _get_client().models.generate_content(model=model, contents=prompt, config=config)
        except Exception as exc:
            last_err = str(exc)
            logger.warning("gemini %s attempt %d failed: %s", source, attempt + 1, exc)
            time.sleep(2 ** attempt)
            continue

        usage = getattr(resp, "usage_metadata", None)
        in_t = int(getattr(usage, "prompt_token_count", 0) or 0)
        out_t = int(getattr(usage, "candidates_token_count", 0) or 0)
        cost = estimate_cost(model, in_t, out_t)
        total_cost += cost
        try:
            from database import SessionLocal
            with SessionLocal() as db:
                record_usage(db, source=source, model=model, input_tokens=in_t,
                             output_tokens=out_t, estimated_cost_usd=cost,
                             resource_type="document" if resource_id else None,
                             resource_id=resource_id)
        except Exception:
            logger.debug("usage record skipped", exc_info=True)

        data = _parse(getattr(resp, "text", "") or "")
        if data is not None:
            return JSONResult(data, total_cost, in_t, out_t)
        last_err = "unparseable JSON"
    return JSONResult(None, cost_usd=total_cost, error=last_err)
