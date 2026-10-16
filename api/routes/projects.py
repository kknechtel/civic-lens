"""Project management routes.

Each project is owned by its creator. List/get/update/delete are scoped to
owner ∪ explicit shares ∪ admin. Sharing is managed via /share endpoints.
"""
from typing import List, Literal, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
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
from database import get_db
from models.document import Project
from models.user import User

router = APIRouter()


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    entity_type: str | None = None  # "town", "school", "general"


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str | None
    entity_type: str | None
    document_count: int = 0
    created_at: str
    is_owner: bool = True
    share_role: str | None = None  # 'viewer' | 'editor' | None (when is_owner)

    class Config:
        from_attributes = True


class ShareCreate(BaseModel):
    user_id: str
    role: Literal["viewer", "editor"] = "viewer"


class ShareResponse(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    role: str


def _serialize(p: Project, user: User, role: Optional[str] = None) -> ProjectResponse:
    is_owner = str(p.created_by) == str(user.id)
    return ProjectResponse(
        id=str(p.id),
        name=p.name,
        description=p.description,
        entity_type=p.entity_type,
        document_count=len(p.documents),
        created_at=p.created_at.isoformat(),
        is_owner=is_owner,
        share_role=None if is_owner else role,
    )


@router.get("", response_model=List[ProjectResponse], include_in_schema=False)
@router.get("/", response_model=List[ProjectResponse])
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(Project)
    if not user.is_admin:
        shared_ids = shared_resource_ids(db, "projects", user.id)
        if shared_ids:
            q = q.filter(or_(Project.created_by == user.id, Project.id.in_(shared_ids)))
        else:
            q = q.filter(Project.created_by == user.id)
    projects = q.all()
    # Batch-fetch share roles for the non-owned projects
    role_by_id: dict[str, str] = {}
    if not user.is_admin:
        rows = db.execute(sql_text(
            "SELECT project_id, role FROM project_shares WHERE user_id = :uid"
        ), {"uid": str(user.id)}).fetchall()
        role_by_id = {str(r[0]): r[1] for r in rows}
    return [_serialize(p, user, role_by_id.get(str(p.id))) for p in projects]


@router.post("", response_model=ProjectResponse, include_in_schema=False)
@router.post("/", response_model=ProjectResponse)
def create_project(
    req: ProjectCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = Project(
        name=req.name,
        description=req.description,
        entity_type=req.entity_type,
        created_by=user.id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _serialize(project, user)


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    require_view(db, "projects", project.created_by, project.id, user)
    role = user_share_role(db, "projects", project.id, user.id)
    return _serialize(project, user, role)


@router.delete("/{project_id}")
def delete_project(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    require_owner_or_admin(project.created_by, user)
    db.delete(project)
    db.commit()
    return {"detail": "Project deleted"}


# ─── Sharing ────────────────────────────────────────────────────────────────

@router.get("/{project_id}/shares", response_model=List[ShareResponse])
def list_project_shares(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    require_view(db, "projects", project.created_by, project.id, user)
    rows = db.execute(sql_text("""
        SELECT u.id, u.email, u.full_name, ps.role
        FROM project_shares ps
        JOIN users u ON u.id = ps.user_id
        WHERE ps.project_id = :pid
        ORDER BY u.email
    """), {"pid": str(project_id)}).fetchall()
    return [
        ShareResponse(user_id=str(r[0]), email=r[1], full_name=r[2], role=r[3])
        for r in rows
    ]


@router.post("/{project_id}/shares", response_model=ShareResponse)
def add_project_share(
    project_id: UUID,
    body: ShareCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    require_owner_or_admin(project.created_by, user)
    if str(body.user_id) == str(project.created_by):
        raise HTTPException(status_code=400, detail="Owner already has full access")
    target = db.query(User).filter(User.id == body.user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Upsert
    db.execute(sql_text("""
        INSERT INTO project_shares (project_id, user_id, role)
        VALUES (:pid, :uid, :role)
        ON CONFLICT (project_id, user_id) DO UPDATE SET role = EXCLUDED.role
    """), {"pid": str(project_id), "uid": str(body.user_id), "role": body.role})
    db.commit()
    return ShareResponse(
        user_id=str(target.id),
        email=target.email,
        full_name=target.full_name,
        role=body.role,
    )


@router.delete("/{project_id}/shares/{user_id}")
def remove_project_share(
    project_id: UUID,
    user_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    require_owner_or_admin(project.created_by, user)
    db.execute(sql_text(
        "DELETE FROM project_shares WHERE project_id = :pid AND user_id = :uid"
    ), {"pid": str(project_id), "uid": str(user_id)})
    db.commit()
    return {"detail": "Share removed"}
