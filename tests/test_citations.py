from datetime import UTC, datetime
from pathlib import Path

import pytest

from plumb.engine.citations import check, find, parse_citation, strip_citations
from plumb.repomap.builder import build_repo_map
from plumb.tools.scope import Scope


@pytest.fixture
def scope(tmp_path: Path) -> Scope:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/db.py").write_text("".join(f"line {i}\n" for i in range(1, 101)))
    (tmp_path / "app/main.py").write_text("a\nb\nc\n")
    (tmp_path / "Dockerfile").write_text("FROM x\nRUN y\n")
    return Scope(
        tmp_path,
        build_repo_map(
            tmp_path, tmp_path / ".tutor", datetime(2026, 10, 4, tzinfo=UTC)
        ),
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[app/db.py:15]", [("app/db.py", ((15, 15),))]),
        ("[app/db.py:15-20]", [("app/db.py", ((15, 20),))]),
        ("[app/db.py:15–20]", [("app/db.py", ((15, 20),))]),
        ("[app/db.py:15—20]", [("app/db.py", ((15, 20),))]),
        ("[app/db.py:15 to 20]", [("app/db.py", ((15, 20),))]),
        ("[app/db.py:15-20, 28-29]", [("app/db.py", ((15, 20), (28, 29)))]),
        ("[app/db.py:15-20,28]", [("app/db.py", ((15, 20), (28, 28)))]),
        ("[app/db.py:15-20; 28-29]", [("app/db.py", ((15, 20), (28, 29)))]),
        ("[app/db.py:15 and 28]", [("app/db.py", ((15, 15), (28, 28)))]),
        ("[app/db.py#L15-L20]", [("app/db.py", ((15, 20),))]),
        ("[app/db.py#L15]", [("app/db.py", ((15, 15),))]),
        ("[app/db.py:L15-L20]", [("app/db.py", ((15, 20),))]),
        ("[app/db.py line 15]", [("app/db.py", ((15, 15),))]),
        ("[app/db.py lines 15-20]", [("app/db.py", ((15, 20),))]),
        ("[app/db.py:lines 15-20]", [("app/db.py", ((15, 20),))]),
        ("[app/db.py, lines 15-20]", [("app/db.py", ((15, 20),))]),
        (
            "[app/db.py:15-20, app/main.py:2]",
            [("app/db.py", ((15, 20),)), ("app/main.py", ((2, 2),))],
        ),
        (
            "[app/db.py:15; app/main.py:1-2]",
            [("app/db.py", ((15, 15),)), ("app/main.py", ((1, 2),))],
        ),
        ("(app/db.py:15-20)", [("app/db.py", ((15, 20),))]),
        ("`app/db.py:15-20`", [("app/db.py", ((15, 20),))]),
        ("see app/db.py:15 here", [("app/db.py", ((15, 15),))]),
        ("[Dockerfile:2]", []),  # No extension and no folder: not a path.
        ("[./Dockerfile:2]", [("./Dockerfile", ((2, 2),))]),
        ("[click here](https://x.io)", []),
    ],
)
def test_find_every_form(
    text: str, expected: list[tuple[str, tuple[tuple[int, int], ...]]]
) -> None:
    assert [(f.path, f.ranges) for f in find(text)] == expected


def test_check_verifies_every_range(scope: Scope) -> None:
    text = (
        "Lists work [app/db.py:15-20, 28-29]. "
        "GitHub style [app/db.py#L95-L99]. "
        "Too far [app/main.py:2-9]. "
        "Too broad [app/db.py:1-80]. "
        "Missing [app/nope.py:3]. "
        "A URL is not a citation: api.example.com:443 or `localhost:8000`."
    )
    report = check(text, scope, "", [])
    assert [(str(c), c.ok) for c in report.citations] == [
        ("app/db.py:15-20", True),
        ("app/db.py:28-29", True),
        ("app/db.py:95-99", True),
        ("app/main.py:2-9", False),
        ("app/db.py:1-80", False),
        ("app/nope.py:3", False),
    ]
    assert report.written == {
        "app/db.py:15-20, 28-29": True,
        "app/db.py#L95-L99": True,
        "app/main.py:2-9": False,
        "app/db.py:1-80": False,
        "app/nope.py:3": False,
    }


def test_reason_uses_its_first_valid_range(scope: Scope) -> None:
    report = check(
        "Kept apart [app/nope.py:1, app/db.py:3-4] (inferred).", scope, "", []
    )
    (reason,) = report.reasons
    assert reason.citation is not None and str(reason.citation) == "app/db.py:3-4"


def test_parse_citation_in_any_form(scope: Scope) -> None:
    for raw in (
        "app/db.py:3-4",
        "[app/db.py#L3-L4]",
        "app/db.py lines 3 to 4",
        "app/db.py:3-4, 9",
    ):
        citation = parse_citation(scope, raw)
        assert citation is not None and (
            citation.file,
            citation.start,
            citation.end,
        ) == ("app/db.py", 3, 4)
    assert parse_citation(scope, "no citation here") is None


def test_strip_citations() -> None:
    text = "db.py opens sessions [app/db.py:1-3] (inferred), see `app/db.py#L4` and (app/main.py:1, 2)."
    assert strip_citations(text) == "db.py opens sessions, see and."
