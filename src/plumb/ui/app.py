"""The Textual front end: shows engine events and collects answers.

The engine knows nothing about this module; it only sees the FrontEnd
protocol, which TextualFrontEnd implements.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Footer, Header, OptionList, Static

from plumb.engine.frontend import (
    AskCheck,
    AskQuestion,
    Checked,
    Feedback,
    FrontEnd,
    Notice,
    SessionEvent,
    Status,
    Summary,
    Text,
    TextEnd,
)
from plumb.engine.schemas import Answer
from plumb.ui.widgets import ExplanationLog, QuestionPanel

ANSWER_KEYS = {"?": "dont_understand", "s": "skip", "q": "skip_rest"}


class TextualFrontEnd:
    """The engine's FrontEnd, drawn by TutorApp."""

    def __init__(self, app: TutorApp) -> None:
        self.app = app

    def emit(self, event: SessionEvent) -> None:
        log = self.app.query_one(ExplanationLog)
        match event:
            case Status(text=text):
                self.app.query_one("#status", Static).update(f"· {text}")
            case Text(text=chunk):
                log.append_text(chunk)
            case TextEnd():
                log.end_text()
            case Notice(text=text, model=model):
                log.add_line(f"! {text}", "notice")
                if model:
                    self.app.model_name = model
            case Checked(report=report):
                log.checked(report)
            case Feedback(text=text):
                log.add_line(text, "feedback")
            case Summary(changes=changes):
                self.app.query_one("#status", Static).update("· Done")
                self.app.query_one(QuestionPanel).show_summary(changes)
                self.app.finished = True

    async def ask(self, prompt: AskQuestion | AskCheck) -> Answer:
        return await self.app.ask(prompt)


class TutorApp(App[None]):
    CSS_PATH = Path(__file__).with_name("tutor.tcss")
    HORIZONTAL_BREAKPOINTS: ClassVar[list[tuple[int, str]]] = [
        (0, "-narrow"),
        (100, "-wide"),
    ]
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("1", "answer('1')", "Option 1", show=False),
        Binding("2", "answer('2')", "Option 2", show=False),
        Binding("3", "answer('3')", "Option 3", show=False),
        Binding("question_mark", "answer('?')", "I don't understand"),
        Binding("s", "answer('s')", "Skip"),
        Binding("q", "answer('q')", "Skip the rest / exit"),
        Binding("ctrl+c", "quit", "Quit", priority=True),
    ]

    def __init__(
        self,
        request: str,
        model_name: str,
        run_session: Callable[[FrontEnd], Awaitable[None]],
    ) -> None:
        super().__init__()
        self.request = request
        self._model_name = model_name
        self.run_session = run_session
        self.finished = False
        self._pending: asyncio.Future[Answer] | None = None
        self._keys: list[str] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    @model_name.setter
    def model_name(self, value: str) -> None:
        self._model_name = value
        self.sub_title = f"{self.request} · {value}"

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="panes"):
            log = ExplanationLog()
            log.border_title = "Explanation"
            yield log
            yield QuestionPanel()
        yield Static("· Starting", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "tutor plan"
        self.model_name = self._model_name
        self.run_worker(self._session(), exclusive=True)

    async def _session(self) -> None:
        try:
            await self.run_session(TextualFrontEnd(self))
        except Exception as error:  # noqa: BLE001 - show it rather than crash the screen
            self.query_one(ExplanationLog).add_line(
                f"! The session stopped: {error}", "notice"
            )
            self.query_one(QuestionPanel).show_summary([])
            self.finished = True

    async def ask(self, prompt: AskQuestion | AskCheck) -> Answer:
        self._keys = self.query_one(QuestionPanel).show_question(prompt)
        self.query_one("#status", Static).update(
            "· Your turn: pick an option, or ? / s / q"
        )
        self._pending = asyncio.get_running_loop().create_future()
        try:
            return await self._pending
        finally:
            self._pending = None
            self._keys = []
            # The app may be closing with the question still open.
            for panel in self.query(QuestionPanel).results(QuestionPanel):
                panel.show_waiting("Thinking…")

    def _resolve(self, key: str) -> None:
        if self._pending is None or self._pending.done() or key not in self._keys:
            return
        if key.isdigit():
            self._pending.set_result(Answer("option", int(key)))
        else:
            self._pending.set_result(Answer(ANSWER_KEYS[key]))  # type: ignore[arg-type]

    def action_answer(self, key: str) -> None:
        if self.finished and key == "q":
            self.exit()
            return
        self._resolve(key)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self._resolve(event.option.id)

    def on_key(self, event: object) -> None:
        if self.finished and getattr(event, "key", None) == "enter":
            self.exit()
