import hashlib
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import plumb.tools
from plumb.memory.decisions import Decision, Decisions, save_decisions
from plumb.memory.mastery import Concept, Mastery, save_mastery
from plumb.memory.sessions import SessionEvent, append_event, new_session_path
from plumb.memory.skipped import Skipped, SkippedQuestion, save_skipped
from plumb.repomap.builder import build_repo_map
from plumb.tools.toolbox import Toolbox

NOW = datetime(2026, 10, 3, 14, 0, 0, tzinfo=UTC)


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    write(
        root,
        "app/db.py",
        "class Db:\n    def query(self):\n        return 'SELECT 1'\n",
    )
    write(
        root, "app/main.py", "from app.db import Db\n\ndef main():\n    Db().query()\n"
    )
    write(root, "app/big.py", "".join(f"x{i} = {i}\n" for i in range(1000)))
    write(root, "README.md", "# Demo\nTODO: write docs\n")
    write(root, ".env", "SECRET=1\n")
    write(root, "node_modules/dep/index.js", "module.exports = 'SELECT';\n")
    (root / "logo.png").write_bytes(b"\x89PNG\x00\x00")
    write(tmp_path, "outside.txt", "outside secret\n")
    (root / "link.txt").symlink_to(tmp_path / "outside.txt")
    write(root, ".gitignore", ".env\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "first commit")
    write(root, "app/db.py", (root / "app/db.py").read_text() + "# tuned\n")
    git(root, "commit", "-q", "-am", "tune db")
    return root


@pytest.fixture
def tools(repo: Path) -> Toolbox:
    tutor_dir = repo / ".tutor"
    repo_map = build_repo_map(repo, tutor_dir, NOW)
    save_mastery(
        tutor_dir,
        Mastery(
            concepts={
                "dependency-injection": Concept(
                    name="Dependency injection",
                    level="shaky",
                    last_seen=NOW,
                    files=["app/main.py"],
                )
            }
        ),
    )
    save_skipped(
        tutor_dir,
        Skipped(
            skipped=[
                SkippedQuestion(
                    topic="caching",
                    question="Why no cache?",
                    command="plan",
                    skipped_at=NOW,
                )
            ]
        ),
    )
    save_decisions(
        tutor_dir,
        Decisions(
            decisions=[
                Decision(
                    decision="Db is a class",
                    reason="Lets tests fake it",
                    label="inferred",
                    source_file="app/db.py",
                    lines=(1, 3),
                    command="plan",
                    recorded_at=NOW,
                )
            ]
        ),
    )
    append_event(
        new_session_path(tutor_dir, "plan", NOW),
        SessionEvent(ts=NOW, type="answer", payload={"text": "the database layer"}),
    )
    return Toolbox(repo, tutor_dir, repo_map)


# read_file


def test_read_file(tools: Toolbox) -> None:
    out = tools.read_file("app/db.py", 2, 3)
    assert out.splitlines() == [
        "app/db.py (lines 2-3 of 4)",
        "2|     def query(self):",
        "3|         return 'SELECT 1'",
    ]


def test_read_file_truncates_with_a_hint(tools: Toolbox) -> None:
    out = tools.read_file("app/big.py")
    assert out.splitlines()[0] == "app/big.py (lines 1-400 of 1000)"
    assert out.splitlines()[-1] == (
        "… truncated at 400 lines; call read_file with start_line=401 for more."
    )


def test_read_file_cuts_long_lines(repo: Path) -> None:
    write(repo, "long.py", "x = '" + "a" * 1000 + "'\n")
    tools = Toolbox(repo, repo / ".tutor", build_repo_map(repo, repo / ".tutor", NOW))
    line = tools.read_file("long.py").splitlines()[1]
    assert len(line) < 320 and line.endswith(" …")


def test_read_file_past_end(tools: Toolbox) -> None:
    assert tools.read_file("app/db.py", 50).startswith("Error: app/db.py has 4 lines")


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("../outside.txt", "outside the repo"),
        ("app/../../outside.txt", "outside the repo"),
        ("/etc/passwd", "outside the repo"),
        (".env", "not a file the tutor can read"),
        ("node_modules/dep/index.js", "not a file the tutor can read"),
        ("logo.png", "not a file the tutor can read"),
        ("link.txt", "not a file the tutor can read"),
        ("missing.py", "not a file the tutor can read"),
        ("app", "is a folder; use list_dir"),
    ],
)
def test_read_file_refusals(tools: Toolbox, path: str, reason: str) -> None:
    out = tools.read_file(path)
    assert out.startswith("Error:") and reason in out


def test_read_file_absolute_path_inside_repo(tools: Toolbox, repo: Path) -> None:
    assert tools.read_file(str(repo / "app/db.py")).startswith("app/db.py")


def test_file_swapped_for_symlink_after_map_is_refused(
    tools: Toolbox, repo: Path
) -> None:
    (repo / "README.md").unlink()
    (repo / "README.md").symlink_to(repo.parent / "outside.txt")
    out = tools.read_file("README.md")
    assert out.startswith("Error:") and "symlink" in out


