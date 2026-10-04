"""A throwaway plain-text front end for the session engine."""

import asyncio
from collections.abc import Callable
from typing import Protocol

import typer

from plumb.engine.frontend import (
    AskCheck,
    AskChoice,
    AskQuestion,
    Checked,
    Feedback,
    Info,
    Notice,
    SessionEvent,
    Status,
    Summary,
    Text,
    TextEnd,
)
from plumb.engine.schemas import Answer


def _dim(text: str) -> str:
    return typer.style(text, dim=True)


class TextFrontEnd:
    def __init__(self, read_line: Callable[[str], str] = input) -> None:
        self.read_line = read_line

    def emit(self, event: SessionEvent) -> None:
        match event:
            case Status(text=text):
                typer.echo(_dim(f"· {text}"))
            case Text(text=text):
                typer.echo(text, nl=False)
            case TextEnd():
                typer.echo("\n")
            case Notice(text=text):
                typer.echo(typer.style(f"! {text}", fg="yellow"))
            case Checked(report=report):
                bad = [c for c in report.citations if not c.ok]
                good = len(report.citations) - len(bad)
                typer.echo(_dim(f"Citations checked: {good} ok, {len(bad)} wrong."))
                for citation in bad:
                    typer.echo(_dim(f"  ✗ {citation} - {citation.problem}"))
                for source in report.downgraded:
                    typer.echo(
                        _dim(
                            f"  (documented: {source}) was never seen, so it is shown as inferred."
                        )
                    )
                if report.untagged:
                    typer.echo(
                        _dim(
                            f"  {len(report.untagged)} untagged reasons count as inferred."
                        )
                    )
                typer.echo("")
            case Info(text=text):
                typer.echo(f"{text}\n")
            case Feedback(text=text):
                typer.echo(f"{text}\n")
            case Summary(changes=changes):
                typer.echo(
                    typer.style("Memory updated:", bold=True)
                    if changes
                    else "Nothing to remember this time."
                )
                for change in changes:
                    typer.echo(f"  - {change}")

    async def read_message(self, prompt: str = "> ") -> str | None:
        """A line as typed, or None at end of input."""
        try:
            return (await asyncio.to_thread(self.read_line, prompt)).strip()
        except EOFError:
            return None

    async def _read(self, prompt: str) -> str | None:
        line = await self.read_message(prompt)
        return line.lower() if line is not None else None

    async def ask(self, prompt: AskQuestion | AskCheck | AskChoice) -> Answer:
        if isinstance(prompt, AskChoice):
            typer.echo(typer.style(prompt.title, bold=True))
            for number, option in enumerate(prompt.options, 1):
                typer.echo(f"  {number}) {option}")
            count, keys = len(prompt.options), "s"
        elif isinstance(prompt, AskCheck):
            check = prompt.check
            typer.echo(typer.style("Check question", bold=True))
            typer.echo(check.question)
            for number, option in enumerate(check.options, 1):
                typer.echo(f"  {number}) {option}")
            typer.echo(_dim("  s) skip"))
            count, keys = len(check.options), "s"
        else:
            q = prompt.question
            title = f"Question {prompt.number}/{prompt.total} - {q.concept_name}"
            typer.echo(typer.style(title, bold=True))
            typer.echo(f"{q.question}  {_dim('[' + q.citation + ']')}")
            for number, option in enumerate(q.options, 1):
                typer.echo(f"  {number}) {option.label} - {option.explanation}")
            extra = "?) I don't understand    " if prompt.allow_dont_understand else ""
            typer.echo(_dim(f"  {extra}s) skip    q) skip the rest"))
            count, keys = (
                len(q.options),
                ("?sq" if prompt.allow_dont_understand else "sq"),
            )
        while True:
            reply = await self._read("> ")
            if reply is None or reply == "q" and "q" in keys:
                return Answer("skip_rest")
            if reply == "s":
                return Answer("skip")
            if reply == "?" and "?" in keys:
                return Answer("dont_understand")
            if reply.isdigit() and 1 <= int(reply) <= count:
                return Answer("option", int(reply))
            choices = ", ".join([str(n) for n in range(1, count + 1)] + list(keys))
            typer.echo(_dim(f"Type one of: {choices}"))


class ChatLike(Protocol):
    async def handle(self, message: str) -> bool: ...


async def plain_chat(
    chat: ChatLike, frontend: TextFrontEnd, first_message: str | None = None
) -> None:
    """The chat as a prompt loop. Ends on /quit or end of input."""
    typer.echo(
        "Describe a change you're about to make, or ask about your code. "
        "/help lists the commands.\n"
    )
    if first_message:
        typer.echo(f"> {first_message}")
        if not await chat.handle(first_message):
            return
    while True:
        line = await frontend.read_message()
        if line is None:
            return
        if not await chat.handle(line):
            return
        typer.echo("")
