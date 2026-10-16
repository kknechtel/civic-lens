"""Admin routes - user management, invites, system stats."""
import secrets
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, text as sql_text
from sqlalchemy.orm import Session

from database import get_db
from models.user import User, InviteToken
from models.document import Document, Project
from models.financial import FinancialStatement
from auth import get_admin_user

router = APIRouter()


# ── Response Models ──────────────────────────────────────────────────────────

class AdminStatsResponse(BaseModel):
    total_users: int
    total_projects: int
    total_documents: int
    total_statements: int
    pending_users: int
    # Corpus-health: counts derived from existing columns. Resilient to
    # missing pgvector — falls back to 0 when the column doesn't exist.
    documents_ocrd: int = 0
    documents_vector_indexed: int = 0
    # Cost rollups from llm_usage. Both tracked since launch (the table
    # only exists after the new migration runs).
    cost_last_30d_usd: float = 0.0
    cost_total_usd: float = 0.0
    llm_calls_last_30d: int = 0


class AdminUserResponse(BaseModel):
    id: str
    email: str
    username: str
    full_name: str | None
    is_active: bool
    is_admin: bool
    created_at: str

    class Config:
        from_attributes = True


class InviteRequest(BaseModel):
    email: str | None = None  # Lock invite to a specific email, or None for open invite
    expires_hours: int = 72


class InviteResponse(BaseModel):
    token: str
    invite_url: str
    email: str | None
    expires_at: str


class InviteListResponse(BaseModel):
    id: str
    token: str
    email: str | None
    is_used: bool
    used_by: str | None
    expires_at: str
    created_at: str


# ── Stats ────────────────────────────────────────────────────────────────────

