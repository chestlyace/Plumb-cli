"""A scripted stand-in for the model, so engine tests run without Ollama."""

import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)


class Calls(list[tuple[str, dict[str, Any]]]):
    """One step that calls these tools, as (name, args) pairs."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]], say: str = "") -> None:
        super().__init__(calls)
        self.say = say  # Text the model says before the calls, in the same response.


def scripted(*steps: str | Calls | Exception) -> FunctionModel:
    """Each model request plays the next step: text is streamed in two chunks,
    Calls become tool calls, an exception is raised. The last step repeats."""
    position = {"n": 0}

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        step = steps[min(position["n"], len(steps) - 1)]
        position["n"] += 1
        if isinstance(step, Exception):
            raise step
        if isinstance(step, Calls):
            if step.say:
                yield step.say
            yield {
                i: DeltaToolCall(
                    name=name,
                    json_args=json.dumps(args),
                    tool_call_id=f"call{position['n']}-{i}",
                )
                for i, (name, args) in enumerate(step)
            }
            return
        middle = len(step) // 2
        yield step[:middle]
        yield step[middle:]

    return FunctionModel(stream_function=stream)
