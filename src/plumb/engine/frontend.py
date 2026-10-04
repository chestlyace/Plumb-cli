"""What a session shows and asks. The engine defines this; front ends
implement it. The engine never imports a front end."""

from dataclasses import dataclass, field
from typing import Protocol

from plumb.engine.citations import Report
from plumb.engine.schemas import Answer, CheckQuestion, Question


@dataclass(frozen=True)
class Status:
    """What the tutor is doing, e.g. "Reading backend/app/database.py"."""

    text: str


@dataclass(frozen=True)
class Text:
    """A chunk of streamed explanation."""

    text: str


@dataclass(frozen=True)
class TextEnd:
    """The current streamed explanation is complete."""


@dataclass(frozen=True)
class Notice:
    """Something she should know, e.g. the fallback model is in use."""

    text: str
    model: str | None = None  # Set when the model answering has changed.


@dataclass(frozen=True)
class Checked:
    """Citations and labels in the explanation just shown, after checking."""

    report: Report


@dataclass(frozen=True)
class Feedback:
    text: str


@dataclass(frozen=True)
class Summary:
    changes: list[str] = field(default_factory=list)


SessionEvent = Status | Text | TextEnd | Notice | Checked | Feedback | Summary


@dataclass(frozen=True)
class AskQuestion:
    question: Question
    number: int
    total: int
    allow_dont_understand: bool = True


@dataclass(frozen=True)
class AskCheck:
    check: CheckQuestion


class FrontEnd(Protocol):
    def emit(self, event: SessionEvent) -> None: ...

    async def ask(self, prompt: AskQuestion | AskCheck) -> Answer:
        """For a check question, `skip_rest` and `dont_understand` mean skip."""
        ...
