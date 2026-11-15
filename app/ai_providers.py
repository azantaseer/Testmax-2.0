"""Provider access for the AI settings page: list models, test a model, check tool calling.

Platform keys come from the server environment. Error messages never include the SDK's own text, which can contain
parts of a key. Provider-specific work is done by the adapters in app/providers.py.
"""
import time as _time
from collections.abc import Mapping

from app import providers


class AIProviderError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


class _Labels(Mapping):
    """id -> label, read live from the registry, so a new adapter appears without touching this file."""

    def __getitem__(self, key):
        return providers.REGISTRY[key].label

    def __iter__(self):
        return iter(providers.REGISTRY)

    def __len__(self):
        return len(providers.REGISTRY)


PROVIDERS = _Labels()


def key_for(provider: str) -> str:
    adapter = providers.get(provider)
    return adapter.api_key() if adapter else ""


def is_configured(provider: str | None) -> bool:
    adapter = providers.get(provider)
    return bool(adapter and adapter.is_configured())


def providers_status() -> list[dict]:
    return [{"id": p.id, "label": p.label, "configured": p.is_configured()} for p in providers.REGISTRY.values()]


def _client(provider: str):
    adapter = providers.get(provider)
    if adapter is None:
        raise AIProviderError("unknown_provider", "Unknown AI provider.")
    if not adapter.is_configured():
        raise AIProviderError("provider_not_configured", f"No API key for {adapter.label} is configured on the server.")
    return adapter.make_client(20)


def _translate(provider: str, exc: Exception) -> AIProviderError:
    sdk, label = providers.get(provider).sdk, providers.get(provider).label
    if isinstance(exc, (sdk.AuthenticationError, sdk.PermissionDeniedError)):
        return AIProviderError("provider_key_rejected", f"{label} rejected the server's API key.")
    if isinstance(exc, sdk.NotFoundError):
        return AIProviderError("model_not_found", f"{label} does not know this model.")
    if isinstance(exc, sdk.RateLimitError):
        return AIProviderError("provider_rate_limited", f"{label} is rate limiting requests. Try again shortly.")
    if isinstance(exc, (sdk.APITimeoutError, sdk.APIConnectionError)):
        return AIProviderError("provider_unreachable", f"Could not reach {label}. Check the server's network.")
    return AIProviderError("provider_error", f"{label} returned an error ({type(exc).__name__}).")


def list_models(provider: str) -> list[str]:
    client = _client(provider)
    try:
        return providers.get(provider).list_models(client)
    except AIProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - translated, never echoed
        raise _translate(provider, exc) from None


def test_model(provider: str, model: str) -> None:
    """Raises AIProviderError if the key or model does not work. Model lookup is free; the tool check costs a few tokens."""
    client = _client(provider)
    try:
        providers.get(provider).retrieve(client, model)
    except Exception as exc:  # noqa: BLE001
        raise _translate(provider, exc) from None


# Does the model support tool calling (scans need it; requirement generation does not)?
# One tiny call, cached for an hour. None means "could not tell" and never blocks anything. A model that only needs
# reasoning turned off still counts as supported: the adapter handles that on its own.
_TOOL_CACHE: dict[tuple[str, str], tuple[float, bool, str]] = {}


def check_tool_calling(provider: str, model: str) -> tuple[bool | None, str]:
    adapter = providers.get(provider)
    if adapter is None:
        return None, ""
    cached = _TOOL_CACHE.get((provider, model))
    if cached and _time.time() - cached[0] < 3600:
        return cached[1], cached[2]
    try:
        client = _client(provider)
        supported, note = adapter.probe_tools(client, model)
    except Exception:  # noqa: BLE001
        return None, ""
    if supported is not None:
        _TOOL_CACHE[(provider, model)] = (_time.time(), supported, note)
    return supported, note
