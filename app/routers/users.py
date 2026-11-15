"""Tenant user management. Every query is scoped to the caller's tenant."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, field_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai_settings import effective_ai
from app.db import get_db
from app.deps import current_user, scoped_select
from app.errors import AppError
from app.models import Tenant, User, UserSession
from app.security import hash_password
from app.validation import check_email, check_name, check_password

router = APIRouter(prefix="/api/users")

Role = Literal["super_admin", "site_admin", "user"]


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role not in ("super_admin", "site_admin"):
        raise AppError(403, "forbidden", "Only organization admins can manage users.")
    return user


def _user_dict(u: User, tenant: Tenant) -> dict:
    ai = effective_ai(u, tenant)
    return {
        "id": str(u.id),
        "name": u.name,
        "email": u.email,
        "role": u.role,
        "status": u.status,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "ai": {"provider": ai.provider, "model": ai.model, "source": ai.source},
    }


def _load_target(db: Session, actor: User, user_id: uuid.UUID) -> User:
    """Another user in the actor's own tenant, or 404 (never reveals other tenants' users)."""
    target = db.scalar(scoped_select(User, actor).where(User.id == user_id))
    if target is None:
        raise AppError(404, "user_not_found", "User not found.")
    return target


def _can_manage(actor: User, target: User) -> None:
    """Super Admin manages everyone; Site Admin manages only plain users."""
    if actor.role == "super_admin":
        return
    if target.role != "user":
        raise AppError(403, "forbidden", "Only a Super Admin can manage admins.")


def _active_super_admins(db: Session, actor: User) -> int:
    return db.scalar(
        select(func.count()).select_from(User).where(
            User.tenant_id == actor.tenant_id, User.role == "super_admin", User.status == "active"
        )
    )


def _end_sessions(db: Session, user_id: uuid.UUID) -> None:
    db.execute(delete(UserSession).where(UserSession.user_id == user_id))


class CreateUser(BaseModel):
    name: str
    email: str
    password: str
    role: Role = "user"

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return check_name(v)

    @field_validator("email")
    @classmethod
    def _e(cls, v):
        return check_email(v)

    @field_validator("password")
    @classmethod
    def _p(cls, v):
        return check_password(v)


class UpdateUser(BaseModel):
    name: str | None = None
    role: Role | None = None
    status: Literal["active", "disabled"] | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return None if v is None else check_name(v)


class ResetPassword(BaseModel):
    password: str

    @field_validator("password")
    @classmethod
    def _p(cls, v):
        return check_password(v)


@router.get("")
def list_users(
    q: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 10,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    stmt = scoped_select(User, actor)
    q = q.strip()
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(or_(User.name.ilike(f"%{esc}%", escape="\\"), User.email.ilike(f"%{esc}%", escape="\\")))
    if status in ("active", "pending", "disabled"):
        stmt = stmt.where(User.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    # Pending requests first so admins see them, then by name.
    rows = db.scalars(
        stmt.order_by((User.status != "pending"), func.lower(User.name)).offset((page - 1) * page_size).limit(page_size)
    ).all()
    tenant = db.get(Tenant, actor.tenant_id)
    pending = db.scalar(
        select(func.count()).select_from(User).where(User.tenant_id == actor.tenant_id, User.status == "pending")
    )
    return {
        "users": [_user_dict(u, tenant) for u in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pending_total": pending,
    }


@router.post("", status_code=201)
def create_user(body: CreateUser, actor: User = Depends(require_admin), db: Session = Depends(get_db)):
    if body.role != "user" and actor.role != "super_admin":
        raise AppError(403, "forbidden", "Only a Super Admin can create admins.")
    if db.scalar(select(User.id).where(User.email == body.email)):
        raise AppError(409, "email_taken", "An account with this email already exists.")
    user = User(
        tenant_id=actor.tenant_id,
        name=body.name,
        email=body.email,
        password_hash=hash_password(body.password),
        role=body.role,
        status="active",
    )
    try:
        db.add(user)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "email_taken", "An account with this email already exists.") from None
    return _user_dict(user, db.get(Tenant, actor.tenant_id))


@router.patch("/{user_id}")
def update_user(user_id: uuid.UUID, body: UpdateUser, actor: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = _load_target(db, actor, user_id)
    _can_manage(actor, target)
    if body.role is not None and body.role != target.role:
        if actor.role != "super_admin":
            raise AppError(403, "forbidden", "Only a Super Admin can change roles.")
        if target.role == "super_admin" and target.status == "active" and _active_super_admins(db, actor) <= 1:
            raise AppError(400, "last_super_admin", "The organization needs at least one active Super Admin.")
        target.role = body.role
    if body.status is not None and body.status != target.status:
        if body.status == "disabled":
            if target.id == actor.id:
                raise AppError(400, "cannot_disable_self", "You cannot disable your own account.")
            if target.role == "super_admin" and target.status == "active" and _active_super_admins(db, actor) <= 1:
                raise AppError(400, "last_super_admin", "The organization needs at least one active Super Admin.")
            _end_sessions(db, target.id)
        target.status = body.status  # pending -> active is an approval; disabled -> active re-enables
    if body.name is not None:
        target.name = body.name
    db.commit()
    return _user_dict(target, db.get(Tenant, actor.tenant_id))


@router.post("/{user_id}/reset-password", status_code=204)
def reset_password(user_id: uuid.UUID, body: ResetPassword, actor: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = _load_target(db, actor, user_id)
    _can_manage(actor, target)
    target.password_hash = hash_password(body.password)
    _end_sessions(db, target.id)
    db.commit()
    return Response(status_code=204)


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: uuid.UUID, actor: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Removes the user. For a pending join request this is a rejection."""
    target = _load_target(db, actor, user_id)
    if target.id == actor.id:
        raise AppError(400, "cannot_delete_self", "You cannot delete your own account.")
    _can_manage(actor, target)
    if target.role == "super_admin" and target.status == "active" and _active_super_admins(db, actor) <= 1:
        raise AppError(400, "last_super_admin", "The organization needs at least one active Super Admin.")
    db.delete(target)
    db.commit()
    return Response(status_code=204)
