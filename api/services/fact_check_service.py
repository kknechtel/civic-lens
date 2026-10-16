"""
Fact-check a presentation. Walks every section's narrative body, extracts
[source: filename] and [DOC:document_id|label] citations, fetches the
underlying document text, and asks Claude whether each citation supports
the surrounding claim.

Verdicts: supported | partial | unsupported | unresolved | no_source
"""
import json
import logging
import re
from datetime import datetime
from typing import Optional

import anthropic
from sqlalchemy.orm import Session

from config import ANTHROPIC_API_KEY
from models.document import Document

log = logging.getLogger(__name__)

CITATION_PATTERNS = [
    re.compile(r"\[source:\s*([^\]]+)\]", re.IGNORECASE),
    re.compile(r"\[DOC:\s*([^\]\|]+?)(?:\s*\|\s*([^\]]+))?\]"),
]

VERDICT_SCHEMA_HINT = (
    "Return ONLY a JSON object with these keys: "
    '{"verdict": "supported"|"partial"|"unsupported"|"unresolved"|"no_source", '
    '"evidence_quote": "...", "missing": ["..."], "conflicting": ["..."]}'
)


def _iter_citations(body: str):
    """Yield (kind, key, label) tuples for every citation in `body`.

    kind: 'filename' for [source: foo.pdf] or 'doc_id' for [DOC:uuid|label]
    """
    for m in CITATION_PATTERNS[0].finditer(body or ""):
        yield "filename", m.group(1).strip(), m.group(1).strip()
    for m in CITATION_PATTERNS[1].finditer(body or ""):
        yield "doc_id", m.group(1).strip(), (m.group(2) or "").strip() or m.group(1).strip()


def _fetch_doc(db: Session, kind: str, key: str) -> Optional[Document]:
    if kind == "doc_id":
        try:
            return db.query(Document).filter(Document.id == key).first()
        except Exception:
            return None
    # filename — best-effort match
    return (
        db.query(Document).filter(Document.filename == key).first()
        or db.query(Document).filter(Document.filename.ilike(f"%{key}%")).first()
    )


def _check_one_claim(client: anthropic.Anthropic, claim: str, doc_text: str) -> tuple[dict, int, int]:
    """Returns (verdict_dict, input_tokens, output_tokens)."""
    prompt = (
        "You are fact-checking a single claim against a single source document.\n\n"
        f"CLAIM:\n{claim[:1500]}\n\n"
        f"SOURCE DOCUMENT EXCERPT:\n{doc_text[:18000]}\n\n"
        "Decide whether the source document supports the claim.\n"
        "- 'supported': the document clearly states the claim's facts.\n"
        "- 'partial': some facts match but key details (numbers, names, dates) differ.\n"
        "- 'unsupported': the document contradicts the claim.\n"
        "- 'unresolved': the document is silent on the claim.\n\n"
        + VERDICT_SCHEMA_HINT
    )
    in_t = out_t = 0
    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        in_t = getattr(resp.usage, "input_tokens", 0) or 0
        out_t = getattr(resp.usage, "output_tokens", 0) or 0
        text = next((b.text for b in resp.content if hasattr(b, "text")), "")
        # Strip markdown fencing if Claude wrapped JSON in ```json ... ```
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
        data = json.loads(text)
    except Exception as exc:
        log.warning("Fact-check parse failed: %s", exc)
        return ({"verdict": "unresolved", "evidence_quote": "", "missing": [], "conflicting": []}, in_t, out_t)
    return ({
        "verdict": data.get("verdict", "unresolved"),
        "evidence_quote": (data.get("evidence_quote") or "")[:600],
        "missing": data.get("missing") or [],
        "conflicting": data.get("conflicting") or [],
    }, in_t, out_t)


