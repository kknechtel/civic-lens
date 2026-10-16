"""
One cheap model call per document → title, summary, and classification.

Input is the first ~10k chars of extracted text plus filename / URL /
source-site hints. Hints guide the model but the text wins: a document
scraped from a neighbouring town's site that is clearly about the primary
municipality gets that municipality's entity key.

The list of governments ("entities") and the wording of the prompt come from
the active municipality profile (municipalities/<slug>/municipality.yaml).

Writes overwrite scraper guesses unless the field is listed in
metadata.locked_fields (set when a person edits it in the UI).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from municipality import Municipality, get_municipality
from services.gemini_json import generate_json

logger = logging.getLogger(__name__)

ENRICH_VERSION = 1
MAX_INPUT_CHARS = 10_000

DOC_TYPES = [
    "agenda", "minutes", "resolution", "ordinance", "budget", "audit",
    "audit_management_report", "financial_statement", "bill_list", "contract",
    "bid", "planning", "zoning_application", "legal", "records_request",
    "notice", "newsletter", "presentation", "policy", "report",
    "performance_report", "election", "menu", "calendar", "form", "general",
]
FIELDS = ("title", "doc_type", "body", "municipality", "department", "doc_date", "fiscal_year")

def schema_for(muni: Municipality) -> dict:
    s = {**SCHEMA, "properties": dict(SCHEMA["properties"])}
    s["properties"]["municipality"] = {"type": "STRING", "enum": muni.entity_keys}
    return s


SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "doc_type": {"type": "STRING", "enum": DOC_TYPES},
        "body": {"type": "STRING", "nullable": True},
        "municipality": {"type": "STRING"},  # enum filled per profile in schema_for()
        "department": {"type": "STRING", "nullable": True},
        "doc_date": {"type": "STRING", "nullable": True},
        "fiscal_year": {"type": "STRING", "nullable": True},
        "confidence": {"type": "NUMBER"},
    },
    "required": ["title", "summary", "doc_type", "municipality", "confidence"],
}

PROMPT = """You index public records for {place}. Read the document excerpt and return JSON.

Governments (municipality):
{entities}
Decide from the TEXT (letterhead, seal, addresses, body names). Source hints can be wrong.

Fields:
- title: concise and specific, <= 90 chars. Pattern "<Short body> — <Kind>, <Mon D, YYYY>" when there is a meeting or date,
  e.g. "City Council — Minutes, Jan 6, 2026", "Planning Board — Agenda, Apr 2, 2026",
  "School District — 2025-26 Adopted Budget", "Ordinance 2026-07 — Short-Term Rentals".
  Use the entity's short name ({short_names}) as the body prefix when it isn't the primary government.
  Never a bare filename or number.
- summary: one plain sentence saying what it is, then 2-4 short "- " bullets with the concrete specifics
  (actions taken, votes, dollar amounts, addresses, names of applicants). No preamble like "This document".
  If the excerpt is a menu, calendar, or form, one sentence is enough.
- doc_type: best fit from the enum.
- body: the board/committee/office (e.g. "Borough Council", "Planning Board", "Harbor Commission", "Board of Education"), or null.
- department: municipal department if clear (e.g. "Police", "Public Works", "Tax Collector"), else null.
- doc_date: the document's own date (meeting date, adoption date) as YYYY-MM-DD, else null.
- fiscal_year: if the document is about a fiscal year: {fy_rule}; else null.
- confidence: 0-1, how sure you are of municipality + doc_type + date.

Hints (may be wrong):
filename: {filename}
source_site: {source_site}
source_url: {source_url}
link_text: {link_text}

