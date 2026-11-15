"""One call interface over every AI provider.

The pilot's engines send a system prompt plus a user message (or a tool-calling conversation) and read the answer
back. This module only dispatches to the provider adapters in app/providers.py; it contains no provider-specific code.
Errors become LLMError: classified as retryable or not, with a message that is safe to show.
"""
from app import providers
from app.llm_types import ChatResult, LLMError, LLMResult
from app.providers import provider_reason, to_anthropic, to_openai  # noqa: F401  (re-exported)

__all__ = ["ChatResult", "LLMError", "LLMResult", "chat", "complete", "converse", "make_client", "provider_reason", "to_anthropic", "to_openai"]


def _adapter(provider: str) -> providers.Provider:
    adapter = providers.get(provider)
    if adapter is None:
        raise LLMError("Unknown AI provider.")
    return adapter


def make_client(provider: str):
    adapter = _adapter(provider)
    if not adapter.is_configured():
        raise LLMError(f"No API key for {adapter.label} is configured on the server.")
    return adapter.make_client()


def _translate(provider: str, model: str, exc: Exception) -> LLMError:
    return _adapter(provider).translate(model, exc)


def complete(provider: str, model: str, system: str, user: str, client=None, max_output_tokens: int | None = None) -> LLMResult:
    adapter = _adapter(provider)
    client = client or make_client(provider)
    try:
        if max_output_tokens:
            return adapter.complete(client, model, system, user, max_output_tokens)
        return adapter.complete(client, model, system, user)
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 - translated, never echoed
        raise adapter.translate(model, exc) from None


def converse(provider: str, model: str, system: str, messages: list[dict], max_output_tokens: int | None = None, client=None) -> LLMResult:
    """A text conversation without tools (several turns, so a failed answer can be sent back for correction)."""
    adapter = _adapter(provider)
    client = client or make_client(provider)
    try:
        return adapter.converse(client, model, system, messages, max_output_tokens)
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 - translated, never echoed
        raise adapter.translate(model, exc) from None


def chat(provider: str, model: str, system: str, messages: list[dict], tools: list[dict], *, tool_choice: str | None = None, client=None) -> ChatResult:
    adapter = _adapter(provider)
    client = client or make_client(provider)
    try:
        return adapter.chat(client, model, system, messages, tools, tool_choice)
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 - translated, never echoed
        raise adapter.translate(model, exc) from None
