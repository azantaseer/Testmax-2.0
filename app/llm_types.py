"""Types shared by the provider adapters and everything that calls a model."""
from dataclasses import dataclass, field


class LLMError(Exception):
    """A model call failed. The message is safe to show: it never contains the provider's raw text for auth problems."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@dataclass
class LLMResult:
    text: str
    tokens_in: int | None
    tokens_out: int | None
    truncated: bool = False  # the model stopped because it hit the output limit


@dataclass
class ChatResult:
    text: str
    tool_calls: list[dict]
    tokens_in: int | None
    tokens_out: int | None
    # Things an adapter did on its own to make the call work, e.g. "reasoning turned off for tool calls".
    notes: list[str] = field(default_factory=list)
