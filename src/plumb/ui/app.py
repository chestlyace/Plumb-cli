"""The Textual chat: shows engine events and collects her messages and answers.

The engine knows nothing about this module; it only sees the FrontEnd
protocol, which TextualFrontEnd implements.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Protocol

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.worker import Worker, WorkerState

from plumb.engine.frontend import (
    AskCheck,
    AskChoice,
    AskQuestion,
    Checked,
    Feedback,
    FrontEnd,
    Info,
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
WELCOME = (
    "**Hi!** Describe a change you're about to make and I'll walk you through "
    "what it touches in your code, or ask me how something in it works. "
    "`/help` lists the commands."
)


class Chat(Protocol):
    async def handle(self, message: str) -> bool: ...


class TextualFrontEnd:
    """The engine's FrontEnd, drawn by TutorApp."""

    def __init__(self, app: TutorApp) -> None:
        self.app = app

    def emit(self, event: SessionEvent) -> None:
        log = self.app.query_one(ExplanationLog)
        match event:
            case Status(text=text):
                self.app.set_status(text)
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
            case Info(text=text):
                log.add_markdown(text)
            case Feedback(text=text):
                log.add_line(text, "feedback")
            case Summary(changes=changes):
                self.app.query_one(QuestionPanel).show_summary(changes)

    async def ask(self, prompt: AskQuestion | AskCheck | AskChoice) -> Answer:
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
        Binding("4", "answer('4')", "Option 4", show=False),
        Binding("5", "answer('5')", "Option 5", show=False),
        Binding("6", "answer('6')", "Option 6", show=False),
        Binding("7", "answer('7')", "Option 7", show=False),
        Binding("8", "answer('8')", "Option 8", show=False),
        Binding("9", "answer('9')", "Option 9", show=False),
        Binding("question_mark", "answer('?')", "I don't understand"),
        Binding("s", "answer('s')", "Skip"),
        Binding("q", "answer('q')", "Skip the rest"),
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+c", "quit", "Quit", priority=True),
    ]

    def __init__(
        self,
        repo_name: str,
        model_name: str,
        make_chat: Callable[[FrontEnd], Chat],
        first_message: str | None = None,
    ) -> None:
        super().__init__()
        self.repo_name = repo_name
        self._model_name = model_name
        self.make_chat = make_chat
        self.first_message = first_message
        self.chat: Chat | None = None
        self.busy = False
        self._pending: asyncio.Future[Answer] | None = None
        self._keys: list[str] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    @model_name.setter
    def model_name(self, value: str) -> None:
        self._model_name = value
        self.sub_title = f"{self.repo_name} · {value}"

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="panes"):
            with Vertical(id="left"):
                log = ExplanationLog()
                log.border_title = "Conversation"
                yield log
                yield Input(
                    placeholder="Describe a change, or ask about your code…  (/help)",
                    id="prompt",
                )
            yield QuestionPanel()
        yield Static("", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Plumb"
        self.model_name = self._model_name
        self.chat = self.make_chat(TextualFrontEnd(self))
        self.query_one(ExplanationLog).add_markdown(WELCOME)
        self.query_one(QuestionPanel).show_idle()
        if self.first_message:
            self.send(self.first_message)
        else:
            self.query_one("#prompt", Input).focus()

    def set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(f"· {text}" if text else "")

    # Messages

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.input.value = ""
        if event.value.strip() and not self.busy:
            self.send(event.value.strip())

    def send(self, message: str) -> None:
        self.query_one(ExplanationLog).add_user_message(message)
        self.busy = True
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = True
        self.set_status("Working… (Esc cancels)")
        self.run_worker(
            self._handle(message), exclusive=True, group="request", exit_on_error=False
        )

    async def _handle(self, message: str) -> None:
        assert self.chat is not None
        keep_going = await self.chat.handle(message)
        if not keep_going:
            self.exit()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "request" or event.state not in (
            WorkerState.SUCCESS,
            WorkerState.CANCELLED,
            WorkerState.ERROR,
        ):
            return
        log = self.query_one(ExplanationLog)
        if event.state == WorkerState.CANCELLED:
            log.end_text()
            log.add_line("Cancelled. Answers you already gave are saved.", "notice")
            self.query_one(QuestionPanel).show_idle()
        elif event.state == WorkerState.ERROR:
            log.add_line(f"! Something went wrong: {event.worker.error}", "notice")
            self.query_one(QuestionPanel).show_idle()
        self.busy = False
        self.set_status("")
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = False
        prompt.focus()

    def action_cancel(self) -> None:
        for worker in self.workers:
            if worker.group == "request" and worker.is_running:
                worker.cancel()

    # Answers

    async def ask(self, prompt: AskQuestion | AskCheck | AskChoice) -> Answer:
        self._keys = self.query_one(QuestionPanel).show_question(prompt)
        self.set_status("Your turn: pick an option, or ? / s / q   (Esc cancels)")
        self._pending = asyncio.get_running_loop().create_future()
        try:
            return await self._pending
        finally:
            self._pending = None
            self._keys = []
            # The app may be closing with the question still open.
            for panel in self.query(QuestionPanel).results(QuestionPanel):
                panel.show_waiting("Thinking…")
            if self.is_running:
                self.set_status("Working… (Esc cancels)")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Answer keys only act while a question is open, so typing in the input
        is never captured; Esc only while a request runs."""
        if action == "answer":
            return self._pending is not None
        if action == "cancel":
            return self.busy
        return True

    def _resolve(self, key: str) -> None:
        if self._pending is None or self._pending.done() or key not in self._keys:
            return
        if key.isdigit():
            self._pending.set_result(Answer("option", int(key)))
        else:
            self._pending.set_result(Answer(ANSWER_KEYS[key]))  # type: ignore[arg-type]

    def action_answer(self, key: str) -> None:
        self._resolve(key)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self._resolve(event.option.id)


def run_chat(
    repo_name: str,
    model_name: str,
    make_chat: Callable[[FrontEnd], Chat],
    first_message: str | None = None,
) -> None:
    TutorApp(repo_name, model_name, make_chat, first_message).run()
