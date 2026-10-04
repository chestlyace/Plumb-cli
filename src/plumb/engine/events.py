"""Events a driver emits during one turn. The last event is TurnDone or TurnStopped."""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class TextDelta:
    """A chunk of the model's answer, as it streams."""

    text: str


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: str


@dataclass(frozen=True)
class Retry:
    """The model's tool call or output was invalid; it was asked to fix it once."""

    name: str
    reason: str


@dataclass(frozen=True)
class RepeatBlocked:
    """A repeated tool call was not run; the model was reminded instead."""

    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class FallbackUsed:
    """The model failed, so the turn restarts on the fallback model. Anything
    streamed before this event belongs to the abandoned attempt."""

    from_model: str
    to_model: str
    reason: str


@dataclass(frozen=True)
class TurnDone:
    text: str | None
    output: Any
    files_read: list[str]
    # Opaque conversation state; pass it back as `history` on the next turn.
    history: list[Any] = field(repr=False)


StopReason = Literal["step_limit", "loop", "invalid_output", "model_error"]


@dataclass(frozen=True)
class TurnStopped:
    reason: StopReason
    detail: str
    files_read: list[str]


Event = (
    TextDelta
    | ToolCall
    | ToolResult
    | Retry
    | RepeatBlocked
    | FallbackUsed
    | TurnDone
    | TurnStopped
)
