"""The signed-in user's own settings: AI model override and password."""
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app import ai_providers
from app.ai_api import AIChoice, models_response, require_configured, test_response
from app.ai_settings import effective_ai
from app.db import get_db
from app.deps import SESSION_COOKIE, current_user
from app.errors import AppError
from app.models import Tenant, User, UserSession
from app.security import hash_password, token_hash, verify_password
from app.validation import check_password

router = APIRouter(prefix="/api/me")


def _payload(user: User, tenant: Tenant) -> dict:
    ai = effective_ai(user, tenant)
    org = effective_ai(None, tenant)
    return {
        "provider": user.ai_provider,
        "model": user.ai_model,
        "organization": {"provider": org.provider, "model": org.model, "source": org.source},
        "effective": {"provider": ai.provider, "model": ai.model, "source": ai.source},
        "providers": ai_providers.providers_status(),
    }


@router.get("/ai-settings")
def get_settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _payload(user, db.get(Tenant, user.tenant_id))


@router.put("/ai-settings")
def put_settings(body: AIChoice, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_configured(body.provider)
    user.ai_provider = body.provider
    user.ai_model = body.model
    db.commit()
    return _payload(user, db.get(Tenant, user.tenant_id))


@router.delete("/ai-settings")
def clear_settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Go back to the organization's model."""
    user.ai_provider = None
    user.ai_model = None
    db.commit()
    return _payload(user, db.get(Tenant, user.tenant_id))


@router.get("/ai-models")
def get_models(provider: str, user: User = Depends(current_user)):
    return models_response(provider)


@router.post("/ai-test")
def post_test(body: AIChoice, user: User = Depends(current_user)):
    return test_response(body)


class ChangePassword(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _new(cls, v: str) -> str:
        return check_password(v)


@router.post("/password", status_code=204)
def change_password(
    body: ChangePassword, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    if not verify_password(body.current_password, user.password_hash):
        raise AppError(400, "wrong_password", "Your current password is not correct.")
    user.password_hash = hash_password(body.new_password)
    # Sign out every other session of this user; the caller's own session stays.
    keep = token_hash(request.cookies.get(SESSION_COOKIE, ""))
    db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.token_hash != keep))
    db.commit()
    return Response(status_code=204)
