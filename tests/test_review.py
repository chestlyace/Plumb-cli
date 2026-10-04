import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from plumb.engine.diff import Diff, DiffError, FileDiff, fit, read_diff
from plumb.engine.frontend import AskChoice, AskQuestion, Info
from plumb.engine.review import relevant_decisions
from plumb.engine.schemas import Answer
from plumb.memory.decisions import Decision, Decisions, load_decisions, save_decisions
from plumb.memory.mastery import load_mastery
from plumb.memory.sessions import read_session
from plumb.memory.skipped import Skipped, SkippedQuestion, load_skipped, save_skipped
from plumb.repomap.builder import build_repo_map
from tests.fake_model import scripted
from tests.test_chat import chat
from tests.test_plan import QUESTIONS, FakeFrontEnd

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def git(root: Path, *args: str) -> None:
    subprocess.run([*GIT, *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/db.py").write_text(
        "class Db:\n    def query(self):\n        pass\n"
    )
    (tmp_path / "app/main.py").write_text("from app.db import Db\n")
    (tmp_path / "app/old.py").write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text(".tutor/\n.env\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "first")
    return tmp_path


def allowed(root: Path) -> set[str]:
    return set(build_repo_map(root, root / ".tutor", NOW).files)


def change(root: Path) -> None:
    (root / "app/db.py").write_text(
        "class Db:\n    def query(self):\n        pass\n\n    def report(self):\n        return []\n"
    )
    (root / "app/report.py").write_text(
        "from app.db import Db\n\ndef rows():\n    return Db().report()\n"
    )
    (root / "app/old.py").unlink()
    (root / ".env").write_text("SECRET=1\n")


def test_uncommitted_changes_with_new_and_deleted_files(repo: Path) -> None:
    change(repo)
    diff = read_diff(repo, allowed(repo))
    assert diff.source == "uncommitted changes"
    by_path = {f.path: f for f in diff.files}
    assert set(by_path) == {"app/db.py", "app/report.py", "app/old.py"}  # Not .env.
    assert (by_path["app/db.py"].added, by_path["app/db.py"].removed) == (3, 0)
    assert (
        by_path["app/report.py"].new and "+def rows():" in by_path["app/report.py"].text
    )
    assert by_path["app/old.py"].text == "deleted file: app/old.py (-1)"


def test_nothing_uncommitted_falls_back_to_last_commit(repo: Path) -> None:
    change(repo)
    (repo / ".env").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add report")
    diff = read_diff(repo, allowed(repo))
    assert diff.source.startswith("the last commit (") and "add report" in diff.source
    assert "app/report.py" in diff.paths


def test_since_a_ref_and_bad_refs(repo: Path) -> None:
    change(repo)
    (repo / ".env").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add report")
    (repo / "app/main.py").write_text(
        "from app.db import Db\nfrom app.report import rows\n"
    )
    diff = read_diff(repo, allowed(repo), "HEAD~1")
    assert diff.source == "changes since HEAD~1"
    assert {"app/main.py", "app/report.py"} <= set(diff.paths)
    with pytest.raises(DiffError):
        read_diff(repo, allowed(repo), "no-such-ref")
    with pytest.raises(DiffError, match="not a git ref"):
        read_diff(repo, allowed(repo), "--output=/tmp/x")


def test_fit_puts_decision_files_first_within_the_limit() -> None:
    files = [
        FileDiff(p, "\n".join(["l"] * n), n, 0)
        for p, n in (("a.py", 300), ("b.py", 150), ("c.py", 90))
    ]
    diff = fit(Diff("x", files), first={"b.py"}, limit=400)
    assert [f.path for f in diff.shown] == ["b.py", "c.py"]
    assert [f.path for f in diff.listed] == ["a.py"]
    assert "a.py  +300 -0" in diff.render()


def decision(text: str, file: str, label: str, command: str, at: datetime) -> Decision:
    return Decision(
        decision=text,
        reason="r",
        label=label,
        source_file=file,
        lines=(1, 2),  # type: ignore[arg-type]
        command=command,
        recorded_at=at,  # type: ignore[arg-type]
    )


def test_relevant_decisions() -> None:
    old_plan = decision(
        "old plan", "x.py", "confirmed", "plan", NOW - timedelta(days=3)
    )
    last_plan = [
        decision(f"plan {i}", "y.py", "confirmed", "plan", NOW - timedelta(minutes=i))
        for i in range(2)
    ]
    on_file = decision(
        "about db", "app/db.py", "inferred", "plan", NOW - timedelta(days=5)
    )
    picked = relevant_decisions([old_plan, *last_plan, on_file], ["app/db.py"])
    assert [d.decision for d in picked] == ["about db", "plan 0", "plan 1"]


REVIEW = "Db gained report() [app/db.py:5-6]. D1 followed: queries stay in Db [app/db.py:1-6] (inferred)."
REBUILT = {
    "concept": "anything",
    "concept_name": "Imports",
    "question": "How should report.py reach Db?",
    "citation": "app/report.py:1",
    "options": [
        {"label": "Import it", "explanation": "As now."},
        {"label": "Inject it", "explanation": "Fakeable."},
    ],
}


async def test_review_flow(repo: Path) -> None:
    tutor = repo / ".tutor"
    save_decisions(
        tutor,
        Decisions(
            decisions=[
                decision(
                    "New queries go in Db -> In Db",
                    "app/db.py",
                    "confirmed",
                    "plan",
                    NOW,
                )
            ]
        ),
    )
    save_skipped(
        tutor,
        Skipped(
            skipped=[
                SkippedQuestion(
                    topic="imports",
                    question="How should main reach Db?",
                    command="plan",
                    skipped_at=NOW,
                )
            ]
        ),
    )
    change(repo)
    frontend = FakeFrontEnd(
        Answer("option", 1),  # review Q1
        Answer("skip"),  # review Q2
        Answer("option", 1),  # revisit: the skipped "imports" question
        Answer("option", 2),  # the rebuilt question
    )
    model = scripted(REVIEW, json.dumps(QUESTIONS), json.dumps(REBUILT))
    assert await chat(repo, model, frontend).handle("/review")

    info = [e.text for e in frontend.events if isinstance(e, Info)]
    assert (
        info[0]
        == "Reviewing uncommitted changes: 3 files, +7 -1, against 1 recorded decisions."
    )
    kinds = [type(a).__name__ for a in frontend.asked]
    assert kinds == ["AskQuestion", "AskQuestion", "AskChoice", "AskQuestion"]
    choice = frontend.asked[2]
    assert isinstance(choice, AskChoice)
    assert (
        choice.title == "You skipped 2 questions before. Revisit one?"
    )  # Plus the one just skipped.
    assert choice.options[0].startswith("imports: ")  # The newest skip of each topic.
    rebuilt = frontend.asked[3]
    assert isinstance(rebuilt, AskQuestion) and rebuilt.question.concept == "imports"

    assert (
        load_skipped(tutor).skipped == []
    )  # Both "imports" skips cleared by answering.
    assert "imports" in load_mastery(tutor).concepts
    review_decisions = [
        d for d in load_decisions(tutor).decisions if d.command == "review"
    ]
    assert {d.label for d in review_decisions} == {"inferred", "confirmed"}
    transcript = next((tutor / "sessions").glob("*-chat.jsonl"))
    diff_event = next(e for e in read_session(transcript) if e.type == "diff")
    assert diff_event.payload["flow"] == "review"
    assert diff_event.payload["shown"] == ["app/db.py", "app/old.py", "app/report.py"]


async def test_review_with_nothing_to_review(repo: Path) -> None:
    frontend = FakeFrontEnd()
    await chat(repo, scripted("unused"), frontend).handle("/review")
    texts = [e.text for e in frontend.events if isinstance(e, Info)]
    assert texts == ["Nothing to review: no uncommitted changes."]


async def test_review_reports_a_bad_ref(repo: Path) -> None:
    frontend = FakeFrontEnd()
    await chat(repo, scripted("unused"), frontend).handle("/review nope")
    texts = [e.text for e in frontend.events if isinstance(e, Info)]
    assert texts[0].startswith("I couldn't read the changes:")


async def test_not_now_skips_the_revisit(repo: Path) -> None:
    tutor = repo / ".tutor"
    save_skipped(
        tutor,
        Skipped(
            skipped=[
                SkippedQuestion(
                    topic="imports", question="q?", command="plan", skipped_at=NOW
                )
            ]
        ),
    )
    change(repo)
    frontend = FakeFrontEnd(Answer("skip_rest"), Answer("option", 2))
    await chat(repo, scripted(REVIEW, json.dumps(QUESTIONS)), frontend).handle(
        "/review"
    )
    assert [type(a).__name__ for a in frontend.asked] == ["AskQuestion", "AskChoice"]
    assert {s.topic for s in load_skipped(tutor).skipped} == {
        "imports",
        "data-access-layer",
    }
