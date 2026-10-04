"""The model-driven loop: PydanticAI behind our Driver interface."""

from collections.abc import AsyncIterator, Sequence
from typing import Any

from pydantic_ai import Agent, PromptedOutput, UsageLimits
from pydantic_ai.exceptions import (
    ModelAPIError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    OutputToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.models import Model
from pydantic_ai.run import AgentRunResultEvent

from plumb.config import Config
from plumb.engine.events import (
    Event,
    FallbackUsed,
    RepeatBlocked,
    Retry,
    TextDelta,
    ToolCall,
    ToolResult,
    TurnDone,
    TurnStopped,
)
from plumb.engine.loop_guard import LoopDetected, LoopGuard
from plumb.engine.tools import build_toolset
from plumb.model.adapter import build_model
from plumb.tools.toolbox import Toolbox

# One retry after an invalid tool call or invalid structured output.
RETRIES = 1


class _ModelFailed(Exception):
    pass


class PydanticAIDriver:
    def __init__(
        self,
        config: Config,
        toolbox: Toolbox,
        model: Model | None = None,
        fallback_model: Model | None = None,
    ) -> None:
        self.config = config
        self.toolbox = toolbox
        self.model = model or build_model(config.model_name, config.endpoint)
        if fallback_model is None and config.fallback_name:
            fallback_model = build_model(config.fallback_name, config.endpoint)
        self.fallback_model = fallback_model
        # Once the local model fails, the rest of the session uses the fallback.
        self._on_fallback = False

    async def run_turn(
        self,
        instructions: str,
        prompt: str,
        *,
        history: Sequence[Any] = (),
        output_type: type[Any] | None = None,
    ) -> AsyncIterator[Event]:
        if not self._on_fallback:
            try:
                async for event in self._attempt(
                    self.model, instructions, prompt, history, output_type
                ):
                    yield event
                return
            except _ModelFailed as failure:
                if self.fallback_model is None:
                    yield TurnStopped("model_error", str(failure), [])
                    return
                self._on_fallback = True
                yield FallbackUsed(
                    from_model=self.config.model_name,
                    to_model=self.config.fallback_name
                    or self.fallback_model.model_name,
                    reason=str(failure),
                )
        assert self.fallback_model is not None
        try:
            async for event in self._attempt(
                self.fallback_model, instructions, prompt, history, output_type
            ):
                yield event
        except _ModelFailed as failure:
            yield TurnStopped(
                "model_error", f"the fallback model failed too: {failure}", []
            )

    async def _attempt(
        self,
        model: Model,
        instructions: str,
        prompt: str,
        history: Sequence[Any],
        output_type: type[Any] | None,
    ) -> AsyncIterator[Event]:
        guard = LoopGuard()
        files_read: set[str] = set()
        agent = Agent(
            model,
            instructions=instructions,
            toolsets=[build_toolset(self.toolbox, guard, files_read)],
            # Structured output goes through the instructions as a JSON schema:
            # tested on gemma4, the model answers in prose instead of calling an
            # output tool, but replies with valid JSON when asked to.
            output_type=PromptedOutput(output_type) if output_type else str,
            retries=RETRIES,
        )
        blocked_seen = 0
        answer_ended = False
        try:
            async with agent.run_stream_events(
                prompt,
                message_history=list(history),
                usage_limits=UsageLimits(request_limit=self.config.step_limit),
            ) as stream:
                async for raw in stream:
                    if output_type:
                        # PydanticAI emits no event when it rejects a prompted
                        # JSON answer; the sign is a new response (part index 0)
                        # after a response that ended in text.
                        if (
                            answer_ended
                            and isinstance(raw, PartStartEvent)
                            and raw.index == 0
                        ):
                            yield Retry(
                                "output",
                                "the answer was not valid JSON for the requested structure",
                            )
                        if isinstance(raw, PartEndEvent):
                            answer_ended = isinstance(raw.part, TextPart)
                        elif isinstance(raw, PartStartEvent):
                            answer_ended = False
                    for event in _translate(raw, files_read):
                        if output_type and isinstance(event, TextDelta):
                            continue  # Raw JSON, not prose for the learner.
                        yield event
                    while blocked_seen < len(guard.blocked):
                        name, args = guard.blocked[blocked_seen]
                        blocked_seen += 1
                        yield RepeatBlocked(name, args)
        except UsageLimitExceeded:
            yield TurnStopped(
                "step_limit",
                f"stopped after {self.config.step_limit} model calls without an answer",
                sorted(files_read),
            )
        except LoopDetected as loop:
            yield TurnStopped("loop", str(loop), sorted(files_read))
        except UnexpectedModelBehavior as error:
            yield TurnStopped("invalid_output", str(error), sorted(files_read))
        except ModelAPIError as error:
            raise _ModelFailed(str(error)) from error


def _translate(
    raw: AgentStreamEvent | AgentRunResultEvent, files_read: set[str]
) -> list[Event]:
    if isinstance(raw, PartStartEvent) and isinstance(raw.part, TextPart):
        return [TextDelta(raw.part.content)] if raw.part.content else []
    if isinstance(raw, PartDeltaEvent) and isinstance(raw.delta, TextPartDelta):
        return [TextDelta(raw.delta.content_delta)] if raw.delta.content_delta else []
    if isinstance(raw, FunctionToolCallEvent):
        if not raw.args_valid:
            return []  # The RetryPromptPart that follows says why.
        return [
            ToolCall(raw.part.tool_call_id, raw.part.tool_name, raw.part.args_as_dict())
        ]
    if isinstance(raw, FunctionToolResultEvent | OutputToolResultEvent):
        part = raw.part
        if isinstance(part, RetryPromptPart):
            return [Retry(part.tool_name or "output", str(part.content)[:300])]
        if isinstance(raw, FunctionToolResultEvent):
            return [ToolResult(part.tool_call_id, part.tool_name, str(part.content))]
        return []
    if isinstance(raw, AgentRunResultEvent):
        output = raw.result.output
        return [
            TurnDone(
                text=output if isinstance(output, str) else None,
                output=output,
                files_read=sorted(files_read),
                history=raw.result.all_messages(),
            )
        ]
    return []
