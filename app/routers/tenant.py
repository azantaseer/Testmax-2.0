from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import ai_providers
from app.ai_api import AIChoice, models_response, require_configured, test_response
from app.ai_settings import effective_ai
from app.db import get_db
from app.deps import require_super_admin
from app.models import Tenant, User

router = APIRouter(prefix="/api/tenant")


def get_own_tenant(db: Session, user: User) -> Tenant:
    """The caller's own tenant; the only tenant a caller can ever load."""
    return db.get(Tenant, user.tenant_id)


def _settings_payload(tenant: Tenant) -> dict:
    ai = effective_ai(None, tenant)
    return {
        "provider": tenant.ai_provider,
        "model": tenant.ai_model,
        "effective": {"provider": ai.provider, "model": ai.model, "source": ai.source},
        "providers": ai_providers.providers_status(),
    }


@router.get("/ai-settings")
def get_ai_settings(user: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    return _settings_payload(get_own_tenant(db, user))


@router.put("/ai-settings")
def put_ai_settings(body: AIChoice, user: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    require_configured(body.provider)
    tenant = get_own_tenant(db, user)
    tenant.ai_provider = body.provider
    tenant.ai_model = body.model
    db.commit()
    return _settings_payload(tenant)


@router.get("/ai-models")
def get_ai_models(provider: str, user: User = Depends(require_super_admin)):
    return models_response(provider)


@router.post("/ai-test")
def post_ai_test(body: AIChoice, user: User = Depends(require_super_admin)):
    return test_response(body)
