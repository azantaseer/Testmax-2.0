"""AI provider adapters: one class per provider, one registry.

Everything provider-specific lives here (client construction, listing models, the call shapes for plain completion
and for tool calling, error translation, model quirks). The rest of the app asks the registry; it never branches on a
provider name. Adding a provider means adding one adapter class and one line in REGISTRY.

Neutral message format used by chat():
  {"role": "user", "content": str}
  {"role": "assistant", "content": str | None, "tool_calls": [{"id", "name", "arguments": dict}]}
  {"role": "tool", "tool_call_id": str, "content": str}
tools: [{"name", "description", "input_schema"}]
"""
import json
import re

import anthropic
import openai

from app.config import get_settings
from app.llm_types import ChatResult, LLMError, LLMResult


def provider_reason(exc: Exception) -> str:
    """The provider's own explanation for a refused request, with anything key-like removed."""
    body = getattr(exc, "body", None)
    detail = ""
    if isinstance(body, dict):
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        detail = str(inner.get("message", ""))
    detail = detail or str(getattr(exc, "message", "")) or "no reason given"
    detail = re.sub(r"(sk|key|tmr)[-_][A-Za-z0-9_\-]{6,}", "[removed]", detail)
    return " ".join(detail.split())[:300]


# ---------------------------------------------------------------------------
# Message converters (pure functions, tested on their own)
# ---------------------------------------------------------------------------
def to_openai(system: str, messages: list[dict], tools: list[dict]) -> tuple[list[dict], list[dict]]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append(
                {
                    "role": "assistant",
                    "content": m.get("content"),
                    "tool_calls": [
                        {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc.get("arguments") or {})}}
                        for tc in m["tool_calls"]
                    ],
                }
            )
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    oa_tools = [
        {"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
        for t in tools
    ]
    return out, oa_tools


def to_anthropic(messages: list[dict], tools: list[dict]) -> tuple[list[dict], list[dict]]:
    """Tool results go in a user message (all results of one assistant turn together, before any plain text)."""
    out: list[dict] = []
    pending_results: list[dict] = []

    def flush(extra_text: str | None = None):
        nonlocal pending_results
        blocks = list(pending_results)
        if extra_text:
            blocks.append({"type": "text", "text": extra_text})
        if blocks:
            out.append({"role": "user", "content": blocks})
        pending_results = []

    for m in messages:
        if m["role"] == "tool":
            pending_results.append({"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]})
        elif m["role"] == "user":
            flush(m.get("content") or "")
        else:  # assistant
            flush()
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc.get("arguments") or {}})
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
    flush()
    an_tools = [{"name": t["name"], "description": t.get("description", ""), "input_schema": t.get("input_schema") or {"type": "object", "properties": {}}} for t in tools]
    return out, an_tools



def to_responses_input(messages: list[dict]) -> list[dict]:
    """Neutral messages -> OpenAI Responses API input items (function calls and their outputs are separate items)."""
    items: list[dict] = []
    for m in messages:
        if m["role"] == "assistant":
            if m.get("content"):
                items.append({"role": "assistant", "content": m["content"]})
            for tc in m.get("tool_calls") or []:
                items.append({"type": "function_call", "call_id": tc["id"], "name": tc["name"], "arguments": json.dumps(tc.get("arguments") or {})})
        elif m["role"] == "tool":
            items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
        else:
            items.append({"role": "user", "content": m.get("content") or ""})
    return items

# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------
class Provider:
    id = ""
    label = ""
    sdk = None  # the provider's SDK module (its exception classes are used for translation)
    key_setting = ""  # attribute of Settings that holds the platform key

    # -- configuration --------------------------------------------------
    def api_key(self) -> str:
        return getattr(get_settings(), self.key_setting, "")

    def is_configured(self) -> bool:
        return bool(self.api_key())

    def make_client(self, timeout: int | None = None):
        raise NotImplementedError

    # -- models ----------------------------------------------------------
    def list_models(self, client) -> list[str]:
        raise NotImplementedError

    def retrieve(self, client, model: str) -> None:
        client.models.retrieve(model)

    # -- calls -----------------------------------------------------------
    def converse(self, client, model: str, system: str, messages: list[dict], max_output_tokens: int | None = None) -> LLMResult:
        """Plain text conversation (no tools): the neutral user/assistant messages in, the reply text out."""
        raise NotImplementedError

    def complete(self, client, model: str, system: str, user: str, max_output_tokens: int | None = None) -> LLMResult:
        return self.converse(client, model, system, [{"role": "user", "content": user}], max_output_tokens)

    def chat(self, client, model: str, system: str, messages: list[dict], tools: list[dict], tool_choice: str | None = None) -> ChatResult:
        raise NotImplementedError

    def probe_tools(self, client, model: str) -> tuple[bool | None, str]:
        """(True/False/None, note). None = could not tell; never blocks anything."""
        return None, ""

    # -- errors ----------------------------------------------------------
    def translate(self, model: str, exc: Exception) -> LLMError:
        sdk, label = self.sdk, self.label
        if isinstance(exc, (sdk.AuthenticationError, sdk.PermissionDeniedError)):
            return LLMError(f"{label} rejected the server's API key.")
        if isinstance(exc, sdk.NotFoundError):
            return LLMError(f"{label} does not know the model '{model}'.")
        if isinstance(exc, sdk.BadRequestError):
            return LLMError(f"{label} refused the request: {provider_reason(exc)}")
        if isinstance(exc, sdk.RateLimitError):
            return LLMError(f"{label} is rate limiting requests.", retryable=True)
        if isinstance(exc, (sdk.APITimeoutError, sdk.APIConnectionError)):
            return LLMError(f"Could not reach {label} (network or timeout).", retryable=True)
        if isinstance(exc, sdk.InternalServerError):
            return LLMError(f"{label} had a temporary error.", retryable=True)
        return LLMError(f"{label} returned an error ({type(exc).__name__}).")


class OpenAIProvider(Provider):
    id = "openai"
    label = "OpenAI"
    sdk = openai
    key_setting = "openai_api_key"

    # Models that refuse function tools on chat completions and need the Responses API instead
    # (found at run time from the provider's own error, remembered per process).
    _responses_models: set[str] = set()
    _NON_CHAT_MARKERS = ("embedding", "whisper", "tts", "dall-e", "moderation", "audio", "realtime", "transcribe", "image", "search", "davinci", "babbage", "codex")

    def make_client(self, timeout=None):
        return openai.OpenAI(api_key=self.api_key(), max_retries=0, timeout=timeout or get_settings().llm_timeout_seconds)

    def list_models(self, client):
        ids = [m.id for m in client.models.list()]
        # A convenience for the picker only: hide models that obviously are not chat models.
        return sorted({i for i in ids if not any(marker in i.lower() for marker in self._NON_CHAT_MARKERS)})

    def converse(self, client, model, system, messages, max_output_tokens=None):
        oa_messages, _ = to_openai(system, messages, [])
        kwargs = {"model": model, "messages": oa_messages}
        if max_output_tokens:
            kwargs["max_completion_tokens"] = max_output_tokens  # the same parameter Pilot 1 used
        completion = client.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        usage = completion.usage
        return LLMResult(
            choice.message.content or "",
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
            getattr(choice, "finish_reason", None) == "length",
        )

    def chat(self, client, model, system, messages, tools, tool_choice=None):
        if tools and model in self._responses_models:
            return self._chat_responses(client, model, system, messages, tools, tool_choice)
        oa_messages, oa_tools = to_openai(system, messages, tools)
        kwargs = {"model": model, "messages": oa_messages}
        if oa_tools:
            kwargs["tools"] = oa_tools
            if tool_choice == "none":
                kwargs["tool_choice"] = "none"
        try:
            completion = client.chat.completions.create(**kwargs)
        except openai.BadRequestError as exc:
            # Some newer models refuse function tools on chat completions and say to use /v1/responses.
            # Do exactly that, keeping the model's reasoning on, and remember it for the next call.
            if oa_tools and "/v1/responses" in provider_reason(exc):
                self._responses_models.add(model)
                return self._chat_responses(client, model, system, messages, tools, tool_choice)
            raise
        msg = completion.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": tc.id, "name": tc.function.name, "arguments": args if isinstance(args, dict) else {}})
        usage = completion.usage
        return ChatResult(msg.content or "", calls, getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))

    def _chat_responses(self, client, model, system, messages, tools, tool_choice):
        kwargs = {
            "model": model,
            "instructions": system,
            "input": to_responses_input(messages),
            "tools": [{"type": "function", "name": t["name"], "description": t.get("description", ""), "parameters": t.get("input_schema") or {"type": "object", "properties": {}}} for t in tools],
        }
        if tool_choice == "none":
            kwargs["tool_choice"] = "none"
        response = client.responses.create(**kwargs)
        calls, texts = [], []
        for item in response.output or []:
            kind = getattr(item, "type", "")
            if kind == "function_call":
                try:
                    args = json.loads(item.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append({"id": item.call_id, "name": item.name, "arguments": args if isinstance(args, dict) else {}})
            elif kind == "message":
                texts.extend(part.text for part in (item.content or []) if getattr(part, "type", "") == "output_text")
        usage = getattr(response, "usage", None)
        note = f"Used OpenAI's Responses API for tool calls because {model} does not accept tools on chat completions."
        return ChatResult("".join(texts), calls, getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None), [note])

    def probe_tools(self, client, model):
        ping = [{"name": "ping", "description": "ping", "input_schema": {"type": "object", "properties": {}}}]
        try:
            result = self.chat(client, model, "", [{"role": "user", "content": "Reply with the word ok."}], ping)
        except openai.BadRequestError as exc:
            return False, provider_reason(exc)
        except Exception:  # noqa: BLE001 - network, rate limit, odd SDK errors
            return None, ""
        return True, (result.notes[0] if result.notes else "")


