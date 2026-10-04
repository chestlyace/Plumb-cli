import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Input, Markdown, OptionList, Static

import plumb.engine
from plumb.engine.frontend import FrontEnd
from plumb.memory.decisions import load_decisions
from plumb.memory.mastery import load_mastery
from plumb.memory.skipped import load_skipped
from plumb.ui.app import TutorApp
from plumb.ui.widgets import ExplanationLog, QuestionPanel
from tests.fake_model import Calls, scripted
from tests.test_chat import CHANGE, QUESTION, chat
from tests.test_plan import CHECK, QUESTIONS

EXPLANATION = (
    "## Where it goes\n\nDb wraps every query [app/db.py:1-3]. "
    "main imports Db directly because nothing injects it [app/main.py:1] (inferred). "
    "See [app/nope.py:4]."
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/db.py").write_text(
        "class Db:\n    def query(self):\n        pass\n"
    )
    (tmp_path / "app/main.py").write_text("from app.db import Db\n")
    return tmp_path


def make_app(repo: Path, *steps: object, first: str | None = None) -> TutorApp:
    model = scripted(*steps)

    def make_chat(frontend: FrontEnd):  # type: ignore[no-untyped-def]
        return chat(repo, model, frontend)

    return TutorApp("repo", "local", make_chat, first_message=first)


async def wait_for(pilot: Pilot, condition: Callable[[], bool]) -> None:
    for _ in range(250):
        if condition():
            return
        await pilot.pause(0.02)
    raise AssertionError("condition never became true")


def title(app: TutorApp) -> str:
    return str(app.query_one("#question-title", Static).render())


def options(app: TutorApp) -> list[str]:
    return [str(o.prompt) for o in app.query_one("#options", OptionList).options]


def lines(app: TutorApp) -> list[str]:
    return [str(s.render()) for s in app.query_one(ExplanationLog).query(Static)]


def prompt(app: TutorApp) -> Input:
    return app.query_one("#prompt", Input)


async def type_message(pilot: Pilot, text: str) -> None:
    await pilot.press(*["space" if c == " " else c for c in text])
    await pilot.press("enter")


async def test_chat_runs_plan_with_typed_message(repo: Path) -> None:
    app = make_app(
        repo,
        CHANGE,
        Calls([("read_file", {"path": "app/db.py"})]),
        EXPLANATION,
        json.dumps(QUESTIONS),
        "Db owns every query [app/db.py:1-3].",
        json.dumps(CHECK),
    )
    async with app.run_test(size=(130, 45)) as pilot:
        assert prompt(app).has_focus
        # Typing letters that are also answer keys must reach the input.
        await type_message(pilot, "add a stats query")
        assert "> add a stats query" in lines(app)
        await wait_for(pilot, lambda: title(app).startswith("Question 1/2"))
        assert prompt(app).disabled

        log = app.query_one(ExplanationLog)
        reply = log.query(".explanation").results(Markdown).__next__()
        assert "[`app/db.py:1-3`]" in reply.source
        assert "[~~`app/nope.py:4`~~]" in reply.source
        assert any(
            l.startswith("✓ 2 citations ok, ✗ 1 wrong: app/nope.py:4")
            for l in lines(app)
        )
        assert options(app)[-3:] == [
            "?  I don't understand",
            "s  Skip",
            "q  Skip the rest",
        ]

        await pilot.press("question_mark")
        await wait_for(pilot, lambda: title(app) == "Check question")
        await pilot.press("q")  # Not an option for a check question: ignored.
        await pilot.press("1")
        await wait_for(pilot, lambda: title(app).startswith("Question 1/2"))
        assert "?  I don't understand" not in options(app)
        await pilot.press("down", "enter")
        await wait_for(pilot, lambda: title(app).startswith("Question 2/2"))
        await pilot.press("s")
        await wait_for(pilot, lambda: title(app) == "Memory updated")
        await wait_for(pilot, lambda: not prompt(app).disabled)
        assert prompt(app).has_focus

        await type_message(pilot, "/help")
        await wait_for(pilot, lambda: len(log.query(".info")) == 2)

    concept = load_mastery(repo / ".tutor").concepts["data-access-layer"]
    assert (concept.level, concept.times_correct) == ("solid", 1)
    confirmed = [
        d for d in load_decisions(repo / ".tutor").decisions if d.label == "confirmed"
    ]
    assert confirmed[0].decision.endswith("-> In the router")
    assert [s.topic for s in load_skipped(repo / ".tutor").skipped] == ["imports"]


