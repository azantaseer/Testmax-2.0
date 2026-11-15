from dataclasses import dataclass

from app import providers
from app.config import get_settings
from app.errors import AppError
from app.models import Tenant, User


@dataclass(frozen=True)
class EffectiveAI:
    provider: str | None
    model: str | None
    source: str  # "user" | "tenant" | "system" | "none"

    @property
    def chosen(self) -> bool:
        return bool(self.provider and self.model)


def _valid(provider: str | None, model: str | None) -> bool:
    return bool(provider and model and providers.get(provider))


def effective_ai(user: User | None, tenant: Tenant | None) -> EffectiveAI:
    """User setting beats tenant setting beats the operator's system default. A choice needs both provider and model.
    If nobody has chosen, the answer is "none": there is no hidden default model in the code."""
    if user is not None and _valid(user.ai_provider, user.ai_model):
        return EffectiveAI(user.ai_provider, user.ai_model, "user")
    if tenant is not None and _valid(tenant.ai_provider, tenant.ai_model):
        return EffectiveAI(tenant.ai_provider, tenant.ai_model, "tenant")
    s = get_settings()
    if _valid(s.default_ai_provider, s.default_ai_model):
        return EffectiveAI(s.default_ai_provider, s.default_ai_model, "system")
    return EffectiveAI(None, None, "none")


def require_model(ai: EffectiveAI) -> None:
    """Work that needs an AI model refuses to start until somebody has chosen one."""
    if not ai.chosen:
        raise AppError(
            409,
            "no_model_selected",
            "No AI model is selected yet. A Super Admin can choose the organization's model under Organization AI Model, "
            "or you can choose your own under My Settings.",
        )