# list_dir


def test_list_dir(tools: Toolbox) -> None:
    # .env (git-ignored), node_modules/, logo.png (binary) and link.txt (symlink) are hidden.
    assert tools.list_dir().splitlines() == [
        "./ (3 entries)",
        "app/",
        ".gitignore",
        "README.md",
    ]
    assert tools.list_dir("app").splitlines()[1:] == ["big.py", "db.py", "main.py"]


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("..", "outside the repo"),
        ("node_modules", "not a folder"),
        ("app/db.py", "is a file"),
    ],
)
def test_list_dir_refusals(tools: Toolbox, path: str, reason: str) -> None:
    out = tools.list_dir(path)
    assert out.startswith("Error:") and reason in out


# grep


def test_grep(tools: Toolbox) -> None:
    out = tools.grep("SELECT")
    assert out.splitlines()[1:] == [
        "app/db.py:3: return 'SELECT 1'"
    ]  # not node_modules


def test_grep_invalid_regex_searches_literally(tools: Toolbox) -> None:
    out = tools.grep("query(")
    assert "searched as plain text" in out
    assert "app/main.py:4: Db().query()" in out


def test_grep_ignore_case_and_path(tools: Toolbox) -> None:
    assert "README.md:2" in tools.grep("todo", ignore_case=True)
    assert tools.grep("todo", path="app", ignore_case=True).startswith("No matches")


def test_grep_caps_matches(tools: Toolbox) -> None:
    out = tools.grep(r"^x\d+", path="app/big.py").splitlines()
    assert len(out) == 102
    assert out[-1].startswith("… stopped at 100 matches")


def test_grep_refuses_outside(tools: Toolbox) -> None:
    assert tools.grep("secret", path="..").startswith("Error:")


# git_log


def test_git_log(tools: Toolbox) -> None:
    repo_log = tools.git_log().splitlines()
    assert repo_log[0] == "Last 2 commits for the repo"
    assert repo_log[1].endswith("tune db")
    assert tools.git_log("app/main.py").splitlines()[1].endswith("first commit")
    assert "capped at 50" in tools.git_log(limit=500)
    assert tools.git_log("../outside.txt").startswith("Error:")


# repo_map


def test_repo_map_overview_and_file(tools: Toolbox) -> None:
    overview = tools.repo_map()
    assert "app/main.py  (file name main.py; import-graph root)" in overview
    assert "  app/ (3)" in overview
    detail = tools.repo_map("app/db.py")
    assert "  class Db  lines 1-3" in detail
    assert "  function Db.query  lines 2-3" in detail
    assert "Imported by:\n  app/main.py" in detail
    assert "  - tune db" in detail
    assert "not written yet" in detail
    assert tools.repo_map("app").startswith("Error:")


# memory


def test_memory(tools: Toolbox) -> None:
    assert "dependency-injection: Dependency injection - shaky" in tools.memory(
        "mastery"
    )
    assert tools.memory("mastery", "main.py").startswith("1 mastery")
    assert tools.memory("mastery", "nothing").startswith("No mastery")
    assert "[plan] caching: Why no cache?" in tools.memory("skipped", "CACHE")
    assert "(inferred; app/db.py:1-3)" in tools.memory("decisions", "fake")
    assert "answer: " in tools.memory("sessions", "database layer")
    assert tools.memory("secrets").startswith("Error: kind must be one of")


def test_memory_reports_corrupt_file(tools: Toolbox) -> None:
    (tools.tutor_dir / "mastery.json").write_text("{broken")
    assert tools.memory("mastery") == "Error: a memory file is unreadable: mastery.json"


# never writes


def snapshot(root: Path) -> dict[str, tuple[int, str]]:
    return {
        str(p.relative_to(root)): (
            p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest(),
        )
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and not p.is_symlink()
        and ".git/" not in str(p.relative_to(root)) + "/"
    }


def test_tools_never_change_the_disk(tools: Toolbox, repo: Path) -> None:
    before = snapshot(repo)
    for call in (
        lambda: tools.read_file("app/db.py"),
        lambda: tools.read_file("../outside.txt"),
        lambda: tools.list_dir("app"),
        lambda: tools.grep("Db"),
        lambda: tools.git_log("app/db.py"),
        lambda: tools.repo_map(),
        lambda: tools.repo_map("app/main.py"),
        *(
            lambda k=k: tools.memory(k)
            for k in ("mastery", "skipped", "decisions", "sessions")
        ),
    ):
        call()
    assert snapshot(repo) == before


def test_tools_package_has_no_write_calls() -> None:
    write_calls = re.compile(
        r"write_text|write_bytes|\.write\(|unlink|rmdir|mkdir|rename|replace\(|"
        r"save_json|append_jsonl|save_|append_event|open\([^)]*['\"][wax+]"
    )
    package = Path(plumb.tools.__file__).parent
    offenders = [
        f"{path.name}:{n}: {line.strip()}"
        for path in package.glob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if write_calls.search(line) and '.replace("\\\\", "/")' not in line
    ]
    assert offenders == []