async def test_first_message_and_header(repo: Path) -> None:
    app = make_app(repo, EXPLANATION, json.dumps(QUESTIONS), first="/plan Add a report")
    async with app.run_test(size=(130, 45)) as pilot:
        await wait_for(pilot, lambda: title(app).startswith("Question 1/2"))
        assert "> /plan Add a report" in lines(app)
        assert app.sub_title == "repo · local"
        status = str(app.query_one("#status", Static).render())
        assert status.startswith("· Your turn")


async def test_escape_cancels_and_keeps_answers(repo: Path) -> None:
    app = make_app(repo, EXPLANATION, json.dumps(QUESTIONS), first="/plan Add a report")
    async with app.run_test(size=(130, 45)) as pilot:
        await wait_for(pilot, lambda: title(app).startswith("Question 1/2"))
        await pilot.press("1")
        await wait_for(pilot, lambda: title(app).startswith("Question 2/2"))
        await pilot.press("escape")
        await wait_for(pilot, lambda: not prompt(app).disabled)
        assert "Cancelled. Answers you already gave are saved." in lines(app)
        assert title(app) == "Your turn"
        await pilot.press("1")  # No question open: goes to the input as text.
        assert prompt(app).value == "1"
    assert "data-access-layer" in load_mastery(repo / ".tutor").concepts


async def test_quit_command_exits(repo: Path) -> None:
    app = make_app(repo, QUESTION)
    async with app.run_test() as pilot:
        await type_message(pilot, "/quit")
        await pilot.pause(0.2)
    assert app.return_code == 0


async def test_narrow_terminal_stacks_the_panes(repo: Path) -> None:
    app = make_app(repo, "Db [app/db.py:1-3].", json.dumps(QUESTIONS), first="/plan x")
    async with app.run_test(size=(80, 40)) as pilot:
        await wait_for(pilot, lambda: title(app).startswith("Question 1/2"))
        assert app.screen.has_class("-narrow")
        left, panel = app.query_one("#left"), app.query_one(QuestionPanel)
        assert panel.region.y > left.region.y and panel.region.x == left.region.x


async def test_errors_are_shown_and_the_chat_continues(repo: Path) -> None:
    class Broken:
        async def handle(self, message: str) -> bool:
            raise RuntimeError("memory file is unreadable")

    app = TutorApp("repo", "local", lambda frontend: Broken(), first_message="hi")
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: not prompt(app).disabled)
        assert "! Something went wrong: memory file is unreadable" in lines(app)


def test_engine_never_imports_the_ui() -> None:
    package = Path(plumb.engine.__file__).parent
    pattern = re.compile(
        r"^\s*(from|import)\s+(textual|plumb\.ui|plumb\.cli)\b", re.MULTILINE
    )
    offenders = [p.name for p in package.glob("*.py") if pattern.search(p.read_text())]
    assert offenders == []


async def test_choice_prompt_in_the_panel(repo: Path) -> None:
    from plumb.engine.frontend import AskChoice
    from plumb.engine.schemas import Answer

    got: list[Answer] = []

    class Chooser:
        def __init__(self, frontend: FrontEnd) -> None:
            self.frontend = frontend

        async def handle(self, message: str) -> bool:
            got.append(
                await self.frontend.ask(
                    AskChoice("Revisit one?", ["imports: q?", "Not now"])
                )
            )
            return True

    app = TutorApp("repo", "local", Chooser, first_message="/review")
    async with app.run_test(size=(130, 45)) as pilot:
        await wait_for(pilot, lambda: title(app) == "Revisit one?")
        assert options(app) == ["1  imports: q?", "2  Not now"]
        await pilot.press("3")  # Not an option: ignored.
        await pilot.press("2")
        await wait_for(pilot, lambda: bool(got))
    assert got == [Answer("option", 2)]
