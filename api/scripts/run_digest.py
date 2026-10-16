"""Entrypoint for the systemd-timer-driven daily digest.

For every enabled SavedAlert that is "due" (daily alerts on every fire,
weekly alerts only on Mondays), find new content matching the alert and
email a digest. Advance last_run_at after each evaluation; advance
last_sent_at only when a non-empty digest is actually sent.

Wire-up on EC2:
  /etc/systemd/system/ah-digest.service   (oneshot)
  /etc/systemd/system/ah-digest.timer     (OnCalendar=*-*-* 11:30:00 = 7:30am ET)

Idempotent — re-running mid-day on the same data produces zero new
emails because last_sent_at advances on each send.
"""
from __future__ import annotations

from municipality import get_municipality
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Iterable
from urllib.parse import quote

sys.path.insert(0, "/opt/atlantic-highlands/api")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import and_, or_

from config import PUBLIC_WEB_ORIGIN
from database import SessionLocal
from models.document import Document
from models.saved_alert import SavedAlert, AlertKind, DigestFrequency
from models.user import User
from services.email_service import send_email
from services.rag_search import search_chunks

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ah_digest")

# Caps so a runaway alert can't email a 10MB digest.
MAX_RESULTS_PER_ALERT = 25
KEYWORD_SEARCH_POOL = 60   # fetch wider, then post-filter by created_at


# ─── Helpers ──────────────────────────────────────────────────────────

_RECORDING_DOC_TYPES = (
    "recording_council",
    "recording_planning",
    "recording_zoning",
    "recording_harbor",
    "recording_school_board",
    "recording_meeting",
)

# Alert UI body choice → recording doc_type. "HHRSD" kept for alerts saved
# before bodies were generic.
_BODY_TO_DOC_TYPE = {
    "Council":  "recording_council",
    "Planning": "recording_planning",
    "Zoning":   "recording_zoning",
    "Harbor":   "recording_harbor",
    "Board of Education": "recording_school_board",
    "HHRSD":    "recording_school_board",
}


def _doc_url(doc_id: str) -> str:
    return f"{PUBLIC_WEB_ORIGIN}/document-library?doc={quote(str(doc_id))}"


def _meeting_url(doc_id: str) -> str:
    return f"{PUBLIC_WEB_ORIGIN}/meetings/{quote(str(doc_id))}"


def _is_due(alert: SavedAlert, now: datetime) -> bool:
    """Daily alerts are due on every fire; weekly only on Mondays.
    A first-time alert (last_sent_at=None) is always due."""
    if alert.frequency == DigestFrequency.weekly and now.weekday() != 0:
        return False
    return True


def _baseline(alert: SavedAlert, now: datetime) -> datetime:
    """High-water mark for "new" content.
    Falls back to 24h ago (daily) / 7d ago (weekly) on a brand-new alert."""
    if alert.last_run_at:
        return alert.last_run_at
    return now - (timedelta(days=7) if alert.frequency == DigestFrequency.weekly
                  else timedelta(days=1))


# ─── Per-kind matchers ────────────────────────────────────────────────

def _match_keyword(db, alert: SavedAlert, since: datetime) -> list[dict]:
    """Run the alert's hybrid query against chunks, post-filter to docs
    created after `since`. Returns up to MAX_RESULTS_PER_ALERT result rows
    flattened to {filename, snippet, fiscal_year, doc_type, category, url}."""
    if not alert.query:
        return []
    f = alert.filters or {}
    hits = search_chunks(
        db, alert.query, top_k=KEYWORD_SEARCH_POOL,
        category=f.get("category") or None,
        doc_type=f.get("doc_type") or None,
        department=f.get("department") or None,
    )
    out: list[dict] = []
    seen_doc_ids: set[str] = set()
    for h in hits:
        if h.get("created_at") and h["created_at"] < since:
            continue
        doc_id = str(h.get("document_id") or "")
        if doc_id in seen_doc_ids:
            continue  # one row per document in the digest
        seen_doc_ids.add(doc_id)
        snippet = (h.get("content") or "").strip()
        if len(snippet) > 320:
            snippet = snippet[:320].rsplit(" ", 1)[0] + "…"
        out.append({
            "title": h.get("filename") or "Document",
            "snippet": snippet,
            "fiscal_year": h.get("fiscal_year"),
            "doc_type": h.get("doc_type"),
            "category": h.get("category"),
            "url": _doc_url(doc_id),
        })
        if len(out) >= MAX_RESULTS_PER_ALERT:
            break
    return out


def _match_new_meetings(db, alert: SavedAlert, since: datetime) -> list[dict]:
    f = alert.filters or {}
    body = f.get("body")
    types = (_BODY_TO_DOC_TYPE[body],) if body in _BODY_TO_DOC_TYPE else _RECORDING_DOC_TYPES
    rows = (db.query(Document)
              .filter(Document.created_at > since,
                      Document.doc_type.in_(types))
              .order_by(Document.created_at.desc())
              .limit(MAX_RESULTS_PER_ALERT)
              .all())
    return [{
        "title": d.filename or "Meeting recording",
        "snippet": (d.summary or d.extracted_text or "")[:280].strip(),
        "fiscal_year": d.fiscal_year,
        "doc_type": d.doc_type,
        "category": d.category,
        "url": _meeting_url(d.id),
    } for d in rows]