def fact_check_presentation(
    db: Session,
    sections: list,
    *,
    user_id: Optional[str] = None,
    presentation_id: Optional[str] = None,
) -> dict:
    """Fact-check every citation in every narrative section."""
    if not ANTHROPIC_API_KEY:
        return {"error": "anthropic_api_key_not_configured"}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    results: list[dict] = []
    summary = {"supported": 0, "partial": 0, "unsupported": 0, "unresolved": 0, "no_source": 0}
    total_in = total_out = 0
    claims_checked = 0

    for section in sections or []:
        if section.get("kind") != "narrative":
            continue
        body = section.get("body") or ""
        section_id = section.get("id")

        for kind, key, label in _iter_citations(body):
            doc = _fetch_doc(db, kind, key)
            if not doc or not (doc.extracted_text and len(doc.extracted_text) > 100):
                summary["no_source"] += 1
                results.append({
                    "section_id": section_id, "kind": kind,
                    "id": key, "label": label, "verdict": "no_source",
                    "evidence_quote": "", "claim": body[:400],
                    "missing": ["source_text_unavailable"], "conflicting": [],
                })
                continue

            verdict, in_t, out_t = _check_one_claim(client, body[:1500], doc.extracted_text)
            total_in += in_t
            total_out += out_t
            claims_checked += 1
            v = verdict["verdict"]
            summary[v] = summary.get(v, 0) + 1
            results.append({
                "section_id": section_id, "kind": kind,
                "id": str(doc.id), "label": label,
                "verdict": v,
                "evidence_quote": verdict["evidence_quote"],
                "claim": body[:400],
                "missing": verdict["missing"],
                "conflicting": verdict["conflicting"],
            })

    if claims_checked:
        try:
            from services.usage import record_usage
            record_usage(
                db, source="fact_check", model="claude-sonnet-4-6",
                input_tokens=total_in, output_tokens=total_out,
                user_id=user_id,
                resource_type="presentation", resource_id=presentation_id,
                metadata={"claims_checked": claims_checked},
            )
        except Exception:
            log.warning("fact_check usage record skipped", exc_info=True)

    return {
        "ran_at": datetime.utcnow().isoformat() + "Z",
        "summary": summary,
        "results": results,
    }


def fact_check_presentation_stream(
    db: Session,
    sections: list,
    *,
    user_id: Optional[str] = None,
    presentation_id: Optional[str] = None,
):
    """Generator version of fact_check_presentation — yields SSE-shaped dicts
    per citation as they finish, plus a final 'complete' record. The route
    handler turns each yield into a `data: {json}\\n\\n` SSE frame.

    Yields:
        {"type": "start", "total": int}
        {"type": "verdict", "verdict": {...}}     (one per citation)
        {"type": "complete", "ran_at": str, "summary": dict, "results": [...]}
        {"type": "error", "message": str}         (on failure)
    """
    if not ANTHROPIC_API_KEY:
        yield {"type": "error", "message": "anthropic_api_key_not_configured"}
        return

    # Pre-count citations so the UI can show "X of Y checked" progress.
    total = 0
    for section in sections or []:
        if section.get("kind") != "narrative":
            continue
        body = section.get("body") or ""
        for _ in _iter_citations(body):
            total += 1
    yield {"type": "start", "total": total}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    results: list[dict] = []
    summary = {"supported": 0, "partial": 0, "unsupported": 0, "unresolved": 0, "no_source": 0}
    total_in = total_out = 0
    claims_checked = 0

    for section in sections or []:
        if section.get("kind") != "narrative":
            continue
        body = section.get("body") or ""
        section_id = section.get("id")
        section_title = section.get("title")

        for kind, key, label in _iter_citations(body):
            doc = _fetch_doc(db, kind, key)
            if not doc or not (doc.extracted_text and len(doc.extracted_text) > 100):
                summary["no_source"] += 1
                rec = {
                    "section_id": section_id, "section_heading": section_title,
                    "kind": kind, "id": key, "label": label, "verdict": "no_source",
                    "evidence_quote": "", "claim": body[:400],
                    "missing": ["source_text_unavailable"], "conflicting": [],
                }
                results.append(rec)
                yield {"type": "verdict", "verdict": rec}
                continue

            verdict, in_t, out_t = _check_one_claim(client, body[:1500], doc.extracted_text)
            total_in += in_t
            total_out += out_t
            claims_checked += 1
            v = verdict["verdict"]
            summary[v] = summary.get(v, 0) + 1
            rec = {
                "section_id": section_id, "section_heading": section_title,
                "kind": kind, "id": str(doc.id), "label": label,
                "verdict": v,
                "evidence_quote": verdict["evidence_quote"],
                "claim": body[:400],
                "missing": verdict["missing"],
                "conflicting": verdict["conflicting"],
            }
            results.append(rec)
            yield {"type": "verdict", "verdict": rec}

    if claims_checked:
        try:
            from services.usage import record_usage
            record_usage(
                db, source="fact_check", model="claude-sonnet-4-6",
                input_tokens=total_in, output_tokens=total_out,
                user_id=user_id,
                resource_type="presentation", resource_id=presentation_id,
                metadata={"claims_checked": claims_checked},
            )
        except Exception:
            log.warning("fact_check usage record skipped", exc_info=True)

    ran_at = datetime.utcnow().isoformat() + "Z"
    yield {
        "type": "complete",
        "ran_at": ran_at,
        "summary": summary,
        "results": results,
    }
