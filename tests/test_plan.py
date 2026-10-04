import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelAPIError

from plumb.cli.text_frontend import TextFrontEnd
from plumb.config import Config
from plumb.engine.frontend import (
    AskCheck,
    AskQuestion,
    Checked,
    Notice,
    SessionEvent,
    Summary,
    Text,
)
from plumb.engine.plan import PlanSession
from plumb.engine.pydantic_driver import PydanticAIDriver
from plumb.engine.schemas import Answer
from plumb.memory.decisions import load_decisions
from plumb.memory.mastery import load_mastery
from plumb.memory.sessions import read_session
from plumb.memory.skipped import load_skipped
from plumb.repomap.builder import build_repo_map
from plumb.tools.toolbox import Toolbox
from tests.fake_model import Calls, scripted

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
CONFIG = Config("local", "http://unused/v1", None, step_limit=6)


class FakeFrontEnd:
    def __init__(self, *answers: Answer) -> None:
        self.answers = list(answers)
        self.events: list[SessionEvent] = []
        self.asked: list[AskQuestion | AskCheck] = []

    def emit(self, event: SessionEvent) -> None:
        self.events.append(event)

    async def ask(self, prompt: AskQuestion | AskCheck) -> Answer:
        self.asked.append(prompt)
        return self.answers.pop(0)


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/db.py").write_text(
        "class Db:\n    def query(self):\n        pass\n"
    )
    (tmp_path / "app/main.py").write_text("from app.db import Db\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run([*git, "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        [*git, "commit", "-q", "-m", "Wrap queries in Db for tests"],
        cwd=tmp_path,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return tmp_path, head


QUESTIONS = {
    "questions": [
        {
            "concept": "Data Access Layer",
            "concept_name": "Data access layer",
            "question": "Where should the new query live?",
            "citation": "app/db.py:1-3",
            "options": [
                {"label": "In Db", "explanation": "Keeps queries together."},
                {
                    "label": "In the router",
                    "explanation": "Closer to use, harder to test.",
                },
            ],
        },
        {
            "concept": "imports",
            "concept_name": "Imports",
            "question": "How should main reach Db?",
            "citation": "app/main.py:1",
            "options": [
                {"label": "Import it", "explanation": "As today."},
                {"label": "Inject it", "explanation": "Easier to fake."},
            ],
        },
    ]
}
CHECK = {
    "question": "What does Db wrap?",
    "options": ["Queries", "Routes"],
    "correct": 1,
    "why": "See query().",
}


def session(root: Path, model: object, frontend: FakeFrontEnd) -> PlanSession:
    tutor_dir = root / ".tutor"
    toolbox = Toolbox(root, tutor_dir, build_repo_map(root, tutor_dir, NOW))
    driver = PydanticAIDriver(CONFIG, toolbox, model=model)  # type: ignore[arg-type]
    return PlanSession(driver, toolbox, tutor_dir, frontend, lambda: NOW)


def explanation(head: str) -> str:
    return (
        "Db wraps every query [app/db.py:1-3]. "
        f"Queries live in Db so tests can fake it (documented: {head}). "
        "main imports Db directly because nothing injects it [app/main.py:1] (inferred). "
        "A new endpoint is safest in Db (documented: commit abcdef1) [app/db.py:2]. "
        "See also [app/nope.py:4]."
    )


async def test_full_plan_flow(repo: tuple[Path, str]) -> None:
    root, head = repo
    model = scripted(
        Calls([("read_file", {"path": "app/db.py"}), ("git_log", {})]),
        explanation(head),
        json.dumps(QUESTIONS),
        Calls([("read_file", {"path": "app/db.py", "start_line": 1, "end_line": 2})]),
        "Db is one class that owns every query [app/db.py:1-3].",
        json.dumps(CHECK),
    )
    frontend = FakeFrontEnd(
        Answer("dont_understand"),  # Q1
        Answer("option", 1),  # check question: right
        Answer("option", 1),  # Q1 asked again
        Answer("skip"),  # Q2
    )
    await session(root, model, frontend).run("Add a report endpoint")
    tutor = root / ".tutor"

    # Explanation streamed, then checked.
    streamed = "".join(e.text for e in frontend.events if isinstance(e, Text))
    assert streamed.startswith("Db wraps every query")
    report = next(e for e in frontend.events if isinstance(e, Checked)).report
    assert [str(c) for c in report.citations if not c.ok] == ["app/nope.py:4"]
    assert report.downgraded == ["commit abcdef1"]  # Never seen; the real commit was.
    assert f"(documented: {head})" in report.text

    # Q1 asked, then the check question, then Q1 again without "I don't understand".
    kinds = [type(a).__name__ for a in frontend.asked]
    assert kinds == ["AskQuestion", "AskCheck", "AskQuestion", "AskQuestion"]
    assert frontend.asked[2].allow_dont_understand is False  # type: ignore[union-attr]

    mastery = load_mastery(tutor).concepts
    assert mastery["data-access-layer"].level == "solid"
    assert (
        mastery["data-access-layer"].times_asked,
        mastery["data-access-layer"].times_correct,
    ) == (1, 1)
    assert mastery["data-access-layer"].files == ["app/db.py"]
    assert "imports" not in mastery  # Skipped, so not seen.

    skipped = load_skipped(tutor).skipped
    assert [(s.topic, s.command) for s in skipped] == [("imports", "plan")]

    decisions = load_decisions(tutor).decisions
    labels = [(d.label, d.source_file) for d in decisions]
    assert ("inferred", "app/main.py") in labels
    assert ("confirmed", "app/db.py") in labels
    confirmed = next(d for d in decisions if d.label == "confirmed")
    assert confirmed.decision == "Where should the new query live? -> In Db"
    downgraded = next(d for d in decisions if "endpoint" in d.decision)
    assert (
        downgraded.label == "inferred"
    )  # (documented: commit abcdef1) was never seen.
    # The documented reason has no citation in its sentence, so it is not logged.
    assert not any(d.label == "documented" for d in decisions)

    events = [
        e.type for e in read_session(next((tutor / "sessions").glob("*-plan.jsonl")))
    ]
    for expected in (
        "request",
        "tool_call",
        "tool_result",
        "checked",
        "answer",
        "check_answer",
        "summary",
    ):
        assert expected in events
    assert isinstance(frontend.events[-1], Summary)


async def test_documented_reason_with_citation_is_logged(
    repo: tuple[Path, str],
) -> None:
    root, head = repo
    text = (
        f"Queries live in Db [app/db.py:1-3] so tests can fake it (documented: {head})."
    )
    model = scripted(Calls([("git_log", {})]), text, json.dumps(QUESTIONS))
    await session(root, model, FakeFrontEnd(Answer("skip_rest"))).run("x")
    decisions = load_decisions(root / ".tutor").decisions
    assert [(d.label, d.reason) for d in decisions] == [
        ("documented", f"commit {head}: Wrap queries in Db for tests")
    ]


async def test_skip_rest_logs_every_remaining_question(repo: tuple[Path, str]) -> None:
    root, _ = repo
    model = scripted("Db wraps queries [app/db.py:1-3].", json.dumps(QUESTIONS))
    await session(root, model, FakeFrontEnd(Answer("skip_rest"))).run("x")
    assert [s.topic for s in load_skipped(root / ".tutor").skipped] == [
        "data-access-layer",
        "imports",
    ]
    assert load_mastery(root / ".tutor").concepts == {}


async def test_answering_later_clears_skipped(repo: tuple[Path, str]) -> None:
    root, _ = repo
    model = scripted("Db [app/db.py:1-3].", json.dumps(QUESTIONS))
    await session(root, model, FakeFrontEnd(Answer("skip"), Answer("skip"))).run("x")
    model = scripted("Db [app/db.py:1-3].", json.dumps(QUESTIONS))
    await session(root, model, FakeFrontEnd(Answer("option", 2), Answer("skip"))).run(
        "x"
    )
    assert [s.topic for s in load_skipped(root / ".tutor").skipped] == [
        "imports",
        "imports",
    ]


async def test_wrong_check_answer_stays_shaky(repo: tuple[Path, str]) -> None:
    root, _ = repo
    model = scripted(
        "Db [app/db.py:1-3].",
        json.dumps(QUESTIONS),
        "Db owns queries [app/db.py:1-3].",
        json.dumps(CHECK),
    )
    frontend = FakeFrontEnd(
        Answer("dont_understand"),
        Answer("option", 2),
        Answer("skip"),
        Answer("skip_rest"),
    )
    await session(root, model, frontend).run("x")
    concept = load_mastery(root / ".tutor").concepts["data-access-layer"]
    assert concept.level == "shaky" and concept.times_correct == 0


async def test_model_failure_ends_cleanly(repo: tuple[Path, str]) -> None:
    root, _ = repo
    frontend = FakeFrontEnd()
    await session(root, scripted(ModelAPIError("local", "down")), frontend).run("x")
    assert any(isinstance(e, Notice) for e in frontend.events)
    assert isinstance(frontend.events[-1], Summary) and frontend.asked == []


async def test_text_front_end_reads_answers(capsys: pytest.CaptureFixture[str]) -> None:
    from plumb.engine.schemas import Question

    question = Question.model_validate(QUESTIONS["questions"][0])
    replies = iter(["7", "hello", "2"])
    frontend = TextFrontEnd(read_line=lambda _: next(replies))
    answer = await frontend.ask(AskQuestion(question, 1, 2))
    assert answer == Answer("option", 2)
    out = capsys.readouterr().out
    assert (
        "Question 1/2 - Data access layer" in out
        and "Type one of: 1, 2, ?, s, q" in out
    )

    for reply, expected in (
        ("?", "dont_understand"),
        ("s", "skip"),
        ("q", "skip_rest"),
    ):
        frontend = TextFrontEnd(read_line=lambda _, r=reply: r)
        assert (await frontend.ask(AskQuestion(question, 1, 2))).kind == expected

    def eof(_: str) -> str:
        raise EOFError

    assert (
        await TextFrontEnd(read_line=eof).ask(AskQuestion(question, 1, 2))
    ).kind == "skip_rest"


async def test_too_broad_citation_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "big.py").write_text("".join(f"x{i} = {i}\n" for i in range(200)))
    text = "x is set everywhere [big.py:1-200] because of history (inferred). Fine [big.py:1-60] (inferred)."
    model = scripted(text, json.dumps(QUESTIONS))
    frontend = FakeFrontEnd(Answer("skip_rest"))
    await session(tmp_path, model, frontend).run("x")
    report = next(e for e in frontend.events if isinstance(e, Checked)).report
    broad, fine = report.citations
    assert not broad.ok and broad.problem == "too broad: 200 lines (at most 60)"
    assert fine.ok
    assert [d.lines for d in load_decisions(tmp_path / ".tutor").decisions] == [(1, 60)]
