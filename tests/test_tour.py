import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from plumb.engine.frontend import AskChoice, AskQuestion, Info
from plumb.engine.schemas import Answer
from plumb.engine.tour import entry_choices, first_paragraph, route
from plumb.memory.decisions import load_decisions
from plumb.memory.mastery import load_mastery
from plumb.memory.sessions import read_session
from plumb.memory.tour import load_tours
from plumb.repomap.builder import build_repo_map
from plumb.repomap.model import RepoMap
from tests.fake_model import scripted
from tests.test_chat import chat
from tests.test_plan import FakeFrontEnd

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    # main -> api, cli; api -> db, util; cli -> db; db -> models; util -> models, helpers
    write(
        tmp_path,
        {
            "pyproject.toml": '[project.scripts]\napp = "app.main:run"\n',
            "app/__init__.py": "",
            "app/main.py": "from app import api, cli\n\ndef run():\n    pass\n",
            "app/api.py": "from app import db, util\n",
            "app/cli.py": "from app import db\n",
            "app/db.py": "from app import models\n",
            "app/util.py": "from app import models, helpers\n",
            "app/models.py": "class User:\n    pass\n",
            "app/helpers.py": "def h():\n    pass\n",
            "tests/test_api.py": "from app import api\n",
            "scripts/tool.py": "from app import helpers\n",
        },
    )
    return tmp_path


def repo_map(root: Path) -> RepoMap:
    return build_repo_map(root, root / ".tutor", NOW)


def test_route_is_breadth_first_with_the_core_first(repo: Path) -> None:
    m = repo_map(repo)
    # db (imported by api and cli) comes before util (imported by api only).
    assert route(m, "app/main.py", set()) == [
        "app/main.py",
        "app/api.py",
        "app/cli.py",
        "app/db.py",
        "app/util.py",
        "app/models.py",
        "app/helpers.py",
    ]
    assert route(m, "app/main.py", {"app/api.py"})[:3] == [
        "app/main.py",
        "app/cli.py",
        "app/db.py",
    ]


def test_entry_choices_put_manifests_first_and_skip_tests(repo: Path) -> None:
    write(
        repo,
        {
            "mobile/linux/flutter/generated_plugin_registrant.cc": '#include "x.h"\n',
            "mobile/linux/flutter/x.h": "",
            "web/main.js": "// Generated file. Do not edit.\nimport './x.js';\n",
            "web/x.js": "",
            "mobile/linux/runner/main.cc": '#include "my_app.h"\nint main() {}\n',
            "mobile/linux/runner/my_app.h": "",
        },
    )
    choices = entry_choices(repo_map(repo), repo)
    assert (
        "mobile/linux/flutter/generated_plugin_registrant.cc" not in choices
    )  # By name.
    assert "web/main.js" not in choices  # By its header.
    assert "mobile/linux/runner/main.cc" in choices  # Hand-written: kept.
    assert choices[0] == "app/main.py"
    assert "tests/test_api.py" not in choices
    assert "scripts/tool.py" in choices


def test_first_paragraph_drops_citations_and_labels() -> None:
    text = "db.py opens sessions [app/db.py:1-3] (inferred). It is small.\n\nWhy: because [x.py:1] (inferred)."
    assert first_paragraph(text) == "db.py opens sessions. It is small."


QUESTION = {
    "concept": "Module Boundaries",
    "concept_name": "Module boundaries",
    "question": "Why is this its own module?",
    "citation": "app/db.py:1",
    "options": [
        {"label": "Reuse", "explanation": "Shared."},
        {"label": "Size", "explanation": "Smaller files."},
    ],
}


def stop_text(path: str) -> str:
    return f"{path} wires things up [{path}:1]. It is imported so tests can swap it [{path}:1] (inferred)."