def _match_new_documents(db, alert: SavedAlert, since: datetime) -> list[dict]:
    f = alert.filters or {}
    q = db.query(Document).filter(Document.created_at > since)
    if f.get("category"):
        q = q.filter(Document.category == f["category"])
    if f.get("doc_type"):
        q = q.filter(Document.doc_type == f["doc_type"])
    # Exclude meeting recordings — they have their own alert kind.
    q = q.filter(or_(Document.doc_type.is_(None),
                     ~Document.doc_type.in_(_RECORDING_DOC_TYPES)))
    rows = (q.order_by(Document.created_at.desc())
             .limit(MAX_RESULTS_PER_ALERT).all())
    return [{
        "title": d.filename or "Document",
        "snippet": (d.summary or d.extracted_text or "")[:280].strip(),
        "fiscal_year": d.fiscal_year,
        "doc_type": d.doc_type,
        "category": d.category,
        "url": _doc_url(d.id),
    } for d in rows]


_MATCHERS = {
    AlertKind.keyword:       _match_keyword,
    AlertKind.new_meeting:   _match_new_meetings,
    AlertKind.new_document:  _match_new_documents,
}


# ─── Email rendering ──────────────────────────────────────────────────

def _render_digest(alert: SavedAlert, items: Iterable[dict]) -> tuple[str, str, str]:
    """Returns (subject, html, text)."""
    items = list(items)
    n = len(items)
    subject = f"[{get_municipality().short_name}] {alert.name} — {n} new match{'es' if n != 1 else ''}"

    # Plaintext
    lines = [
        f"{alert.name}",
        f"{n} new match{'es' if n != 1 else ''} since {alert.last_run_at or 'subscription start'}",
        "",
    ]
    for i, it in enumerate(items, 1):
        meta_bits = [b for b in (it.get("category"), it.get("doc_type"),
                                 it.get("fiscal_year")) if b]
        meta = (" · " + " · ".join(meta_bits)) if meta_bits else ""
        lines.append(f"{i}. {it['title']}{meta}")
        if it.get("snippet"):
            lines.append(f"   {it['snippet']}")
        lines.append(f"   {it['url']}")
        lines.append("")
    lines.append("—")
    lines.append(f"Manage your alerts: {PUBLIC_WEB_ORIGIN}/alerts")
    text = "\n".join(lines)

    # HTML — intentionally simple, inline styles for client compat.
    html_items = []
    for it in items:
        meta_bits = [b for b in (it.get("category"), it.get("doc_type"),
                                 it.get("fiscal_year")) if b]
        meta_html = ('<div style="color:#6b7280;font-size:12px;margin-top:2px">'
                     + " · ".join(meta_bits) + "</div>") if meta_bits else ""
        snippet_html = (f'<div style="color:#374151;margin-top:6px">{it["snippet"]}</div>'
                        if it.get("snippet") else "")
        html_items.append(
            '<li style="margin:0 0 16px 0;padding:0;list-style:none;'
            'border-bottom:1px solid #e5e7eb;padding-bottom:12px">'
            f'<a href="{it["url"]}" style="color:#385854;text-decoration:none;'
            f'font-weight:600;font-size:15px">{it["title"]}</a>'
            f'{meta_html}{snippet_html}'
            '</li>'
        )
    html = (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'max-width:640px;margin:0 auto;padding:24px;color:#111827">'
        f'<div style="color:#6b7280;font-size:12px;letter-spacing:0.05em;'
        f'text-transform:uppercase">{get_municipality().short_name} · alert digest</div>'
        f'<h2 style="margin:8px 0 4px;font-size:20px">{alert.name}</h2>'
        f'<div style="color:#6b7280;font-size:13px;margin-bottom:20px">'
        f'{n} new match{"es" if n != 1 else ""}'
        '</div>'
        f'<ul style="margin:0;padding:0">{"".join(html_items)}</ul>'
        '<div style="margin-top:24px;color:#9ca3af;font-size:12px">'
        f'You can pause or delete this alert at '
        f'<a href="{PUBLIC_WEB_ORIGIN}/alerts" style="color:#6b7280">'
        f'{PUBLIC_WEB_ORIGIN}/alerts</a>.</div>'
        '</div>'
    )
    return subject, html, text


# ─── Main loop ────────────────────────────────────────────────────────

def run_once() -> dict:
    """Single pass — returns a counts dict for logging."""
    now = datetime.utcnow()
    db = SessionLocal()
    sent = empty = errored = skipped = 0
    try:
        alerts = (db.query(SavedAlert)
                    .filter(SavedAlert.enabled.is_(True))
                    .order_by(SavedAlert.created_at.asc())
                    .all())
        for alert in alerts:
            if not _is_due(alert, now):
                skipped += 1
                continue

            user = db.query(User).filter(User.id == alert.user_id).first()
            if not user or not user.is_active or not user.email:
                skipped += 1
                continue

            since = _baseline(alert, now)
            try:
                matcher = _MATCHERS[alert.kind]
                items = matcher(db, alert, since)
            except Exception as exc:
                errored += 1
                logger.exception("alert %s matcher failed: %s", alert.id, exc)
                # Don't advance last_run_at on failure — retry next tick.
                continue

            # Always advance the watermark, even on empty, so we don't keep
            # re-evaluating the same window. The matcher's `since` filter
            # already encoded the watermark for this pass.
            alert.last_run_at = now

            if not items:
                empty += 1
            else:
                subject, html, text = _render_digest(alert, items)
                mid = send_email(user.email, subject, html, text)
                if mid is not None:
                    alert.last_sent_at = now
                    sent += 1
                    logger.info("digest sent to %s for alert %s (%d items, msg=%s)",
                                user.email, alert.id, len(items), mid)
                else:
                    # SES skipped (unconfigured) or failed; treat as empty so we
                    # don't roll the watermark forward past unsent content.
                    alert.last_run_at = since
                    empty += 1

            db.commit()
    finally:
        db.close()

    return {"sent": sent, "empty": empty, "errored": errored, "skipped": skipped}


def main() -> int:
    summary = run_once()
    logger.info("digest done: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