class AnthropicProvider(Provider):
    id = "anthropic"
    label = "Anthropic"
    sdk = anthropic
    key_setting = "anthropic_api_key"

    def make_client(self, timeout=None):
        return anthropic.Anthropic(api_key=self.api_key(), max_retries=0, timeout=timeout or get_settings().llm_timeout_seconds)

    def list_models(self, client):
        return sorted({m.id for m in client.models.list(limit=100)})

    def _stream(self, client, max_output_tokens=None, **kwargs):
        # Stream so long outputs do not hit the non-streaming time limit. The configured ceiling always applies.
        ceiling = get_settings().anthropic_max_output_tokens
        max_tokens = min(max_output_tokens, ceiling) if max_output_tokens else ceiling
        with client.messages.stream(max_tokens=max_tokens, **kwargs) as stream:
            return stream.get_final_message()

    @staticmethod
    def _result(message) -> LLMResult:
        text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
        return LLMResult(text, message.usage.input_tokens, message.usage.output_tokens, getattr(message, "stop_reason", None) == "max_tokens")

    def complete(self, client, model, system, user, max_output_tokens=None):
        message = self._stream(client, max_output_tokens, model=model, system=system, messages=[{"role": "user", "content": user}])
        return self._result(message)

    def converse(self, client, model, system, messages, max_output_tokens=None):
        an_messages, _ = to_anthropic(messages, [])
        return self._result(self._stream(client, max_output_tokens, model=model, system=system, messages=an_messages))

    def chat(self, client, model, system, messages, tools, tool_choice=None):
        an_messages, an_tools = to_anthropic(messages, tools)
        kwargs = {"model": model, "system": system, "messages": an_messages}
        if an_tools:
            kwargs["tools"] = an_tools
            if tool_choice == "none":
                kwargs["tool_choice"] = {"type": "none"}
        message = self._stream(client, **kwargs)
        text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
        calls = [{"id": b.id, "name": b.name, "arguments": dict(b.input or {})} for b in message.content if getattr(b, "type", "") == "tool_use"]
        return ChatResult(text, calls, message.usage.input_tokens, message.usage.output_tokens)


REGISTRY: dict[str, Provider] = {p.id: p for p in (OpenAIProvider(), AnthropicProvider())}


def get(provider_id: str | None) -> Provider | None:
    return REGISTRY.get(provider_id or "")