Document excerpt:
<<<
{text}
>>>"""

_PREAMBLE = re.compile(r"^(here is|here's|this is) (a |the )?(summary|overview)[^:\n]*:?\s*", re.I)


@dataclass
class EnrichResult:
    ok: bool
    changes: dict = field(default_factory=dict)
    summary: str = ""
    confidence: float = 0.0
    cost_usd: float = 0.0
    error: Optional[str] = None


def _clean_date(v: Optional[str]) -> Optional[str]:
    if v and re.fullmatch(r"(19|20)\d{2}-\d{2}-\d{2}", v):
        return v
    return None


def _clean_fy(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    v = v.strip()
    if re.fullmatch(r"(19|20)\d{2}", v):
        return v
    m = re.fullmatch(r"((?:19|20)\d{2})-((?:19|20)?\d{2})", v)
    if m:
        end = m.group(2)
        end = int(end) if len(end) == 4 else int(m.group(1)[:2] + end)
        if end == int(m.group(1)) + 1:
            return f"{m.group(1)}-{end}"
    return None


def _fy_rule(muni: Municipality) -> str:
    town = ('"YYYY"' if muni.fiscal_year.start_month == 1
            else '"YYYY-YYYY" (the town\'s fiscal year starts in month %d)' % muni.fiscal_year.start_month)
    return f'{town} for {muni.short_name}, "YYYY-YYYY" for school years'


def render_prompt(doc, text: str, muni: Municipality | None = None) -> str:
    muni = muni or get_municipality()
    meta = doc.metadata_ or {}
    return PROMPT.format(
        place=f"{muni.short_name}, {muni.state}",
        entities=muni.entities_prompt(),
        short_names=", ".join(e.short for e in muni.entities),
        fy_rule=_fy_rule(muni),
        filename=doc.filename or "",
        source_site=meta.get("source_site") or "upload",
        source_url=meta.get("source_url") or "",
        link_text=meta.get("title") or "",
        text=text[:MAX_INPUT_CHARS],
    )


def analyze(doc, text: str) -> EnrichResult:
    """Run the model on one document. Does not touch the DB."""
    muni = get_municipality()
    prompt = render_prompt(doc, text, muni)
    res = generate_json(prompt, schema_for(muni), source="doc_enrich", resource_id=str(doc.id))
    if not res.data:
        return EnrichResult(False, cost_usd=res.cost_usd, error=res.error)
    d = res.data
    title = (d.get("title") or "").strip()[:200]
    summary = _PREAMBLE.sub("", (d.get("summary") or "").strip())
    changes = {
        "title": title or None,
        "doc_type": d.get("doc_type") if d.get("doc_type") in DOC_TYPES else None,
        "body": (d.get("body") or "").strip() or None,
        "municipality": d.get("municipality") if d.get("municipality") in muni.entity_keys else None,
        "department": (d.get("department") or "").strip() or None,
        "doc_date": _clean_date(d.get("doc_date")),
        "fiscal_year": _clean_fy(d.get("fiscal_year")),
    }
    return EnrichResult(True, {k: v for k, v in changes.items() if v}, summary,
                        float(d.get("confidence") or 0), res.cost_usd)


def apply(doc, result: EnrichResult) -> dict:
    """Write an EnrichResult onto a Document. Returns {field: (old, new)} for changed fields."""
    meta = dict(doc.metadata_ or {})
    locked = set(meta.get("locked_fields") or [])
    diff = {}
    for k, v in result.changes.items():
        if k in locked:
            continue
        if k == "body":
            if meta.get("body") != v:
                diff[k] = (meta.get("body"), v)
                meta["body"] = v
            continue
        # Recordings keep their recording_* doc_type; the pipeline keys off it.
        if k == "doc_type" and (doc.doc_type or "").startswith("recording"):
            continue
        old = getattr(doc, k, None)
        if old != v:
            diff[k] = (old, v)
            setattr(doc, k, v)
    if result.summary and "notes" not in locked and doc.notes != result.summary:
        diff["notes"] = (doc.notes, result.summary)
        doc.notes = result.summary
    # Keep the legacy category column consistent for code that still filters on it.
    entity_key = result.changes.get("municipality")
    if entity_key and "category" not in locked:
        doc.category = get_municipality().entity_category(entity_key)
    meta.update({
        "enrich_version": ENRICH_VERSION,
        "enriched_at": datetime.utcnow().isoformat(),
        "enrich_confidence": result.confidence,
    })
    doc.metadata_ = meta
    return diff