async def test_tour_runs_pauses_and_resumes(repo: Path) -> None:
    stops = [
        "app/main.py",
        "app/api.py",
        "app/cli.py",
        "app/db.py",
        "app/util.py",
        "app/models.py",
        "app/helpers.py",
    ]
    steps: list[str] = []
    for path in stops:
        steps += [stop_text(path), json.dumps(QUESTION)]
    frontend = FakeFrontEnd(
        Answer("option", 1),  # pick app/main.py
        *[Answer("option", 1)] * 4,  # stops 1-4
        Answer("skip"),  # stop 5
        Answer("option", 2),  # "Continue the tour?" -> Stop here
    )
    session = chat(repo, scripted(*steps), frontend)
    await session.handle("/tour")

    picker = frontend.asked[0]
    assert (
        isinstance(picker, AskChoice) and picker.title == "Where should the tour start?"
    )
    assert picker.options[0] == "app/main.py"
    assert [type(a).__name__ for a in frontend.asked[1:]] == ["AskQuestion"] * 5 + [
        "AskChoice"
    ]

    tours = load_tours(repo / ".tutor").tours
    assert tours["app/main.py"].visited == stops[:5]
    assert tours["app/main.py"].queue == stops[5:]
    infos = [e.text for e in frontend.events if isinstance(e, Info)]
    assert infos[-1] == "Tour paused; `/tour app/main.py` resumes (2 stops left)."

    # The stop's first paragraph became the module summary.
    summary = repo_map(repo).files["app/db.py"].summary
    assert (
        summary is not None
        and summary.text
        == "app/db.py wires things up. It is imported so tests can swap it."
    )
    assert "app/db.py wires things up" in session.session.toolbox.repo_map("app/db.py")

    # Memory: confirmed picks and inferred reasons, all as tour.
    decisions = load_decisions(repo / ".tutor").decisions
    assert {d.command for d in decisions} == {"tour"}
    assert {d.label for d in decisions} == {"confirmed", "inferred"}
    assert "module-boundaries" in load_mastery(repo / ".tutor").concepts

    # Resume.
    frontend2 = FakeFrontEnd(
        Answer("option", 1), Answer("option", 1), Answer("option", 1)
    )
    await chat(
        repo,
        scripted(
            stop_text("app/models.py"),
            json.dumps(QUESTION),
            stop_text("app/helpers.py"),
            json.dumps(QUESTION),
        ),
        frontend2,
    ).handle("/tour app/main.py")
    resume = frontend2.asked[0]
    assert isinstance(resume, AskChoice) and resume.options == [
        "Resume (2 stops left)",
        "Start over",
    ]
    assert load_tours(repo / ".tutor").tours["app/main.py"].queue == []
    assert [e.text for e in frontend2.events if isinstance(e, Info)][
        -1
    ] == "That's the end of this route."


async def test_start_over_and_bad_start(repo: Path) -> None:
    frontend = FakeFrontEnd()
    await chat(repo, scripted("unused"), frontend).handle("/tour app/nope.py")
    assert [e.text for e in frontend.events if isinstance(e, Info)] == [
        "`app/nope.py` isn't a file I can see in this repo."
    ]

    frontend = FakeFrontEnd(Answer("skip"), Answer("skip"))
    await chat(
        repo, scripted(stop_text("app/helpers.py"), json.dumps(QUESTION)), frontend
    ).handle("/tour ./app/helpers.py")
    assert load_tours(repo / ".tutor").tours["app/helpers.py"].visited == [
        "app/helpers.py"
    ]


async def test_tour_transcript(repo: Path) -> None:
    frontend = FakeFrontEnd(Answer("skip"))
    await chat(
        repo, scripted(stop_text("app/helpers.py"), json.dumps(QUESTION)), frontend
    ).handle("/tour app/helpers.py")
    transcript = next((repo / ".tutor/sessions").glob("*-chat.jsonl"))
    tour_event = next(e for e in read_session(transcript) if e.type == "tour")
    assert tour_event.payload == {
        "start": "app/helpers.py",
        "queue": ["app/helpers.py"],
        "visited": [],
        "flow": "tour",
    }
    assert isinstance(frontend.asked[0], AskQuestion)
