"""The internal format every server-side model call speaks (C1 spec §4.1).

Messages follow OpenAI's chat shape (what LiteLLM normalizes to), so the
OpenAI-compatible adapter is close to a pass-through and the Ollama adapter is
the one that translates. Nothing here knows about HTTP or any provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union

Role = Literal["system", "user", "assistant", "tool"]
ThinkLevel = Literal["off", "on", "low", "medium", "high"]
FinishReason = Literal["stop", "length", "tool_calls", "error"]


@dataclass(frozen=True)
class Attachment:
    kind: Literal["image", "audio"]
    mime: str
    base64: str = field(repr=False)   # never let a stray repr/f-string dump image bytes into logs
    name: str = ""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str = ""
    attachments: tuple[Attachment, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None   # on role="tool": which call this answers
    name: str | None = None           # on role="tool": the tool's name (Ollama wants it)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class CallSettings:
    think: ThinkLevel = "off"
    num_ctx: int | None = None
    keep_alive: str | int | None = None
    num_predict: int | None = None
    temperature: float | None = None


@dataclass(frozen=True)
class Capabilities:
    """What a model can do. None = the provider didn't say; callers refuse
    only on an explicit False."""
    tools: bool | None = None
    thinking: bool | None = None
    vision: bool | None = None
    audio: bool | None = None
    context_window: int | None = None   # tokens

    def to_json(self) -> dict[str, Any]:
        return {"tools": self.tools, "thinking": self.thinking, "vision": self.vision,
                "audio": self.audio, "contextWindow": self.context_window}


# ---- stream chunks an adapter yields ----

@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    text: str


@dataclass(frozen=True)
class ToolCallReady:
    """A COMPLETE tool call: adapters assemble streamed argument fragments."""
    call: ToolCall


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    estimated: bool = False
    detail: dict[str, Any] = field(default_factory=dict)   # e.g. Ollama's *_duration, in ns


@dataclass(frozen=True)
class Finish:
    reason: FinishReason


@dataclass(frozen=True)
class FeatureDropped:
    """The provider rejected a feature; the adapter retried without it."""
    feature: Literal["tools", "thinking", "think_level"]


Chunk = Union[TextDelta, ReasoningDelta, ToolCallReady, Usage, Finish, FeatureDropped]


# ---- errors ----

class ProviderUnavailable(Exception):
    """Could not connect to the provider, or the stream broke off."""


class ProviderTimeout(Exception):
    """The provider sent nothing for INFERENCE_TIMEOUT_S seconds (an idle timeout)."""


class ProviderError(Exception):
    """The provider answered with an error. `safe_message` is the provider's
    own text, trimmed: never request headers, never keys."""

    def __init__(self, status: int, safe_message: str, *, explained: bool = False) -> None:
        super().__init__(f"HTTP {status}: {safe_message}")
        self.status = status
        self.safe_message = safe_message
        # True when `safe_message` is our own plain sentence for the status
        # (rate limit, rejected key, no credit), shown to the user as it is.
        self.explained = explained
