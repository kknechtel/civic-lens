"""
Presentations & analytical decks.

  GET    /api/presentations
  POST   /api/presentations
  GET    /api/presentations/{id}
  PUT    /api/presentations/{id}
  DELETE /api/presentations/{id}
  PATCH  /api/presentations/{id}/section/{section_id}
  POST   /api/presentations/{id}/section
  DELETE /api/presentations/{id}/section/{section_id}
  POST   /api/presentations/{id}/attachments
  DELETE /api/presentations/{id}/attachments/{att_id}
  POST   /api/presentations/{id}/publish
  POST   /api/presentations/{id}/unpublish
  POST   /api/presentations/{id}/password         body: {password: str}
  POST   /api/presentations/{id}/ai-chat          SSE stream — propose_section, search_chunks
  POST   /api/presentations/{id}/fact-check       run citation entailment
  POST   /api/presentations/{id}/ai-edit          rewrite/expand/tighten one section
  GET    /api/presentations/{id}/export?format=pptx|docx   download office file
  POST   /api/presentations/from-chat             create deck from chat transcript
  GET    /api/presentations/public/{slug}/meta    public, no auth
  GET    /api/presentations/public/{slug}         public, header X-Deck-Password if protected
"""
from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime
from typing import List, Literal, Optional

import bcrypt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_, text as sql_text
from sqlalchemy.orm import Session

from auth import (
    get_current_user,
    require_edit,
    require_owner_or_admin,
    require_view,
    shared_resource_ids,
    user_share_role,
)
from config import ANTHROPIC_API_KEY
from database import get_db, SessionLocal
from models.presentation import Presentation
from models.user import User
from services.deck_chat_service import build_sections_summary, stream_deck_chat
from services.deck_export import export_docx, export_pptx
from services.fact_check_service import fact_check_presentation, fact_check_presentation_stream

logger = logging.getLogger(__name__)

router = APIRouter()


def _place() -> str:
    from municipality import get_municipality
    m = get_municipality()
    return f"{m.short_name}, {m.state}"


# ─── Schemas ────────────────────────────────────────────────────────────────

class CreatePresentation(BaseModel):
    title: str
    sections: Optional[list] = None
    theme: Optional[dict] = None


class UpdatePresentation(BaseModel):
    title: Optional[str] = None
    sections: Optional[list] = None
    attachments: Optional[list] = None
    theme: Optional[dict] = None


class SectionPatch(BaseModel):
    """Partial update to a single section. Any provided field overwrites."""
    title: Optional[str] = None
    body: Optional[str] = None
    kind: Optional[str] = None
    headers: Optional[list[str]] = None
    rows: Optional[list[list[str]]] = None
    caption: Optional[str] = None
    attachment_id: Optional[str] = None
    tsx: Optional[str] = None
    data: Optional[object] = None


class NewSection(BaseModel):
    kind: str  # narrative, table, attachment, react_component
    title: str = ""
    body: Optional[str] = None
    headers: Optional[list[str]] = None
    rows: Optional[list[list[str]]] = None
    caption: Optional[str] = None
    attachment_id: Optional[str] = None
    tsx: Optional[str] = None
    data: Optional[object] = None
    after_section_id: Optional[str] = None


class AttachmentBody(BaseModel):
    document_id: str
    caption: Optional[str] = None


class AIChatAttachment(BaseModel):
    # "image" → image_block; "document" → PDF/document block. Anthropic
    # requires the raw bytes already base64-encoded.
    type: Literal["image", "document"]
    media_type: str  # e.g. "image/png", "application/pdf"
    data_base64: str
    filename: Optional[str] = None


class AIChatBody(BaseModel):
    message: str
    history: Optional[list[dict]] = None
    # Scope the model to a specific section — adds a focus directive to the
    # system prompt so proposals land on the right section.
    target_section_id: Optional[str] = None
    # When true, switch to Opus 4.7 + adaptive thinking + effort=high and
    # stream `thinking` SSE events the client can render in a disclosure.
    deep_thinking: bool = False
    # Image / PDF attachments — sent as content blocks alongside the user
    # message so Claude can see them directly (not just via search).
    attachments: Optional[list[AIChatAttachment]] = None


class PasswordBody(BaseModel):
    password: str  # empty string clears the password


# ─── Helpers ────────────────────────────────────────────────────────────────

def _serialize(p: Presentation, user: Optional[User] = None,
               role: Optional[str] = None) -> dict:
    is_owner = bool(user and str(p.created_by) == str(user.id))
    return {
        "id": str(p.id),
        "title": p.title,
        "slug": p.slug,
        "public_slug": p.public_slug,
        "status": p.status,
        "sections": p.sections or [],
        "attachments": p.attachments or [],
        "theme": p.theme or {},
        "last_fact_check": p.last_fact_check,
        "has_password": bool(p.public_password_hash),
        "published_at": p.published_at.isoformat() if p.published_at else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "is_owner": is_owner,
        "share_role": None if is_owner else role,
    }


def _get_or_404(db: Session, presentation_id: str) -> Presentation:
    p = db.query(Presentation).filter(Presentation.id == presentation_id).first()
    if not p:
        raise HTTPException(404, "Presentation not found")
    return p


def _get_viewable_or_404(db: Session, presentation_id: str, user: User) -> Presentation:
    p = _get_or_404(db, presentation_id)
    require_view(db, "presentations", p.created_by, p.id, user)
    return p


def _get_editable_or_404(db: Session, presentation_id: str, user: User) -> Presentation:
    p = _get_or_404(db, presentation_id)
    require_edit(db, "presentations", p.created_by, p.id, user)
    return p


def _ensure_section_ids(sections: list) -> list:
    """Every section needs a stable id so the editor can target it."""
    out = []
    for s in sections or []:
        s = dict(s)
        if not s.get("id"):
            s["id"] = f"sec_{uuid.uuid4().hex[:10]}"
        out.append(s)
    return out


# ─── CRUD ───────────────────────────────────────────────────────────────────

