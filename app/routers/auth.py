import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import ai_providers
from app.ai_settings import effective_ai
from app.config import get_settings
from app.db import get_db
from app.deps import SESSION_COOKIE, current_user
from app.errors import AppError
from app.models import Tenant, User, UserSession
from app.validation import check_email as _check_email, check_password as _check_password
from app.security import hash_password, login_throttle, new_session_token, token_hash, verify_password

router = APIRouter(prefix="/api")

class _AccountFields(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str
    password: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Enter your name.")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _check_email(v)

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return _check_password(v)


class SignupOrganization(_AccountFields):
    organization_name: str = Field(min_length=2, max_length=100)
    remember_me: bool = True

    @field_validator("organization_name")
    @classmethod
    def _org(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Enter your organization's name.")
        return v


class SignupJoin(_AccountFields):
    organization_id: uuid.UUID


class Login(BaseModel):
    email: str
    password: str = Field(max_length=200)
    remember_me: bool = True

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return v.strip().lower()


def _me_payload(user: User, tenant: Tenant) -> dict:
    ai = effective_ai(user, tenant)
    return {
        "user": {"id": str(user.id), "name": user.name, "email": user.email, "role": user.role},
        "tenant": {"id": str(tenant.id), "name": tenant.name},
        "ai": {"provider": ai.provider, "model": ai.model, "source": ai.source},
        "providers": [{"id": p["id"], "label": p["label"]} for p in ai_providers.providers_status()],
    }


def _start_session(db: Session, response: Response, user: User, remember_me: bool) -> None:
    s = get_settings()
    token = new_session_token()
    lifetime = timedelta(days=s.remember_days) if remember_me else timedelta(hours=s.session_hours)
    db.add(
        UserSession(
            token_hash=token_hash(token),
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + lifetime,
        )
    )
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(lifetime.total_seconds()) if remember_me else None,
        httponly=True,
        samesite="lax",
        secure=s.cookie_secure,
        path="/",
    )


@router.post("/auth/signup-organization", status_code=201)
def signup_organization(body: SignupOrganization, response: Response, db: Session = Depends(get_db)):
    if db.scalar(select(User.id).where(User.email == body.email)):
        raise AppError(409, "email_taken", "An account with this email already exists.")
    if db.scalar(select(Tenant.id).where(func.lower(Tenant.name) == body.organization_name.lower())):
        raise AppError(409, "organization_exists", "An organization with this name already exists.")
    tenant = Tenant(name=body.organization_name)
    try:
        db.add(tenant)
        db.flush()
        user = User(
            tenant_id=tenant.id,
            name=body.name,
            email=body.email,
            password_hash=hash_password(body.password),
            role="super_admin",
            status="active",
        )
        db.add(user)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "already_exists", "That email or organization name is already taken.") from None
    _start_session(db, response, user, body.remember_me)
    return _me_payload(user, tenant)


@router.post("/auth/signup-join", status_code=201)
def signup_join(body: SignupJoin, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, body.organization_id)
    if tenant is None:
        raise AppError(404, "organization_not_found", "That organization was not found.")
    if db.scalar(select(User.id).where(User.email == body.email)):
        raise AppError(409, "email_taken", "An account with this email already exists.")
    user = User(
        tenant_id=tenant.id,
        name=body.name,
        email=body.email,
        password_hash=hash_password(body.password),
        role="user",
        status="pending",
    )
    try:
        db.add(user)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "email_taken", "An account with this email already exists.") from None
    return {"status": "pending", "organization": {"name": tenant.name}}


@router.post("/auth/login")
def login(body: Login, response: Response, db: Session = Depends(get_db)):
    if login_throttle.blocked(body.email):
        raise AppError(429, "too_many_attempts", "Too many failed attempts. Try again in a few minutes.")
    user = db.scalar(select(User).where(User.email == body.email))
    if not verify_password(body.password, user.password_hash if user else None):
        login_throttle.record_failure(body.email)
        raise AppError(401, "invalid_credentials", "Incorrect email or password.")
    login_throttle.reset(body.email)
    if user.status == "disabled":
        raise AppError(403, "account_disabled", "Your account has been disabled. Contact your organization's admin.")
    if user.status != "active":
        raise AppError(
            403, "pending_approval", "Your account is waiting for approval by your organization's admin."
        )
    tenant = db.get(Tenant, user.tenant_id)
    _start_session(db, response, user, body.remember_me)
    return _me_payload(user, tenant)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        row = db.get(UserSession, token_hash(token))
        if row:
            db.delete(row)
            db.commit()
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _me_payload(user, db.get(Tenant, user.tenant_id))


@router.get("/public/organizations")
def search_organizations(q: str = "", db: Session = Depends(get_db)):
    """Names only, for the join screen. Needs at least 2 characters; returns at most 20."""
    q = q.strip()
    if len(q) < 2:
        return {"organizations": []}
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = db.execute(
        select(Tenant.id, Tenant.name)
        .where(Tenant.name.ilike(f"%{escaped}%", escape="\\"))
        .order_by(func.lower(Tenant.name))
        .limit(20)
    ).all()
    return {"organizations": [{"id": str(r.id), "name": r.name} for r in rows]}
