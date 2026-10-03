from datetime import UTC, datetime
from pathlib import Path

import pytest

from plumb.memory.decisions import Decision, Decisions, load_decisions, save_decisions
from plumb.memory.io import CorruptMemoryFile
from plumb.memory.mastery import Concept, Mastery, load_mastery, save_mastery
from plumb.memory.paths import find_tutor_dir
from plumb.memory.sessions import (
    SessionEvent,
    append_event,
    new_session_path,
    read_session,
)
from plumb.memory.skipped import Skipped, SkippedQuestion, load_skipped, save_skipped

NOW = datetime(2026, 10, 3, 14, 0, 0, tzinfo=UTC)


@pytest.fixture
def tutor_dir(tmp_path: Path) -> Path:
    return tmp_path / ".tutor"


def test_mastery_round_trip(tutor_dir: Path) -> None:
    mastery = Mastery(
        concepts={
            "dependency-injection": Concept(
                name="Dependency injection",
                level="shaky",
                last_seen=NOW,
                times_asked=3,
                times_correct=1,
                files=["src/app/container.py"],
            )
        }
    )
    save_mastery(tutor_dir, mastery)
    assert load_mastery(tutor_dir) == mastery


def test_mastery_rejects_non_slug_key() -> None:
    with pytest.raises(ValueError):
        Mastery.model_validate(
            {
                "concepts": {
                    "Not A Slug": {"name": "x", "level": "solid", "last_seen": NOW}
                }
            }
        )


def test_skipped_round_trip(tutor_dir: Path) -> None:
    skipped = Skipped(
        skipped=[
            SkippedQuestion(
                topic="dependency-injection",
                question="Why is the DB client passed in?",
                command="plan",
                skipped_at=NOW,
            )
        ]
    )
    save_skipped(tutor_dir, skipped)
    assert load_skipped(tutor_dir) == skipped


def test_decisions_round_trip(tutor_dir: Path) -> None:
    decisions = Decisions(
        decisions=[
            Decision(
                decision="Inject the DB client",
                reason="Lets tests swap in a fake",
                label="inferred",
                source_file="src/app/container.py",
                lines=(12, 30),
                command="plan",
                recorded_at=NOW,
            ),
            Decision(
                decision="Use JSON files",
                reason="She can read them",
                label="documented",
                source_file="README.md",
                command="tour",
                recorded_at=NOW,
            ),
        ]
    )
    save_decisions(tutor_dir, decisions)
    assert load_decisions(tutor_dir) == decisions


def test_decision_label_is_restricted() -> None:
    with pytest.raises(ValueError):
        Decision(
            decision="x",
            reason="y",
            label="guessed",  # type: ignore[arg-type]
            source_file="a.py",
            command="plan",
            recorded_at=NOW,
        )


def test_session_round_trip_and_append_only(tutor_dir: Path) -> None:
    path = new_session_path(tutor_dir, "plan", NOW)
    assert path == tutor_dir / "sessions" / "20261003T140000Z-plan.jsonl"
    first = SessionEvent(
        ts=NOW,
        type="tool_call",
        payload={"tool": "read_file", "args": {"path": "a.py"}},
    )
    second = SessionEvent(ts=NOW, type="answer", payload={"option": 2})
    append_event(path, first)
    before = path.read_text()
    append_event(path, second)
    assert path.read_text().startswith(before)
    assert len(path.read_text().splitlines()) == 2
    assert read_session(path) == [first, second]


def test_missing_files_load_empty(tutor_dir: Path) -> None:
    assert load_mastery(tutor_dir) == Mastery()
    assert load_skipped(tutor_dir) == Skipped()
    assert load_decisions(tutor_dir) == Decisions()
    assert read_session(tutor_dir / "sessions" / "nope.jsonl") == []


def test_corrupt_json_raises_and_is_left_untouched(tutor_dir: Path) -> None:
    tutor_dir.mkdir()
    path = tutor_dir / "mastery.json"
    path.write_text("{not json")
    with pytest.raises(CorruptMemoryFile) as error:
        load_mastery(tutor_dir)
    assert error.value.path == path
    assert path.read_text() == "{not json"


def test_corrupt_session_line_names_the_line(tutor_dir: Path) -> None:
    path = new_session_path(tutor_dir, "plan", NOW)
    append_event(path, SessionEvent(ts=NOW, type="answer"))
    with path.open("a") as handle:
        handle.write("garbage\n")
    with pytest.raises(CorruptMemoryFile, match="line 2"):
        read_session(path)


def test_atomic_save_leaves_no_temp_files(tutor_dir: Path) -> None:
    save_mastery(tutor_dir, Mastery())
    save_mastery(tutor_dir, Mastery())
    assert [p.name for p in tutor_dir.iterdir()] == ["mastery.json"]


def test_tutor_dir_uses_git_root(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    assert find_tutor_dir(nested) == tmp_path.resolve() / ".tutor"


def test_tutor_dir_falls_back_to_cwd(tmp_path: Path) -> None:
    assert find_tutor_dir(tmp_path) == tmp_path.resolve() / ".tutor"
