from datetime import datetime, timezone

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import AppError
from app.models import User, UserSession
from app.security import token_hash

SESSION_COOKIE = "tm_session"


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise AppError(401, "not_authenticated", "Please sign in.")
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == token_hash(token))
    ).first()
    if row is None:
        raise AppError(401, "not_authenticated", "Please sign in.")
    session, user = row
    if session.expires_at <= datetime.now(timezone.utc) or user.status != "active":
        db.delete(session)
        db.commit()
        raise AppError(401, "not_authenticated", "Please sign in.")
    return user


def require_super_admin(user: User = Depends(current_user)) -> User:
    if user.role != "super_admin":
        raise AppError(403, "forbidden", "Only the organization's Super Admin can do this.")
    return user


def scoped_select(model, user: User):
    """Every query on tenant-owned tables starts here, so it always filters by the caller's tenant."""
    if not hasattr(model, "tenant_id"):
        raise TypeError(f"{model.__name__} has no tenant_id; use get_own_tenant for tenants")
    return select(model).where(model.tenant_id == user.tenant_id)