@router.get("/stats", response_model=AdminStatsResponse)
def get_stats(db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    # Documents OCR'd: extracted_text non-empty
    try:
        ocrd = db.execute(sql_text(
            "SELECT count(*) FROM documents WHERE COALESCE(LENGTH(extracted_text), 0) > 0"
        )).scalar() or 0
    except Exception:
        ocrd = 0
    # Vector-indexed: doc embedding present AND at least one embedded chunk.
    # Wrap in try since the embedding column doesn't exist when pgvector is
    # unavailable (degraded mode).
    try:
        vec_indexed = db.execute(sql_text("""
            SELECT count(DISTINCT d.id)
            FROM documents d
            JOIN document_chunks c ON c.document_id = d.id
            WHERE d.embedding IS NOT NULL AND c.embedding IS NOT NULL
        """)).scalar() or 0
    except Exception:
        vec_indexed = 0
    # Cost rollups. llm_usage may not exist yet if startup migration hasn't run.
    try:
        cost_30 = db.execute(sql_text("""
            SELECT COALESCE(SUM(estimated_cost_usd), 0)
            FROM llm_usage
            WHERE created_at >= now() - interval '30 days'
        """)).scalar() or 0
        calls_30 = db.execute(sql_text("""
            SELECT count(*) FROM llm_usage
            WHERE created_at >= now() - interval '30 days'
        """)).scalar() or 0
        cost_total = db.execute(sql_text(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM llm_usage"
        )).scalar() or 0
    except Exception:
        cost_30 = calls_30 = cost_total = 0

    return AdminStatsResponse(
        total_users=db.query(User).count(),
        total_projects=db.query(Project).count(),
        total_documents=db.query(Document).count(),
        total_statements=db.query(FinancialStatement).count(),
        pending_users=db.query(User).filter(User.is_active == False).count(),
        documents_ocrd=int(ocrd),
        documents_vector_indexed=int(vec_indexed),
        cost_last_30d_usd=float(cost_30),
        cost_total_usd=float(cost_total),
        llm_calls_last_30d=int(calls_30),
    )


# ── User Management ─────────────────────────────────────────────────────────

@router.get("/users", response_model=List[AdminUserResponse])
def list_users(db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [
        AdminUserResponse(
            id=str(u.id),
            email=u.email,
            username=u.username,
            full_name=u.full_name,
            is_active=u.is_active,
            is_admin=u.is_admin,
            created_at=u.created_at.isoformat(),
        )
        for u in users
    ]


@router.patch("/users/{user_id}/approve")
def approve_user(user_id: str, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    """Approve a pending user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = True
    db.commit()
    return {"id": str(user.id), "is_active": True}


@router.patch("/users/{user_id}/toggle-active")
def toggle_user_active(user_id: str, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = not user.is_active
    db.commit()
    return {"id": str(user.id), "is_active": user.is_active}


@router.patch("/users/{user_id}/toggle-admin")
def toggle_user_admin(user_id: str, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if str(user.id) == str(admin.id):
        raise HTTPException(status_code=400, detail="Cannot change your own admin status")
    user.is_admin = not user.is_admin
    db.commit()
    return {"id": str(user.id), "is_admin": user.is_admin}


@router.delete("/users/{user_id}")
def delete_user(user_id: str, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if str(user.id) == str(admin.id):
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    db.delete(user)
    db.commit()
    return {"deleted": True}


# ── Invite Links ─────────────────────────────────────────────────────────────

@router.post("/invites", response_model=InviteResponse)
def create_invite(
    req: InviteRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Generate an invite link. Optionally lock to a specific email."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=req.expires_hours)

    invite = InviteToken(
        token=token,
        email=req.email.lower() if req.email else None,
        created_by=admin.id,
        expires_at=expires_at,
    )
    db.add(invite)
    db.commit()

    # Build the invite URL — frontend will read the ?invite= param
    from municipality import get_municipality
    base_url = get_municipality().branding.public_origin.rstrip("/")
    invite_url = f"{base_url}?invite={token}"

    return InviteResponse(
        token=token,
        invite_url=invite_url,
        email=req.email,
        expires_at=expires_at.isoformat(),
    )


@router.get("/invites", response_model=List[InviteListResponse])
def list_invites(db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    invites = db.query(InviteToken).order_by(InviteToken.created_at.desc()).limit(50).all()
    return [
        InviteListResponse(
            id=str(i.id),
            token=i.token,
            email=i.email,
            is_used=i.is_used,
            used_by=str(i.used_by) if i.used_by else None,
            expires_at=i.expires_at.isoformat(),
            created_at=i.created_at.isoformat(),
        )
        for i in invites
    ]


@router.delete("/invites/{invite_id}")
def delete_invite(invite_id: str, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    invite = db.query(InviteToken).filter(InviteToken.id == invite_id).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    db.delete(invite)
    db.commit()
    return {"deleted": True}


# ── Documents (corpus health) ───────────────────────────────────────────────

class AdminDocumentRow(BaseModel):
    id: str
    filename: str
    project_id: str | None
    project_name: str | None
    doc_type: str | None
    fiscal_year: str | None
    status: str
    file_size: int
    page_count: int | None
    is_ocrd: bool
    ocr_chars: int
    is_vector_indexed: bool
    chunk_count: int
    embedded_chunk_count: int
    uploaded_by: str | None
    uploaded_by_email: str | None
    created_at: str


@router.get("/documents", response_model=List[AdminDocumentRow])
def list_admin_documents(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    search: str | None = Query(None, description="Substring match on filename"),
    project_id: str | None = Query(None),
    has_ocr: str | None = Query(None, description="'yes' / 'no'"),
    has_vector: str | None = Query(None, description="'yes' / 'no'"),
    limit: int = Query(500, le=2000),
    offset: int = Query(0, ge=0),
):
    """List every document with derived OCR + vector-index flags. The flags
    are derived (not stored): a doc is OCR'd if `extracted_text` has content,
    vector-indexed if it has both an embedding and at least one chunk with
    an embedding."""
    # Detect degraded mode (no pgvector → embedding columns may not exist).
    has_doc_embedding = bool(db.execute(sql_text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'documents' AND column_name = 'embedding'"
    )).fetchone())
    has_chunk_embedding = bool(db.execute(sql_text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'document_chunks' AND column_name = 'embedding'"
    )).fetchone())

    doc_embedded_expr = "(d.embedding IS NOT NULL)" if has_doc_embedding else "FALSE"
    chunk_embed_filter = (
        "count(*) FILTER (WHERE embedding IS NOT NULL)"
        if has_chunk_embedding else "0"
    )

    rows = db.execute(sql_text(f"""
        SELECT
            d.id::text AS id,
            d.filename,
            d.project_id::text AS project_id,
            p.name AS project_name,
            d.doc_type, d.fiscal_year, d.status, d.file_size, d.page_count,
            COALESCE(LENGTH(d.extracted_text), 0) AS ocr_chars,
            {doc_embedded_expr} AS doc_embedded,
            COALESCE(c.total, 0) AS chunk_count,
            COALESCE(c.embedded, 0) AS embedded_chunk_count,
            d.uploaded_by::text AS uploaded_by,
            u.email AS uploaded_by_email,
            d.created_at
        FROM documents d
        LEFT JOIN projects p ON p.id = d.project_id
        LEFT JOIN users u ON u.id = d.uploaded_by
        LEFT JOIN (
            SELECT document_id,
                   count(*) AS total,
                   {chunk_embed_filter} AS embedded
            FROM document_chunks
            GROUP BY document_id
        ) c ON c.document_id = d.id
        WHERE
            (:search IS NULL OR d.filename ILIKE '%' || :search || '%')
            AND (:project_id IS NULL OR d.project_id::text = :project_id)
        ORDER BY d.created_at DESC
        LIMIT :limit OFFSET :offset
    """), {
        "search": search, "project_id": project_id,
        "limit": limit, "offset": offset,
    }).fetchall()

    out: list[AdminDocumentRow] = []
    for r in rows:
        is_ocrd = (r.ocr_chars or 0) > 0
        # "Vector-indexed" means the doc itself has an embedding AND at least
        # one chunk has one. Either alone is incomplete — chat tools rely on
        # both halves of the hybrid pipeline.
        is_vec = bool(r.doc_embedded) and (r.embedded_chunk_count or 0) > 0

        if has_ocr == "yes" and not is_ocrd: continue
        if has_ocr == "no" and is_ocrd: continue
        if has_vector == "yes" and not is_vec: continue
        if has_vector == "no" and is_vec: continue

        out.append(AdminDocumentRow(
            id=r.id, filename=r.filename,
            project_id=r.project_id, project_name=r.project_name,
            doc_type=r.doc_type, fiscal_year=r.fiscal_year,
            status=r.status, file_size=int(r.file_size or 0),
            page_count=r.page_count,
            is_ocrd=is_ocrd, ocr_chars=int(r.ocr_chars or 0),
            is_vector_indexed=is_vec,
            chunk_count=int(r.chunk_count or 0),
            embedded_chunk_count=int(r.embedded_chunk_count or 0),
            uploaded_by=r.uploaded_by,
            uploaded_by_email=r.uploaded_by_email,
            created_at=r.created_at.isoformat() if r.created_at else "",
        ))
    return out


# ── Cost tracker ────────────────────────────────────────────────────────────

class UsageSummaryResponse(BaseModel):
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_calls: int
    by_source: list[dict]    # [{source, cost, input_tokens, output_tokens, calls}]
    by_model: list[dict]
    by_user: list[dict]      # [{user_id, email, cost, calls}]
    daily: list[dict]        # last 30 days [{date, cost, calls}]


@router.get("/usage/summary", response_model=UsageSummaryResponse)
def usage_summary(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    days: int = Query(30, ge=1, le=365),
):
    """Aggregated usage for the last N days. One round-trip, four GROUP BYs."""
    since = datetime.utcnow() - timedelta(days=days)
    base_params = {"since": since}

    totals = db.execute(sql_text("""
        SELECT
            COALESCE(SUM(estimated_cost_usd), 0) AS cost,
            COALESCE(SUM(input_tokens), 0) AS in_t,
            COALESCE(SUM(output_tokens), 0) AS out_t,
            count(*) AS calls
        FROM llm_usage
        WHERE created_at >= :since
    """), base_params).fetchone()

    by_source = db.execute(sql_text("""
        SELECT source,
               COALESCE(SUM(estimated_cost_usd), 0) AS cost,
               COALESCE(SUM(input_tokens), 0) AS in_t,
               COALESCE(SUM(output_tokens), 0) AS out_t,
               count(*) AS calls
        FROM llm_usage
        WHERE created_at >= :since
        GROUP BY source
        ORDER BY cost DESC
    """), base_params).fetchall()

    by_model = db.execute(sql_text("""
        SELECT model,
               COALESCE(SUM(estimated_cost_usd), 0) AS cost,
               COALESCE(SUM(input_tokens), 0) AS in_t,
               COALESCE(SUM(output_tokens), 0) AS out_t,
               count(*) AS calls
        FROM llm_usage
        WHERE created_at >= :since
        GROUP BY model
        ORDER BY cost DESC
    """), base_params).fetchall()

    by_user = db.execute(sql_text("""
        SELECT u.id::text AS user_id, u.email,
               COALESCE(SUM(lu.estimated_cost_usd), 0) AS cost,
               count(*) AS calls
        FROM llm_usage lu
        LEFT JOIN users u ON u.id = lu.user_id
        WHERE lu.created_at >= :since
        GROUP BY u.id, u.email
        ORDER BY cost DESC
        LIMIT 50
    """), base_params).fetchall()

    daily = db.execute(sql_text("""
        SELECT date_trunc('day', created_at)::date AS d,
               COALESCE(SUM(estimated_cost_usd), 0) AS cost,
               count(*) AS calls
        FROM llm_usage
        WHERE created_at >= :since
        GROUP BY d
        ORDER BY d
    """), base_params).fetchall()

    return UsageSummaryResponse(
        total_cost_usd=float(totals.cost or 0),
        total_input_tokens=int(totals.in_t or 0),
        total_output_tokens=int(totals.out_t or 0),
        total_calls=int(totals.calls or 0),
        by_source=[
            {"source": r.source, "cost": float(r.cost or 0),
             "input_tokens": int(r.in_t or 0), "output_tokens": int(r.out_t or 0),
             "calls": int(r.calls or 0)}
            for r in by_source
        ],
        by_model=[
            {"model": r.model, "cost": float(r.cost or 0),
             "input_tokens": int(r.in_t or 0), "output_tokens": int(r.out_t or 0),
             "calls": int(r.calls or 0)}
            for r in by_model
        ],
        by_user=[
            {"user_id": r.user_id, "email": r.email or "(system)",
             "cost": float(r.cost or 0), "calls": int(r.calls or 0)}
            for r in by_user
        ],
        daily=[
            {"date": r.d.isoformat(), "cost": float(r.cost or 0),
             "calls": int(r.calls or 0)}
            for r in daily
        ],
    )


class UsageRowResponse(BaseModel):
    id: str
    source: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    user_email: str | None
    resource_type: str | None
    resource_id: str | None
    created_at: str


@router.get("/usage", response_model=List[UsageRowResponse])
def list_usage_rows(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    source: str | None = Query(None),
    model: str | None = Query(None),
    user_id: str | None = Query(None),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(200, le=1000),
    offset: int = Query(0, ge=0),
):
    """Paginated raw usage rows for drill-down."""
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(sql_text("""
        SELECT lu.id::text AS id, lu.source, lu.model,
               lu.input_tokens, lu.output_tokens, lu.estimated_cost_usd,
               u.email AS user_email,
               lu.resource_type, lu.resource_id, lu.created_at
        FROM llm_usage lu
        LEFT JOIN users u ON u.id = lu.user_id
        WHERE lu.created_at >= :since
          AND (:source IS NULL OR lu.source = :source)
          AND (:model IS NULL OR lu.model = :model)
          AND (:uid IS NULL OR lu.user_id::text = :uid)
        ORDER BY lu.created_at DESC
        LIMIT :limit OFFSET :offset
    """), {
        "since": since, "source": source, "model": model, "uid": user_id,
        "limit": limit, "offset": offset,
    }).fetchall()
    return [
        UsageRowResponse(
            id=r.id, source=r.source, model=r.model,
            input_tokens=int(r.input_tokens or 0),
            output_tokens=int(r.output_tokens or 0),
            estimated_cost_usd=float(r.estimated_cost_usd or 0),
            user_email=r.user_email, resource_type=r.resource_type,
            resource_id=r.resource_id,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
    ]


# Phase-2 instrumentation status: chat / OCR / Voyage embeddings / deck AI
# (chat, edit, fact-check, from-chat) / OPRA / reports / financial extraction
# (v1, v2, agent) / document_processor are all instrumented.
#
# Still NOT instrumented:
#   - api/scripts/extract_*.py, enrich_all.py — batch CLI jobs run outside
#     the API. Each is a 5-line addition to call record_usage with user_id=None.