@router.get("")
def list_presentations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(Presentation)
    if not user.is_admin:
        shared_ids = shared_resource_ids(db, "presentations", user.id)
        if shared_ids:
            q = q.filter(or_(
                Presentation.created_by == user.id,
                Presentation.id.in_(shared_ids),
            ))
        else:
            q = q.filter(Presentation.created_by == user.id)
    rows = q.order_by(Presentation.updated_at.desc()).limit(200).all()

    role_by_id: dict[str, str] = {}
    if not user.is_admin:
        share_rows = db.execute(sql_text(
            "SELECT presentation_id, role FROM presentation_shares WHERE user_id = :uid"
        ), {"uid": str(user.id)}).fetchall()
        role_by_id = {str(r[0]): r[1] for r in share_rows}
    return [_serialize(p, user, role_by_id.get(str(p.id))) for p in rows]


@router.post("")
def create_presentation(
    body: CreatePresentation,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    p = Presentation(
        title=body.title,
        sections=_ensure_section_ids(body.sections or []),
        theme=body.theme or {},
        created_by=user.id,
    )
    db.add(p); db.commit(); db.refresh(p)
    return _serialize(p, user)


@router.get("/{presentation_id}")
def get_presentation(presentation_id: str, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    p = _get_viewable_or_404(db, presentation_id, user)
    role = user_share_role(db, "presentations", p.id, user.id)
    return _serialize(p, user, role)


@router.put("/{presentation_id}")
def update_presentation(presentation_id: str, body: UpdatePresentation,
                        db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _get_editable_or_404(db, presentation_id, user)
    if body.title is not None:
        p.title = body.title
    if body.sections is not None:
        p.sections = _ensure_section_ids(body.sections)
    if body.attachments is not None:
        p.attachments = body.attachments
    if body.theme is not None:
        p.theme = body.theme
    db.commit(); db.refresh(p)
    return _serialize(p, user)


@router.delete("/{presentation_id}")
def delete_presentation(presentation_id: str, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    p = _get_or_404(db, presentation_id)
    require_owner_or_admin(p.created_by, user)
    db.delete(p); db.commit()
    return {"ok": True}


# ─── Sections ────────────────────────────────────────────────────────────────

@router.post("/{presentation_id}/section")
def add_section(presentation_id: str, body: NewSection, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    p = _get_editable_or_404(db, presentation_id, user)
    new_sec = {
        "id": f"sec_{uuid.uuid4().hex[:10]}",
        "kind": body.kind,
        "title": body.title,
    }
    for k in ("body", "headers", "rows", "caption", "attachment_id", "tsx", "data"):
        v = getattr(body, k)
        if v is not None:
            new_sec[k] = v

    sections = list(p.sections or [])
    if body.after_section_id:
        idx = next((i for i, s in enumerate(sections) if s.get("id") == body.after_section_id), -1)
        sections.insert(idx + 1 if idx >= 0 else len(sections), new_sec)
    else:
        sections.append(new_sec)
    p.sections = sections
    db.commit(); db.refresh(p)
    return _serialize(p, user)


@router.patch("/{presentation_id}/section/{section_id}")
def patch_section(presentation_id: str, section_id: str, body: SectionPatch,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _get_editable_or_404(db, presentation_id, user)
    sections = list(p.sections or [])
    for i, s in enumerate(sections):
        if s.get("id") == section_id:
            updated = dict(s)
            for k in ("title", "body", "kind", "headers", "rows", "caption", "attachment_id", "tsx", "data"):
                v = getattr(body, k)
                if v is not None:
                    updated[k] = v
            sections[i] = updated
            p.sections = sections
            db.commit(); db.refresh(p)
            return _serialize(p, user)
    raise HTTPException(404, "Section not found")


@router.delete("/{presentation_id}/section/{section_id}")
def delete_section(presentation_id: str, section_id: str,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _get_editable_or_404(db, presentation_id, user)
    p.sections = [s for s in (p.sections or []) if s.get("id") != section_id]
    db.commit(); db.refresh(p)
    return _serialize(p, user)


# ─── Attachments ────────────────────────────────────────────────────────────

@router.post("/{presentation_id}/attachments")
def add_attachment(presentation_id: str, body: AttachmentBody,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _get_editable_or_404(db, presentation_id, user)
    from models.document import Document
    doc = db.query(Document).filter(Document.id == body.document_id).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    att = {
        "id": f"att_{uuid.uuid4().hex[:10]}",
        "document_id": str(doc.id),
        "filename": doc.filename,
        "caption": body.caption,
    }
    p.attachments = [*(p.attachments or []), att]
    db.commit(); db.refresh(p)
    return _serialize(p, user)


@router.delete("/{presentation_id}/attachments/{att_id}")
def remove_attachment(presentation_id: str, att_id: str,
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _get_editable_or_404(db, presentation_id, user)
    p.attachments = [a for a in (p.attachments or []) if a.get("id") != att_id]
    db.commit(); db.refresh(p)
    return _serialize(p, user)


# ─── Publish / password ─────────────────────────────────────────────────────
# Publishing/password = owner-only — sharing read access is what the share
# endpoints are for; making a deck public is a different decision that
# only the owner should be able to make.

@router.post("/{presentation_id}/publish")
def publish(presentation_id: str, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """Publish (or republish) a deck. Creates a new PresentationVersion
    snapshot — the public viewer reads from the snapshot, NOT from the
    live draft sections, so edits stay private until republish."""
    from services.version_publisher import publish_new_version
    p = _get_or_404(db, presentation_id)
    require_owner_or_admin(p.created_by, user)
    if not p.public_slug:
        # short slugs are easier to share; collisions are negligible at this scale
        p.public_slug = secrets.token_urlsafe(9).replace("_", "").replace("-", "")[:12].lower()
    publish_new_version(p, db, user_id=getattr(user, "email", None) or str(user.id))
    db.commit(); db.refresh(p)
    return _serialize(p, user)


@router.post("/{presentation_id}/unpublish")
def unpublish(presentation_id: str, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    p = _get_or_404(db, presentation_id)
    require_owner_or_admin(p.created_by, user)
    p.public_slug = None
    p.status = "draft"
    p.published_at = None
    db.commit(); db.refresh(p)
    return _serialize(p, user)


@router.post("/{presentation_id}/password")
def set_password(presentation_id: str, body: PasswordBody,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _get_or_404(db, presentation_id)
    require_owner_or_admin(p.created_by, user)
    if not body.password:
        p.public_password_hash = None
        p.public_password_set_at = None
    else:
        p.public_password_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
        p.public_password_set_at = datetime.utcnow()
    db.commit(); db.refresh(p)
    return _serialize(p, user)


# ─── AI chat ────────────────────────────────────────────────────────────────

@router.post("/{presentation_id}/ai-chat")
async def ai_chat(presentation_id: str, body: AIChatBody, request: Request,
                  user: User = Depends(get_current_user)):
    """Stream a deck-aware AI chat. Emits SSE events: delta, tool_use, tool_result, proposal, done, error."""
    setup_db = SessionLocal()
    try:
        p = setup_db.query(Presentation).filter(Presentation.id == presentation_id).first()
        if not p:
            raise HTTPException(404, "Presentation not found")
        require_view(setup_db, "presentations", p.created_by, p.id, user)
        sections_summary = build_sections_summary(p.sections or [])
    finally:
        setup_db.close()

    return StreamingResponse(
        stream_deck_chat(
            body.message, sections_summary, body.history,
            user_id=str(user.id), presentation_id=presentation_id,
            target_section_id=body.target_section_id,
            deep_thinking=body.deep_thinking,
            attachments=[a.model_dump() for a in (body.attachments or [])],
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ─── Fact-check ─────────────────────────────────────────────────────────────

@router.post("/{presentation_id}/fact-check")
def fact_check(presentation_id: str, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    p = _get_editable_or_404(db, presentation_id, user)
    result = fact_check_presentation(
        db, p.sections or [],
        user_id=str(user.id), presentation_id=str(p.id),
    )
    p.last_fact_check = result
    db.commit()
    return result


@router.post("/{presentation_id}/fact-check/stream")
def fact_check_stream(presentation_id: str, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """SSE: stream per-citation verdicts as they complete. The final
    'complete' event also carries the full results array so the client
    can drop it into local state without a follow-up fetch. Persisted
    server-side once the stream ends so a page reload sees the same
    `last_fact_check` record the synchronous endpoint produces."""
    p = _get_editable_or_404(db, presentation_id, user)
    sections = p.sections or []
    pid = str(p.id)
    uid = str(user.id)

    def event_stream():
        last_complete: dict | None = None
        try:
            for ev in fact_check_presentation_stream(
                db, sections, user_id=uid, presentation_id=pid,
            ):
                if ev.get("type") == "complete":
                    last_complete = ev
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        # Persist the final summary so a reload doesn't have to re-run.
        if last_complete:
            try:
                p.last_fact_check = {
                    "ran_at": last_complete["ran_at"],
                    "summary": last_complete["summary"],
                    "results": last_complete["results"],
                }
                db.commit()
            except Exception:  # noqa: BLE001
                logger.warning("fact_check persist failed", exc_info=True)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ─── Export (PPTX / DOCX) ───────────────────────────────────────────────────

@router.get("/{presentation_id}/export")
def export_deck(
    presentation_id: str,
    format: Literal["pptx", "docx"] = Query("pptx"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Stream a generated PPTX or DOCX of the presentation."""
    p = _get_viewable_or_404(db, presentation_id, user)

    if format == "pptx":
        data, filename = export_pptx(p)
        media = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    else:
        data, filename = export_docx(p)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─── AI-edit (rewrite / expand / tighten / fact-check one section) ──────────

class AIEditBody(BaseModel):
    section_id: str
    action: Literal["rewrite_tighter", "expand", "fact_polish", "translate_plain", "summarize"]
    instructions: Optional[str] = None  # extra freeform guidance for the AI


_AI_EDIT_GUIDANCE = {
    "rewrite_tighter":
        "Rewrite the section to be tighter and more direct. Cut filler. Keep all "
        "facts, numbers, and citations. Aim for ~30-40% fewer words.",
    "expand":
        "Expand the section with additional detail, context, and supporting facts. "
        "If the original cites documents, weave in additional related citations from "
        "the same documents where useful. Do not invent facts.",
    "fact_polish":
        "Tighten any imprecise claims. Where the original says approximations, replace "
        "with the actual figures from cited sources if you can verify them. Flag any "
        "unverifiable claims with [unverified]. Keep all citations.",
    "translate_plain":
        "Rewrite the section in plain English suitable for a resident with no government "
        "or finance background. Preserve all numbers and citations. Avoid jargon.",
    "summarize":
        "Replace the section body with a 3-5 bullet executive summary. Keep all "
        "[source: ...] citations associated with their facts.",
}


@router.post("/{presentation_id}/ai-edit")
def ai_edit_section(
    presentation_id: str,
    body: AIEditBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Apply an AI-powered transformation to a single section's body. Returns
    the updated presentation (the section's `body` is replaced in place).

    Synchronous (no SSE) — these are usually fast (<10s) on Sonnet."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")
    p = _get_editable_or_404(db, presentation_id, user)

    sections = list(p.sections or [])
    target_idx = next((i for i, s in enumerate(sections) if s.get("id") == body.section_id), -1)
    if target_idx < 0:
        raise HTTPException(404, "Section not found")
    section = dict(sections[target_idx])
    if section.get("kind") != "narrative":
        raise HTTPException(400, "ai-edit currently only works on narrative sections")

    original_body = section.get("body") or ""
    if not original_body.strip():
        raise HTTPException(400, "Section is empty — nothing to edit")

    guidance = _AI_EDIT_GUIDANCE[body.action]
    if body.instructions:
        guidance += "\n\nAdditional instructions from the operator:\n" + body.instructions

    prompt = (
        f"You are editing one section of a presentation about {_place()} municipal affairs.\n\n"
        f"## Action: {body.action}\n{guidance}\n\n"
        f"## Section title: {section.get('title') or '(untitled)'}\n\n"
        f"## Current body:\n{original_body}\n\n"
        f"Output ONLY the new markdown body (no commentary, no fences, no preamble)."
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        new_body = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                new_body += block.text or ""
        new_body = new_body.strip()
        # Drop accidental code-fence wrappers some models emit
        new_body = new_body.removeprefix("```markdown").removeprefix("```md").removeprefix("```").strip()
        new_body = new_body.removesuffix("```").strip()
        if not new_body:
            raise HTTPException(502, "AI returned empty body")
        from services.usage import record_usage
        record_usage(
            db, source="ai_edit", model="claude-sonnet-4-6",
            input_tokens=getattr(resp.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(resp.usage, "output_tokens", 0) or 0,
            user_id=str(user.id),
            resource_type="presentation", resource_id=str(p.id),
            metadata={"action": body.action, "section_id": body.section_id},
        )
    except anthropic.APIStatusError as exc:
        logger.error("ai-edit Anthropic error: %s", exc)
        raise HTTPException(502, f"AI service error: {getattr(exc, 'message', str(exc))}")
    except Exception as exc:
        logger.exception("ai-edit failed")
        raise HTTPException(500, str(exc)[:300])

    section["body"] = new_body
    sections[target_idx] = section
    p.sections = sections
    db.commit(); db.refresh(p)
    return _serialize(p, user)


# ─── From-chat: turn a chat transcript into a draft deck ────────────────────

class FromChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class FromChatBody(BaseModel):
    messages: List[FromChatMessage]
    title_hint: Optional[str] = None


_FROM_CHAT_PROMPT = """You are converting a chat transcript about {place} into a structured presentation.

Output ONLY a JSON object of this shape:
{{
  "title": "<concise deck title>",
  "sections": [
    {{ "kind": "narrative", "title": "<heading>", "body": "<markdown body with citations and embedded chart fences>" }},
    ...
  ]
}}

RULES:
- 6-10 sections. Group related findings together. Don't make one section per chat turn.
- Each narrative body should be 2-6 paragraphs of substantive content.
- PRESERVE every [source: filename.pdf] citation from the assistant turns — do not drop them.
- The transcript contains tokens like [[CHART_PLACEHOLDER_0]], [[CHART_PLACEHOLDER_1]], etc.
  These represent live Chart.js charts the user will see in the deck. Keep the EXACT token
  text in the narrative body it belongs to. Do NOT replace, paraphrase, expand, or remove it
  — the backend swaps the token back to the original chart after you respond. If a section
  references a chart, just include the [[CHART_PLACEHOLDER_N]] token on its own line
  inside the markdown body.
- PRESERVE every markdown table inside the narrative body. Don't convert to "table" sections
  unless the table is the entire point of a section — narrative sections render markdown
  tables natively, including the chart fences.
- DO NOT paraphrase or round numbers. Copy figures, dollar amounts, percentages, dates,
  and proper nouns verbatim from the assistant's output.
- DO NOT summarize away findings. If the chat covered 8 topics, the deck should cover all 8.
- If the user typed a clear prompt at the top, weave it into a section 1 "Background" or "Question".
- title_hint, if provided, is a strong suggestion for the deck title.

Output ONLY valid JSON — no fences, no commentary."""


def _structure_chat_with_claude(messages: list[dict], title_hint: Optional[str]) -> tuple[dict, int, int]:
    """Returns (deck_json, input_tokens, output_tokens) so the caller can
    record usage."""
    import anthropic
    import re as _re
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Belt-and-suspenders: extract every ```chart fence from the transcript and
    # replace with an opaque placeholder before sending to Claude. The model
    # would otherwise paraphrase or drop the JSON. We restore the original
    # fences after Claude returns the structured deck.
    chart_blocks: list[str] = []
    def _stash_chart(m):
        idx = len(chart_blocks)
        chart_blocks.append(m.group(0))
        return f"[[CHART_PLACEHOLDER_{idx}]]"

    # Trim each turn to a sane size — full chat transcripts often run 300K+
    # tokens once tool results bloat the assistant turns. The structurer doesn't
    # need every intermediate "Now let me check…" line, just the final content.
    PER_TURN_CHARS = 12000
    transcript_lines = []
    for m in messages:
        role = (m.get("role") or "user").upper()
        body = (m.get("content") or "")
        # Extract chart blocks BEFORE trimming so a fence that straddles the cut
        # still gets preserved in full.
        body = _re.sub(r"```chart\s*\n[\s\S]*?\n```", _stash_chart, body)
        if len(body) > PER_TURN_CHARS:
            body = body[:PER_TURN_CHARS] + "\n\n[... truncated ...]"
        transcript_lines.append(f"## {role}\n\n{body}")
    transcript = "\n\n---\n\n".join(transcript_lines)

    prompt = _FROM_CHAT_PROMPT.replace("{place}", _place())
    if title_hint:
        prompt += f"\n\ntitle_hint: {title_hint}"

    # Use Haiku 4.5 — structuring a transcript into JSON sections is a
    # straightforward reformatting task; Sonnet's ~5x latency isn't worth it.
    # 1M-context beta still useful when transcripts are large after trimming.
    # Anthropic SDK refuses non-streaming for long requests, so we stream.
    text = ""
    in_t = out_t = 0
    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=12000,
        system=prompt,
        messages=[{"role": "user", "content": f"TRANSCRIPT:\n\n{transcript}"}],
        extra_headers={"anthropic-beta": "context-1m-2025-08-07"},
    ) as stream:
        for chunk in stream.text_stream:
            text += chunk
        try:
            final_msg = stream.get_final_message()
            in_t = getattr(final_msg.usage, "input_tokens", 0) or 0
            out_t = getattr(final_msg.usage, "output_tokens", 0) or 0
        except Exception:
            pass
    text = text.strip().removeprefix("```json").removeprefix("```").strip().removesuffix("```").strip()

    def _restore_charts(obj):
        """Walk the parsed deck JSON and substitute placeholders back to
        their original ```chart``` fences. Operates on dict/list/str."""
        if isinstance(obj, str):
            def repl(m):
                idx = int(m.group(1))
                return chart_blocks[idx] if 0 <= idx < len(chart_blocks) else m.group(0)
            return _re.sub(r"\[\[CHART_PLACEHOLDER_(\d+)\]\]", repl, obj)
        if isinstance(obj, list):
            return [_restore_charts(x) for x in obj]
        if isinstance(obj, dict):
            return {k: _restore_charts(v) for k, v in obj.items()}
        return obj

    try:
        return _restore_charts(json.loads(text)), in_t, out_t
    except json.JSONDecodeError:
        # Best-effort: salvage the largest JSON object substring
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return _restore_charts(json.loads(text[start:end + 1])), in_t, out_t
            except Exception:
                pass
        raise HTTPException(502, "AI returned invalid JSON for the deck structure")


@router.post("/from-chat")
def create_from_chat(
    body: FromChatBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Convert a chat conversation into a draft presentation. The assistant
    structures the messages into 4-8 sections, preserving citations."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")
    if not body.messages:
        raise HTTPException(400, "messages cannot be empty")

    msg_dicts = [{"role": m.role, "content": m.content} for m in body.messages]
    structured, in_t, out_t = _structure_chat_with_claude(msg_dicts, body.title_hint)

    title = (structured.get("title") or body.title_hint or "Untitled Presentation").strip()
    raw_sections = structured.get("sections") or []
    sections = _ensure_section_ids([
        s for s in raw_sections
        if isinstance(s, dict) and s.get("kind") in ("narrative", "table")
    ])

    p = Presentation(
        title=title,
        sections=sections,
        theme={},
        created_by=user.id,
    )
    db.add(p); db.commit(); db.refresh(p)

    from services.usage import record_usage
    record_usage(
        db, source="deck_from_chat", model="claude-haiku-4-5-20251001",
        input_tokens=in_t, output_tokens=out_t,
        user_id=str(user.id),
        resource_type="presentation", resource_id=str(p.id),
        metadata={"message_count": len(msg_dicts)},
    )

    return _serialize(p, user)


# ─── Comments (threaded review) ─────────────────────────────────────────────

class CommentBody(BaseModel):
    section_id: Optional[str] = None
    body: str
    parent_comment_id: Optional[str] = None
    # Inline-range anchor: {quote, prefix?, suffix?}. NULL for section-level.
    # When set, the viewer highlights the matched text and the panel shows
    # a "Commenting on …" preview above the comment body.
    anchor: Optional[dict] = None


class CommentPatchBody(BaseModel):
    """Edit body OR resolve/unresolve. Both fields optional."""
    body: Optional[str] = None
    resolved: Optional[bool] = None


def _serialize_comment(c) -> dict:
    return {
        "id": str(c.id),
        "presentation_id": str(c.presentation_id),
        "section_id": c.section_id,
        "parent_comment_id": str(c.parent_comment_id) if c.parent_comment_id else None,
        "author_email": c.author_email,
        "author_name": c.author_name,
        "body": c.body,
        "resolved": c.resolved,
        "resolved_by_email": c.resolved_by_email,
        "anchor": getattr(c, "anchor", None),
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@router.get("/{presentation_id}/comments")
def list_comments(presentation_id: str, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    from models.presentation import PresentationComment
    _get_viewable_or_404(db, presentation_id, user)
    rows = (
        db.query(PresentationComment)
        .filter(PresentationComment.presentation_id == presentation_id)
        .order_by(PresentationComment.created_at)
        .all()
    )
    return [_serialize_comment(c) for c in rows]


@router.post("/{presentation_id}/comments")
def add_comment(presentation_id: str, body: CommentBody,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from models.presentation import PresentationComment
    if not body.body.strip():
        raise HTTPException(400, "body cannot be empty")
    _get_viewable_or_404(db, presentation_id, user)
    kwargs = dict(
        presentation_id=presentation_id,
        section_id=body.section_id,
        parent_comment_id=body.parent_comment_id,
        author_email=user.email,
        author_name=getattr(user, "full_name", None) or user.email,
        body=body.body.strip(),
    )
    # Only pass anchor if the column exists on the model — guarded for
    # back-compat with older deploys that haven't run the inline migration.
    if hasattr(PresentationComment, "anchor") and body.anchor:
        kwargs["anchor"] = body.anchor
    c = PresentationComment(**kwargs)
    db.add(c); db.commit(); db.refresh(c)
    return _serialize_comment(c)


@router.patch("/{presentation_id}/comments/{comment_id}")
def patch_comment(presentation_id: str, comment_id: str, body: CommentPatchBody,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Edit body and/or resolve state of a single comment. Body edits
    require the comment author; anyone with view access can toggle resolve."""
    from models.presentation import PresentationComment
    p = _get_viewable_or_404(db, presentation_id, user)
    c = (
        db.query(PresentationComment)
        .filter(PresentationComment.id == comment_id,
                PresentationComment.presentation_id == presentation_id)
        .first()
    )
    if not c:
        raise HTTPException(404, "Comment not found")
    if body.body is not None:
        is_author = c.author_email and user.email and c.author_email.lower() == user.email.lower()
        is_owner = str(p.created_by) == str(user.id)
        if not (is_author or is_owner or user.is_admin):
            raise HTTPException(403, "You can only edit your own comments")
        c.body = body.body.strip()
    if body.resolved is not None:
        c.resolved = bool(body.resolved)
        c.resolved_by_email = user.email if c.resolved else None
    db.commit(); db.refresh(c)
    return _serialize_comment(c)


@router.patch("/{presentation_id}/comments/{comment_id}/resolve")
def toggle_resolve(presentation_id: str, comment_id: str,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from models.presentation import PresentationComment
    _get_viewable_or_404(db, presentation_id, user)
    c = (
        db.query(PresentationComment)
        .filter(PresentationComment.id == comment_id,
                PresentationComment.presentation_id == presentation_id)
        .first()
    )
    if not c:
        raise HTTPException(404, "Comment not found")
    c.resolved = not c.resolved
    c.resolved_by_email = user.email if c.resolved else None
    db.commit(); db.refresh(c)
    return _serialize_comment(c)


@router.delete("/{presentation_id}/comments/{comment_id}")
def delete_comment(presentation_id: str, comment_id: str,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from models.presentation import PresentationComment
    p = _get_viewable_or_404(db, presentation_id, user)
    c = (
        db.query(PresentationComment)
        .filter(PresentationComment.id == comment_id,
                PresentationComment.presentation_id == presentation_id)
        .first()
    )
    if not c:
        raise HTTPException(404, "Comment not found")
    # Only the comment author, the presentation owner, or an admin can delete.
    is_author = c.author_email and user.email and c.author_email.lower() == user.email.lower()
    is_owner = str(p.created_by) == str(user.id)
    if not (is_author or is_owner or user.is_admin):
        raise HTTPException(403, "You can only delete your own comments")
    db.delete(c); db.commit()
    return {"ok": True}


# ─── Sharing ────────────────────────────────────────────────────────────────

class ShareCreateBody(BaseModel):
    user_id: str
    role: Literal["viewer", "editor"] = "viewer"


@router.get("/{presentation_id}/shares")
def list_presentation_shares(presentation_id: str, db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    p = _get_viewable_or_404(db, presentation_id, user)
    rows = db.execute(sql_text("""
        SELECT u.id, u.email, u.full_name, ps.role
        FROM presentation_shares ps
        JOIN users u ON u.id = ps.user_id
        WHERE ps.presentation_id = :pid
        ORDER BY u.email
    """), {"pid": str(p.id)}).fetchall()
    return [
        {"user_id": str(r[0]), "email": r[1], "full_name": r[2], "role": r[3]}
        for r in rows
    ]


@router.post("/{presentation_id}/shares")
def add_presentation_share(presentation_id: str, body: ShareCreateBody,
                           db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    p = _get_or_404(db, presentation_id)
    require_owner_or_admin(p.created_by, user)
    if str(body.user_id) == str(p.created_by):
        raise HTTPException(400, "Owner already has full access")
    target = db.query(User).filter(User.id == body.user_id).first()
    if not target:
        raise HTTPException(404, "User not found")
    db.execute(sql_text("""
        INSERT INTO presentation_shares (presentation_id, user_id, role)
        VALUES (:pid, :uid, :role)
        ON CONFLICT (presentation_id, user_id) DO UPDATE SET role = EXCLUDED.role
    """), {"pid": str(p.id), "uid": str(body.user_id), "role": body.role})
    db.commit()
    return {
        "user_id": str(target.id),
        "email": target.email,
        "full_name": target.full_name,
        "role": body.role,
    }


@router.delete("/{presentation_id}/shares/{user_id}")
def remove_presentation_share(presentation_id: str, user_id: str,
                              db: Session = Depends(get_db),
                              user: User = Depends(get_current_user)):
    p = _get_or_404(db, presentation_id)
    require_owner_or_admin(p.created_by, user)
    db.execute(sql_text(
        "DELETE FROM presentation_shares "
        "WHERE presentation_id = :pid AND user_id = :uid"
    ), {"pid": str(p.id), "uid": str(user_id)})
    db.commit()
    return {"ok": True}


# ─── Public viewer ──────────────────────────────────────────────────────────

@router.get("/public/{slug}/meta")
def public_meta(slug: str, db: Session = Depends(get_db)):
    p = db.query(Presentation).filter(Presentation.public_slug == slug).first()
    if not p or p.status != "published":
        raise HTTPException(404, "Not found")
    return {
        "title": p.title,
        "has_password": bool(p.public_password_hash),
    }


@router.get("/public/{slug}")
def public_view(slug: str,
                x_deck_password: Optional[str] = Header(None, alias="X-Deck-Password"),
                db: Session = Depends(get_db)):
    """Serve the public deck. Reads from the current PresentationVersion
    snapshot when one exists; falls back to the parent presentation's
    live sections for back-compat with pre-versioning decks."""
    from services.version_publisher import get_current_public_version
    p = db.query(Presentation).filter(Presentation.public_slug == slug).first()
    if not p or p.status != "published":
        raise HTTPException(404, "Not found")
    if p.public_password_hash:
        if not x_deck_password or not bcrypt.checkpw(
            x_deck_password.encode(), p.public_password_hash.encode()
        ):
            raise HTTPException(401, "Password required or incorrect")

    v = get_current_public_version(db, p)
    if v is not None:
        return {
            "title": v.title,
            "sections": v.sections or [],
            "attachments": v.attachments or [],
            "theme": p.theme or {},
            "disclosure": v.disclosure,
            "published_at": v.published_at.isoformat() if v.published_at else None,
            "version_no": v.version_no,
        }
    return {
        "title": p.title,
        "sections": p.sections or [],
        "attachments": p.attachments or [],
        "theme": p.theme or {},
        "disclosure": getattr(p, "disclosure", None),
        "published_at": p.published_at.isoformat() if p.published_at else None,
        "version_no": None,
    }


@router.get("/public/{slug}/citation")
def public_citation_view(
    slug: str,
    filename: str = Query(...),
    x_deck_password: Optional[str] = Header(None, alias="X-Deck-Password"),
    db: Session = Depends(get_db),
):
    """Resolve a [source: filename.pdf] citation in a published deck to a
    short-lived signed S3 URL. Only documents whose filename appears as a
    citation in this deck (or as an attachment) can be resolved — the slug
    acts as the access token."""
    from models.document import Document
    from services.s3_service import S3Service
    import re as _re

    p = db.query(Presentation).filter(Presentation.public_slug == slug).first()
    if not p or p.status != "published":
        raise HTTPException(404, "Not found")
    if p.public_password_hash:
        if not x_deck_password or not bcrypt.checkpw(
            x_deck_password.encode(), p.public_password_hash.encode()
        ):
            raise HTTPException(401, "Password required or incorrect")

    # Build the set of filenames cited anywhere in the deck (sections + attachments).
    allowed: set[str] = set()
    for s in (p.sections or []):
        body = (s.get("body") or "")
        for m in _re.findall(r"\[source:\s*([^\]]+)\]", body):
            for fn in _re.split(r"\s*[,|;]\s*", m):
                fn = fn.strip()
                if fn:
                    allowed.add(fn)
    for att in (p.attachments or []):
        if att.get("filename"):
            allowed.add(att["filename"])

    if filename not in allowed:
        raise HTTPException(404, "Citation not in this deck")

    doc = db.query(Document).filter(Document.filename == filename).first()
    if not doc or not doc.s3_key:
        raise HTTPException(404, "Document not found")

    s3 = S3Service()
    url = s3.get_presigned_url(doc.s3_key, expires_in=900)
    return {"url": url, "filename": doc.filename}


# ─── Versions / publish history ─────────────────────────────────────────────

@router.get("/{presentation_id}/versions")
def list_versions(presentation_id: str,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Return the publish history of a deck, newest first. Each entry has
    the bits the editor's Versions panel needs (no full content blob —
    clients fetch a single version's body from /versions/{n})."""
    from models.presentation_version import PresentationVersion
    p = _get_viewable_or_404(db, presentation_id, user)
    rows = (
        db.query(PresentationVersion)
        .filter(PresentationVersion.presentation_id == p.id)
        .order_by(PresentationVersion.version_no.desc())
        .all()
    )
    return {
        "versions": [
            {
                "version_no": v.version_no,
                "title": v.title,
                "published_at": v.published_at.isoformat() if v.published_at else None,
                "published_by": v.published_by,
                "is_current_public": v.is_current_public,
                "rolled_back_from_version_no": v.rolled_back_from_version_no,
                "section_count": len(v.sections or []),
                "doc_snapshot_count": len(v.doc_snapshots or {}),
            }
            for v in rows
        ],
    }


@router.get("/{presentation_id}/versions/{version_no}")
def get_version(presentation_id: str, version_no: int,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """Full snapshot of a specific version — used by the
    Preview-this-version button in the editor's Versions panel."""
    from models.presentation_version import PresentationVersion
    p = _get_viewable_or_404(db, presentation_id, user)
    v = (
        db.query(PresentationVersion)
        .filter(PresentationVersion.presentation_id == p.id,
                PresentationVersion.version_no == version_no)
        .first()
    )
    if not v:
        raise HTTPException(404, "Version not found")
    return {
        "version_no": v.version_no,
        "title": v.title,
        "sections": v.sections or [],
        "attachments": v.attachments or [],
        "disclosure": v.disclosure,
        "doc_snapshots": v.doc_snapshots or {},
        "is_current_public": v.is_current_public,
        "published_at": v.published_at.isoformat() if v.published_at else None,
        "published_by": v.published_by,
        "rolled_back_from_version_no": v.rolled_back_from_version_no,
    }


@router.post("/{presentation_id}/rollback-to/{version_no}")
def rollback_to_version(presentation_id: str, version_no: int,
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Make an older version the current public version by REPLACING the
    draft with that older snapshot AND re-publishing it. This creates a
    new version row whose content matches the target — linear history is
    preserved, nothing in `presentation_versions` is mutated."""
    from models.presentation_version import PresentationVersion
    from services.version_publisher import publish_new_version
    from sqlalchemy.orm.attributes import flag_modified
    import copy as _copy

    p = _get_or_404(db, presentation_id)
    require_owner_or_admin(p.created_by, user)
    target = (
        db.query(PresentationVersion)
        .filter(PresentationVersion.presentation_id == p.id,
                PresentationVersion.version_no == version_no)
        .first()
    )
    if not target:
        raise HTTPException(404, "Version not found")

    p.title = target.title
    p.sections = _copy.deepcopy(target.sections or [])
    p.attachments = _copy.deepcopy(target.attachments or [])
    if hasattr(p, "disclosure"):
        p.disclosure = _copy.deepcopy(target.disclosure) if target.disclosure else None
        if p.disclosure is not None:
            flag_modified(p, "disclosure")
    flag_modified(p, "sections")
    flag_modified(p, "attachments")

    user_id = getattr(user, "email", None) or str(user.id)
    new_v = publish_new_version(p, db, user_id=user_id, rolled_back_from=version_no)
    db.commit()
    return {
        "new_version_no": new_v.version_no,
        "rolled_back_from_version_no": version_no,
        "is_current_public": True,
    }


@router.get("/{presentation_id}/changes-since-publish")
def changes_since_publish(presentation_id: str,
                          db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """Cheap structural diff for the editor's "X unpublished changes"
    badge. Compares draft (presentations.sections) against the row where
    is_current_public=True."""
    from services.version_publisher import get_current_public_version, diff_summary
    p = _get_viewable_or_404(db, presentation_id, user)
    cur = get_current_public_version(db, p)
    return diff_summary(p, cur)


# ─── Citation audit (draft-only — public viewer is untouched) ───────────────

@router.get("/{presentation_id}/audit-citations")
def audit_citations(presentation_id: str,
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """For every [DOC:id|label] token in the deck draft, return the token
    id, the label the AI wrote, and the actual filename of the document
    at that PK. Lets the operator spot citations the model hallucinated
    wrong ids for (chip label says "Master PG List" but the file at PK is
    a TD Bank statement → mismatch_score ~0).

    Mismatch score is Jaccard similarity over tokenized words
    (label vs filename, lowercased, alphanumeric-only). 1.0 means the
    label and filename share the same key terms; 0.0 means no overlap.
    Below ~0.2 is almost certainly wrong.
    """
    import re as _re
    from models.document import Document
    p = _get_viewable_or_404(db, presentation_id, user)

    DOC_TOKEN_FULL = _re.compile(r"\[DOC:([A-Za-z0-9_\-]+)(?:\|([^\]]*))?\]")

    def _walk(blob):
        if isinstance(blob, str):
            yield blob
        elif isinstance(blob, dict):
            for v in blob.values():
                yield from _walk(v)
        elif isinstance(blob, list):
            for v in blob:
                yield from _walk(v)

    occurrences: dict[str, list[str]] = {}  # id -> labels seen
    for sec in (p.sections or []):
        for text_blob in _walk(sec):
            for m in DOC_TOKEN_FULL.finditer(text_blob):
                cid = m.group(1)
                label = (m.group(2) or "").strip()
                occurrences.setdefault(cid, [])
                if label and label not in occurrences[cid]:
                    occurrences[cid].append(label)

    def _tokens(s: str) -> set[str]:
        return {t for t in _re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) >= 2}

    rows: list[dict] = []
    for cid, labels in occurrences.items():
        # AH document IDs are UUIDs (string).
        doc = None
        try:
            doc = db.query(Document).filter(Document.id == cid).first()
        except Exception:
            doc = None
        filename = doc.filename if doc else None
        best_score = 0.0
        chosen_label = labels[0] if labels else None
        if filename and labels:
            f_tok = _tokens(filename)
            for lab in labels:
                l_tok = _tokens(lab)
                if not l_tok or not f_tok:
                    score = 0.0
                else:
                    score = len(l_tok & f_tok) / max(1, len(l_tok | f_tok))
                if score > best_score:
                    best_score = score
                    chosen_label = lab
        rows.append({
            "id": cid,
            "labels": labels,
            "label": chosen_label,
            "filename": filename,
            "found": bool(doc),
            "mismatch_score": round(best_score, 2),
            "looks_mismatched": bool(doc) and bool(labels) and best_score < 0.20,
            "size_bytes": doc.file_size if doc else None,
        })

    rows.sort(key=lambda r: (r["found"], r["mismatch_score"], r["id"]))
    return {
        "presentation_id": str(p.id),
        "total_citations": len(rows),
        "missing": sum(1 for r in rows if not r["found"]),
        "likely_mismatched": sum(1 for r in rows if r["looks_mismatched"]),
        "rows": rows,
    }


@router.post("/{presentation_id}/fix-citations")
def apply_citation_fixes(presentation_id: str,
                        body: dict,
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Apply per-citation fixes to the DRAFT.

    Body shape:
        {
          "fixes": [
            {"id": "<uuid>", "action": "strip"},
            {"id": "<uuid>", "action": "swap", "new_id": "<uuid>"}
          ]
        }

    Each entry rewrites every [DOC:<id>|label] occurrence:
        - swap → [DOC:<new_id>|label]
        - strip → label text only (chip dropped, prose preserved)

    Returns the number of token occurrences mutated. The public viewer is
    untouched until the operator republishes; this only edits the draft.
    """
    import re as _re
    from sqlalchemy.orm.attributes import flag_modified

    p = _get_editable_or_404(db, presentation_id, user)
    fixes = body.get("fixes") or []
    plan: dict[tuple, dict] = {}
    for f in fixes:
        if not isinstance(f, dict):
            continue
        cid = str(f.get("id") or "").strip()
        action = str(f.get("action") or "").strip()
        if not cid or action not in ("swap", "strip"):
            continue
        section_scope = str(f.get("section_id") or "").strip() or "*"
        if action == "swap":
            new_id = str(f.get("new_id") or "").strip()
            if not new_id:
                continue
            plan[(section_scope, cid)] = {"action": "swap", "to_id": new_id}
        else:
            plan[(section_scope, cid)] = {"action": "strip"}
    if not plan:
        return {"edits": 0, "sections_changed": 0}

    DOC_TOKEN = _re.compile(r"\[DOC:([A-Za-z0-9_\-]+)(?:\|([^\]]*))?\]")

    def _spec_for(section_id: str, doc_id: str):
        """Section-scoped fix wins over a deck-wide '*' fix."""
        return plan.get((section_id, doc_id)) or plan.get(("*", doc_id))

    def _rewrite_str(text_in: str, section_id: str) -> tuple[str, int]:
        out_parts: list[str] = []
        last = 0
        edits = 0
        for m in DOC_TOKEN.finditer(text_in):
            cid = m.group(1)
            label = (m.group(2) or "").strip()
            spec = _spec_for(section_id, cid)
            out_parts.append(text_in[last:m.start()])
            if spec and spec["action"] == "swap":
                new_id = spec["to_id"]
                out_parts.append(f"[DOC:{new_id}|{label}]" if label else f"[DOC:{new_id}]")
                edits += 1
            elif spec and spec["action"] == "strip":
                out_parts.append(label)
                edits += 1
            else:
                out_parts.append(m.group(0))
            last = m.end()
        out_parts.append(text_in[last:])
        return ("".join(out_parts), edits)

    def _rewrite_blob(b, section_id: str):
        if isinstance(b, str):
            return _rewrite_str(b, section_id)
        if isinstance(b, dict):
            out: dict = {}
            total = 0
            for k, v in b.items():
                nv, e = _rewrite_blob(v, section_id)
                total += e
                out[k] = nv
            return (out, total)
        if isinstance(b, list):
            out_l: list = []
            total = 0
            for v in b:
                nv, e = _rewrite_blob(v, section_id)
                total += e
                out_l.append(nv)
            return (out_l, total)
        return (b, 0)

    sections_changed = 0
    edits_total = 0
    new_sections = []
    for sec in (p.sections or []):
        sid = str(sec.get("id") or "")
        new_sec, e = _rewrite_blob(sec, sid)
        if e:
            sections_changed += 1
        edits_total += e
        new_sections.append(new_sec)
    if edits_total > 0:
        p.sections = new_sections
        flag_modified(p, "sections")
        db.commit()
    return {"edits": edits_total, "sections_changed": sections_changed}


# ─── Disclosure (public viewer modal) ───────────────────────────────────────

class DisclosureBody(BaseModel):
    enabled: bool = False
    is_draft: bool = False
    custom_text: Optional[str] = None


@router.put("/{presentation_id}/disclosure")
def set_disclosure(presentation_id: str, body: DisclosureBody,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Configure the first-visit disclosure modal shown on the public
    viewer. Edit-only; the public viewer reads disclosure from the
    current published version, so changes take effect on Republish."""
    from sqlalchemy.orm.attributes import flag_modified
    p = _get_editable_or_404(db, presentation_id, user)
    if not hasattr(p, "disclosure"):
        raise HTTPException(500, "disclosure column not provisioned yet — restart API")
    p.disclosure = {
        "enabled": bool(body.enabled),
        "is_draft": bool(body.is_draft),
        "custom_text": (body.custom_text or "").strip() or None,
    }
    flag_modified(p, "disclosure")
    db.commit(); db.refresh(p)
    return p.disclosure
