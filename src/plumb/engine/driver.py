"""The driver interface the session engine calls. Implementations are swappable:
a model-driven loop today, a code-driven pipeline using the same tools later."""

from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol

from plumb.engine.events import Event


class Driver(Protocol):
    def run_turn(
        self,
        instructions: str,
        prompt: str,
        *,
        history: Sequence[Any] = (),
        output_type: type[Any] | None = None,
    ) -> AsyncIterator[Event]:
        """Run one turn, yielding events; the last is TurnDone or TurnStopped.

        With `output_type`, TurnDone.output is an instance of it; otherwise
        TurnDone.text holds the answer.
        """
        ...
