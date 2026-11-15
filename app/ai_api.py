"""Shared request models and helpers for the AI settings endpoints (tenant level and per-user)."""
from pydantic import BaseModel, field_validator

from app import ai_providers
from app.errors import AppError

_STATUS = {
    "unknown_provider": 400,
    "provider_not_configured": 400,
    "model_not_found": 400,
    "provider_rate_limited": 429,
}


class AIChoice(BaseModel):
    provider: str
    model: str

    @field_validator("provider")
    @classmethod
    def _provider(cls, v: str) -> str:
        if v not in ai_providers.PROVIDERS:
            raise ValueError("Choose OpenAI or Anthropic.")
        return v

    @field_validator("model")
    @classmethod
    def _model(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 100 or any(c.isspace() for c in v):
            raise ValueError("Choose a model.")
        return v


def raise_provider_error(err: ai_providers.AIProviderError):
    raise AppError(_STATUS.get(err.code, 502), err.code, err.message)


def require_configured(provider: str) -> None:
    if not ai_providers.is_configured(provider):
        raise AppError(
            400,
            "provider_not_configured",
            f"No API key for {ai_providers.PROVIDERS[provider]} is configured on the server.",
        )


def models_response(provider: str) -> dict:
    try:
        return {"provider": provider, "models": ai_providers.list_models(provider)}
    except ai_providers.AIProviderError as err:
        raise_provider_error(err)


def test_response(choice: AIChoice) -> dict:
    try:
        ai_providers.test_model(choice.provider, choice.model)
    except ai_providers.AIProviderError as err:
        raise_provider_error(err)
    out = {"ok": True, "message": f"Connected to {ai_providers.PROVIDERS[choice.provider]}. Model is available.", "tool_calling": None}
    supported, reason = ai_providers.check_tool_calling(choice.provider, choice.model)
    out["tool_calling"] = supported
    if supported is True and reason:
        out["message"] += f" Note: {reason}"
    if supported is False:
        out["message"] += (
            f" Warning: this model cannot be used for app scans, which need tool calling ({reason}). "
            "It still works for requirement generation. Choose another model for scans."
        )
    return out
