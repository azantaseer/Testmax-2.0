"""Projects. Hierarchy is Tenant > Project (no workspaces). Every query is tenant-scoped."""
import uuid

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, field_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user, scoped_select
from app.errors import AppError
from app.models import Project, ProjectMember, User

router = APIRouter(prefix="/api/projects")


def get_project(db: Session, user: User, project_id: uuid.UUID) -> Project:
    """A project in the caller's own tenant, or 404 (never reveals other tenants' projects)."""
    project = db.scalar(scoped_select(Project, user).where(Project.id == project_id))
    if project is None:
        raise AppError(404, "project_not_found", "Project not found.")
    return project


def _members(db: Session, project_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    out: dict[uuid.UUID, list[dict]] = {pid: [] for pid in project_ids}
    if not project_ids:
        return out
    rows = db.execute(
        select(ProjectMember.project_id, User.id, User.name)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id.in_(project_ids))
        .order_by(func.lower(User.name))
    ).all()
    for project_id, uid, name in rows:
        out[project_id].append({"id": str(uid), "name": name})
    return out


def _dict(p: Project, members: list[dict], user: User) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "description": p.description,
        "assigned_users": members,
        "created_by": str(p.created_by) if p.created_by else None,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
        "can_edit": user.role in ("super_admin", "site_admin") or p.created_by == user.id,
    }


def _check_members(db: Session, user: User, member_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    ids = list(dict.fromkeys(member_ids))
    if not ids:
        return ids
    found = set(db.scalars(scoped_select(User, user).where(User.id.in_(ids), User.status == "active").with_only_columns(User.id)))
    if len(found) != len(ids):
        raise AppError(400, "invalid_member", "Assigned users must be active members of your organization.")
    return ids


def _clean_name(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("Enter a project name.")
    if len(v) > 100:
        raise ValueError("Project name is too long (100 characters max).")
    return v


def _clean_desc(v: str) -> str:
    v = v.strip()
    if len(v) > 2000:
        raise ValueError("Description is too long (2000 characters max).")
    return v


class ProjectBody(BaseModel):
    name: str
    description: str = ""
    member_ids: list[uuid.UUID] = []

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean_name(v)

    @field_validator("description")
    @classmethod
    def _desc(cls, v: str) -> str:
        return _clean_desc(v)


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    member_ids: list[uuid.UUID] | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return None if v is None else _clean_name(v)

    @field_validator("description")
    @classmethod
    def _desc(cls, v):
        return None if v is None else _clean_desc(v)


@router.get("/assignable-users")
def assignable_users(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        scoped_select(User, user).where(User.status == "active").with_only_columns(User.id, User.name).order_by(func.lower(User.name))
    ).all()
    return {"users": [{"id": str(r.id), "name": r.name} for r in rows]}


@router.get("")
def list_projects(
    q: str = "",
    sort: str = "updated",
    order: str = "desc",
    page: int = 1,
    page_size: int = 10,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    stmt = scoped_select(Project, user)
    q = q.strip()
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(or_(Project.name.ilike(f"%{esc}%", escape="\\"), Project.description.ilike(f"%{esc}%", escape="\\")))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    key = func.lower(Project.name) if sort == "name" else Project.updated_at
    key = key.asc() if order == "asc" else key.desc()
    rows = db.scalars(stmt.order_by(key, Project.id).offset((page - 1) * page_size).limit(page_size)).all()
    members = _members(db, [p.id for p in rows])
    return {"projects": [_dict(p, members[p.id], user) for p in rows], "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=201)
def create_project(body: ProjectBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = _check_members(db, user, body.member_ids)
    project = Project(tenant_id=user.tenant_id, name=body.name, description=body.description, created_by=user.id)
    try:
        db.add(project)
        db.flush()
        db.add_all([ProjectMember(project_id=project.id, user_id=i) for i in ids])
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "project_exists", "A project with this name already exists.") from None
    return _dict(project, _members(db, [project.id])[project.id], user)


@router.get("/{project_id}")
def read_project(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = get_project(db, user, project_id)
    return _dict(p, _members(db, [p.id])[p.id], user)


@router.patch("/{project_id}")
def update_project(project_id: uuid.UUID, body: ProjectPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = get_project(db, user, project_id)
    if not _dict(p, [], user)["can_edit"]:
        raise AppError(403, "forbidden", "Only the project's creator or an admin can edit it.")
    try:
        if body.name is not None:
            p.name = body.name
        if body.description is not None:
            p.description = body.description
        if body.member_ids is not None:
            ids = _check_members(db, user, body.member_ids)
            db.execute(delete(ProjectMember).where(ProjectMember.project_id == p.id))
            db.add_all([ProjectMember(project_id=p.id, user_id=i) for i in ids])
        p.updated_at = func.now()
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "project_exists", "A project with this name already exists.") from None
    db.refresh(p)
    return _dict(p, _members(db, [p.id])[p.id], user)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = get_project(db, user, project_id)
    if not _dict(p, [], user)["can_edit"]:
        raise AppError(403, "forbidden", "Only the project's creator or an admin can delete it.")
    db.delete(p)
    db.commit()
    return Response(status_code=204)
