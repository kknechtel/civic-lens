"""
Chat: SSE endpoint, history and sessions.

The assistant itself lives in services/chat (provider-neutral agent loop,
tools, prompt, citation grounding). Which model runs and whose API key pays is
decided per request by services.ai (user key → sponsored deployment key →
"add your own key").

Routes:
  POST /api/chat/stream             — SSE chat
  GET  /api/chat/history?session_id — full message thread
  GET  /api/chat/sessions           — recent sessions
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from auth import get_current_user
from database import SessionLocal, get_db
from models.user import User
from services import ai
from services.chat import agent, prompt, tools as chat_tools

logger = logging.getLogger(__name__)
router = APIRouter()

# Kept as a module attribute for scripts (scripts/eval_search.py).
_exec_search_chunks = chat_tools._exec_search_chunks


# ─── Models ──────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    document_id: Optional[str] = None
    scope_type: str = "all"
    scope_id: Optional[str] = None
    model: str = "claude"
    web_search: bool = False
    deep_thinking: bool = False
    report_mode: bool = False
    # Deck-mode: when set, the chat is bound to a specific presentation. The
    # backend exposes a `propose_section` tool whose input is forwarded to
    # the frontend as a `proposal` SSE event for the user to accept/reject.
    presentation_id: Optional[str] = None
    presentation_summary: Optional[str] = None
    # Slide scope — when the operator picks a specific section in the chat's
    # section dropdown, this lands here and we focus the model on that section
    # (so propose_section defaults to editing it, not appending a new one).
    target_section_id: Optional[str] = None


@router.get("/history")
def get_chat_history(
    session_id: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Verify the session belongs to this user (or admin can read any).
    if not user.is_admin:
        owner = db.execute(sql_text(
            "SELECT user_id FROM chat_history WHERE session_id = :sid LIMIT 1"
        ), {"sid": session_id}).fetchone()
        if owner is None:
            return {"session_id": session_id, "messages": []}
        if owner[0] is None or str(owner[0]) != str(user.id):
            raise HTTPException(status_code=404, detail="Session not found")

    rows = db.execute(sql_text(
        "SELECT role, content, metadata, created_at FROM chat_history "
        "WHERE session_id = :sid ORDER BY created_at"
    ), {"sid": session_id}).fetchall()
    return {
        "session_id": session_id,
        "messages": [
            {"role": r.role, "content": r.content, "metadata": r.metadata,
             "timestamp": r.created_at.isoformat()}
            for r in rows
        ],
    }


@router.get("/sessions")
def list_chat_sessions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.is_admin:
        rows = db.execute(sql_text("""
            SELECT session_id, scope_type, scope_id,
                   count(*) AS message_count,
                   max(created_at) AS last_activity,
                   (SELECT content FROM chat_history ch2
                    WHERE ch2.session_id = ch.session_id AND ch2.role = 'user'
                    ORDER BY created_at DESC LIMIT 1) AS last_query
            FROM chat_history ch
            GROUP BY session_id, scope_type, scope_id
            ORDER BY max(created_at) DESC
            LIMIT 30
        """)).fetchall()
    else:
        rows = db.execute(sql_text("""
            SELECT session_id, scope_type, scope_id,
                   count(*) AS message_count,
                   max(created_at) AS last_activity,
                   (SELECT content FROM chat_history ch2
                    WHERE ch2.session_id = ch.session_id AND ch2.role = 'user'
                    ORDER BY created_at DESC LIMIT 1) AS last_query
            FROM chat_history ch
            WHERE user_id = :uid
            GROUP BY session_id, scope_type, scope_id
            ORDER BY max(created_at) DESC
            LIMIT 30
        """), {"uid": str(user.id)}).fetchall()
    return [
        {
            "session_id": r.session_id,
            "scope_type": r.scope_type,
            "scope_id": r.scope_id,
            "message_count": r.message_count,
            "last_activity": r.last_activity.isoformat() if r.last_activity else None,
            "last_query": (r.last_query[:120] if r.last_query else None),
        }
        for r in rows
    ]


def _save_message(db: Session, session_id: str, role: str, content: str,
                  scope_type: str = "all", scope_id: Optional[str] = None,
                  user_id: Optional[str] = None) -> None:
    db.execute(sql_text(
        "INSERT INTO chat_history (id, session_id, scope_type, scope_id, role, content, user_id) "
        "VALUES (:id, :sid, :st, :si, :role, :content, :uid)"
    ), {"id": str(uuid.uuid4()), "sid": session_id, "st": scope_type,
        "si": scope_id, "role": role, "content": content, "uid": user_id})
    db.commit()




def _feature(req: ChatRequest) -> str:
    if req.presentation_id:
        return "presentations"
    if req.deep_thinking or req.report_mode:
        return "deep_chat"
    return "document_chat" if req.document_id else "chat"


def _neutral_history(rows) -> list[dict]:
    """Stored turns → alternating user/assistant text (providers require alternation)."""
    out: list[dict] = []
    for r in rows:
        role = "assistant" if r.role == "assistant" else "user"
        if out and out[-1]["role"] == role:
            out[-1]["text"] += "\n\n" + (r.content or "")
        else:
            out.append({"role": role, "text": r.content or ""})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _run_tool(name: str, args: dict) -> dict:
    s = SessionLocal()
    try:
        return chat_tools._execute_tool(name, args, s)
    finally:
        s.close()


def _cost(route, stats: dict) -> float:
    """Anthropic bills cache reads at ~10% and cache writes at ~125% of input."""
    usd, _ = ai.estimate_cost(route.model, stats["input"], stats["output"])
    if route.provider == "anthropic":
        per_in, _e = ai.estimate_cost(route.model, 1_000_000, 0)
        usd += (stats["cache_read"] * 0.1 + stats["cache_write"] * 1.25) * per_in / 1_000_000
    return usd


def _sse_response(gen, session_id: str) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"X-Session-Id": session_id, "Cache-Control": "no-cache",
                                      "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@router.post("/stream")
async def chat_stream(req: ChatRequest, request: Request, user: User = Depends(get_current_user)):
    """SSE chat. Deliberately no request-scoped DB session: one held across a
    stream sits "idle in transaction" if the client disconnects. Each DB op
    opens and closes its own SessionLocal()."""
    session_id = req.session_id or str(uuid.uuid4())
    user_id = str(user.id)
    feature = _feature(req)

    try:
        route = ai.resolve(feature, user.id)
    except ai.NoAIAccess as exc:
        reason = exc.reason

        async def no_access():
            yield agent.sse("session", {"session_id": session_id})
            yield agent.sse("needs_key", {"feature": feature, "reason": reason, "settings_url": "/settings"})
            yield agent.sse("error", {"content": reason, "code": "needs_key"})
        return _sse_response(no_access(), session_id)

    db = SessionLocal()
    try:
        if req.session_id and not user.is_admin:
            owner = db.execute(sql_text("SELECT user_id FROM chat_history WHERE session_id = :sid LIMIT 1"),
                               {"sid": session_id}).fetchone()
            if owner is not None and owner[0] is not None and str(owner[0]) != user_id:
                raise HTTPException(status_code=404, detail="Session not found")
        doc_context = prompt._attached_doc_context(db, req.document_id)
        rows = db.execute(sql_text("SELECT role, content FROM chat_history WHERE session_id = :sid ORDER BY created_at"),
                          {"sid": session_id}).fetchall() if req.session_id else []
        _save_message(db, session_id, "user", req.query, req.scope_type, req.scope_id, user_id=user_id)
    finally:
        db.close()

    stable, variable = prompt._split_system_parts(req, doc_context)
    tool_defs = chat_tools._tool_defs()
    if req.web_search:
        tool_defs.append(chat_tools._web_search_tool())
    if req.presentation_id:
        tool_defs.append(chat_tools._propose_section_tool())

    def on_done(text: str, stats: dict) -> float:
        from services.usage import record_usage
        usd = _cost(route, stats)
        s = SessionLocal()
        try:
            _save_message(s, session_id, "assistant", text, req.scope_type, req.scope_id, user_id=user_id)
            record_usage(s, source=feature, model=route.model, input_tokens=stats["input"],
                         output_tokens=stats["output"], estimated_cost_usd=usd, user_id=user_id,
                         resource_type="chat_session", resource_id=session_id, payer=route.payer,
                         provider=route.provider, metadata={k: stats[k] for k in (
                             "tool_calls_made", "turns", "cache_read", "cache_write", "unverified_citations")})
        finally:
            s.close()
        return usd

    ctx = agent.ChatContext(
        route=route, system=[stable, variable], history=_neutral_history(rows), query=req.query, tools=tool_defs,
        execute=_run_tool, describe=chat_tools._describe_tool_call, summarize=chat_tools._summarize_tool_result,
        fit=chat_tools._fit_json, session_id=session_id,
        max_tokens=8000 if feature in ("deep_chat", "presentations") else 4000,
        on_done=on_done, is_disconnected=request.is_disconnected,
    )

    async def stream():
        yield agent.sse("session", {"session_id": session_id})
        async for chunk in agent.run(ctx):
            yield chunk

    return _sse_response(stream(), session_id)
