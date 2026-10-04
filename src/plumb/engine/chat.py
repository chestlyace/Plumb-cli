"""A chat: each message runs the plan flow or gets an answer; slash commands."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from plumb.engine import prompts
from plumb.engine.driver import Driver
from plumb.engine.events import FallbackUsed, TurnDone
from plumb.engine.frontend import FrontEnd, Info, Status
from plumb.engine.plan import PlanSession, fallback_notice
from plumb.engine.recorder import Recorder
from plumb.engine.review import ReviewFlow
from plumb.engine.tour import TourFlow
from plumb.memory.skipped import load_skipped
from plumb.tools.toolbox import Toolbox

HELP = """\
**Describe a change** you're about to make and I'll walk you through what it
touches in your code, then ask you a few questions about it.
**Ask a question** about your code and I'll answer it with citations.

- `/plan <change>` - always do the walkthrough and questions
- `/ask <question>` - always just answer
- `/review` - after you've made a change: what changed, against your
  earlier decisions, then a few questions. `/review <git ref>` reviews
  everything since that ref, e.g. `/review HEAD~1`
- `/skipped` - questions you skipped earlier
- `/tour` - a guided tour of your code from an entry point outward, one
  file per stop; `/tour <file>` starts at that file, and a tour resumes
  where you left it
- `/help` - this message
- `/quit` - leave (Ctrl+C works too)

While I'm working, Esc cancels. Your answers are saved as you go."""


class MessageKind(BaseModel):
    kind: Literal["change", "question"]


class ChatSession:
    def __init__(
        self,
        driver: Driver,
        toolbox: Toolbox,
        tutor_dir: Path,
        frontend: FrontEnd,
        now: Callable[[], datetime],
    ) -> None:
        self.driver = driver
        self.frontend = frontend
        self.tutor_dir = tutor_dir
        self.recorder = Recorder(tutor_dir, "chat", now)
        self.session = PlanSession(
            driver, toolbox, tutor_dir, frontend, now, self.recorder
        )
        self.review = ReviewFlow(self.session)
        self.tour = TourFlow(self.session)

    async def handle(self, message: str) -> bool:
        """Handle one message. Returns False when she asked to quit."""
        text = message.strip()
        if not text:
            return True
        command, _, rest = text.partition(" ")
        rest = rest.strip()
        match command.lower():
            case "/quit" | "/exit":
                return False
            case "/help":
                self.frontend.emit(Info(HELP))
            case "/skipped":
                self.frontend.emit(Info(self._skipped()))
            case "/review":
                await self.review.run(rest or None)
            case "/tour":
                await self.tour.run(rest or None)
            case "/plan" | "/ask" if not rest:
                self.frontend.emit(Info(f"Add what you want after `{command}`."))
            case "/plan":
                await self.session.run(rest)
            case "/ask":
                await self.session.answer(rest)
            case _ if command.startswith("/"):
                self.frontend.emit(Info(f"Unknown command `{command}`. Try `/help`."))
            case _:
                await self._route(text)
        return True

    async def _route(self, text: str) -> None:
        self.frontend.emit(Status("Reading your message"))
        kind = await self._classify(text)
        if kind == "change":
            await self.session.run(text)
        else:
            await self.session.answer(text)

    async def _classify(self, text: str) -> str:
        """change or question. If the model can't tell, answer it as a question:
        that never quizzes her, and /plan gets the walkthrough."""
        result: str | None = None
        async for event in self.driver.run_turn(
            prompts.CLASSIFY_INSTRUCTIONS, text, output_type=MessageKind
        ):
            if isinstance(event, TurnDone):
                result = event.output.kind
            elif isinstance(event, FallbackUsed):
                self.recorder.log(
                    "fallback", to_model=event.to_model, reason=event.reason
                )
                self.frontend.emit(fallback_notice(event.to_model))
        self.recorder.log("classified", text=text, kind=result or "unknown")
        return result or "question"

    def _skipped(self) -> str:
        skipped = load_skipped(self.tutor_dir).skipped
        if not skipped:
            return "Nothing skipped."
        lines = ["**Skipped questions** - ask about any of them, or `/plan` again:"]
        lines += [f"- {s.skipped_at:%b %d} · {s.topic}: {s.question}" for s in skipped]
        return "\n".join(lines)
