import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Markdown, OptionList, Static

import plumb.engine
from plumb.engine.frontend import FrontEnd
from plumb.memory.decisions import load_decisions
from plumb.memory.mastery import load_mastery
from plumb.memory.skipped import load_skipped
from plumb.ui.app import TutorApp
from plumb.ui.widgets import ExplanationLog, QuestionPanel
from tests.fake_model import Calls, scripted
from tests.test_plan import CHECK, QUESTIONS, session

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


def make_app(repo: Path, *steps: object) -> TutorApp:
    async def run_session(frontend: FrontEnd) -> None:
        await session(repo, scripted(*steps), frontend).run("Add a report")  # type: ignore[arg-type]

    return TutorApp("Add a report", "local", run_session)


async def wait_for(pilot: Pilot, condition: Callable[[], bool]) -> None:
    for _ in range(200):
        if condition():
            return
        await pilot.pause(0.02)
    raise AssertionError("condition never became true")


def title(app: TutorApp) -> str:
    return str(app.query_one("#question-title", Static).render())


def options(app: TutorApp) -> list[str]:
    option_list = app.query_one("#options", OptionList)
    return [
        str(option_list.get_option_at_index(i).prompt)
        for i in range(option_list.option_count)
    ]


async def test_plan_runs_inside_textual(repo: Path) -> None:
    app = make_app(
        repo,
        Calls([("read_file", {"path": "app/db.py"})]),
        EXPLANATION,
        json.dumps(QUESTIONS),
        "Db owns every query [app/db.py:1-3].",
        json.dumps(CHECK),
    )
    async with app.run_test(size=(130, 45)) as pilot:
        await wait_for(pilot, lambda: title(app).startswith("Question 1/2"))

        # Explanation panel: streamed markdown, citations as code, check line.
        log = app.query_one(ExplanationLog)
        first = log.query(Markdown).first()
        assert "`[app/db.py:1-3]`" in first.source
        assert (
            "~~`[app/nope.py:4]`~~" in first.source
        )  # Struck through after the check.
        lines = [str(s.render()) for s in log.query(Static)]
        assert any(
            l.startswith("✓ 2 citations ok, ✗ 1 wrong: app/nope.py:4") for l in lines
        )

        # Option select with "I don't understand", skip and skip the rest.
        assert options(app) == [
            "1  In Db - Keeps queries together.",
            "2  In the router - Closer to use, harder to test.",
            "?  I don't understand",
            "s  Skip",
            "q  Skip the rest",
        ]
        await pilot.press("question_mark")
        await wait_for(pilot, lambda: title(app) == "Check question")
        assert options(app) == ["1  Queries", "2  Routes", "s  Skip"]
        await pilot.press("q")  # Not an option for a check question: ignored.
        await pilot.press("1")
        await wait_for(pilot, lambda: title(app).startswith("Question 1/2"))
        assert any("Right. See query()." in str(s.render()) for s in log.query(Static))
        assert "?  I don't understand" not in options(app)

        await pilot.press("down", "enter")  # Arrow keys + Enter pick option 2.
        await wait_for(pilot, lambda: title(app).startswith("Question 2/2"))
        await pilot.press("s")
        await wait_for(pilot, lambda: title(app) == "Memory updated")
        assert app.finished
        await pilot.press("q")
    assert app.return_code == 0

    concept = load_mastery(repo / ".tutor").concepts["data-access-layer"]
    assert (concept.level, concept.times_correct) == ("solid", 1)
    confirmed = [
        d for d in load_decisions(repo / ".tutor").decisions if d.label == "confirmed"
    ]
    assert confirmed[0].decision.endswith("-> In the router")
    assert [s.topic for s in load_skipped(repo / ".tutor").skipped] == ["imports"]


async def test_status_bar_and_header(repo: Path) -> None:
    app = make_app(
        repo,
        Calls([("read_file", {"path": "app/db.py"})]),
        EXPLANATION,
        json.dumps(QUESTIONS),
    )
    async with app.run_test(size=(130, 45)) as pilot:
        await wait_for(pilot, lambda: title(app).startswith("Question 1/2"))
        assert app.sub_title == "Add a report · local"
        status = str(app.query_one("#status", Static).render())
        assert status == "· Your turn: pick an option, or ? / s / q"
        await pilot.press("q")
        await wait_for(pilot, lambda: app.finished)


async def test_narrow_terminal_stacks_the_panes(repo: Path) -> None:
    app = make_app(repo, "Db [app/db.py:1-3].", json.dumps(QUESTIONS))
    async with app.run_test(size=(80, 40)) as pilot:
        await wait_for(pilot, lambda: title(app).startswith("Question 1/2"))
        assert app.screen.has_class("-narrow")
        log, panel = app.query_one(ExplanationLog), app.query_one(QuestionPanel)
        assert panel.region.y > log.region.y and panel.region.x == log.region.x


async def test_session_error_is_shown_not_crashed(repo: Path) -> None:
    async def broken(frontend: FrontEnd) -> None:
        raise RuntimeError("memory file is unreadable")

    app = TutorApp("x", "local", broken)
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: app.finished)
        lines = [str(s.render()) for s in app.query_one(ExplanationLog).query(Static)]
        assert "! The session stopped: memory file is unreadable" in lines


def test_engine_never_imports_the_ui() -> None:
    package = Path(plumb.engine.__file__).parent
    pattern = re.compile(
        r"^\s*(from|import)\s+(textual|plumb\.ui|plumb\.cli)\b", re.MULTILINE
    )
    offenders = [p.name for p in package.glob("*.py") if pattern.search(p.read_text())]
    assert offenders == []
