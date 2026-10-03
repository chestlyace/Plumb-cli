import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from plumb.repomap.builder import build_repo_map
from plumb.repomap.files import list_repo_files
from plumb.repomap.model import Summary
from plumb.repomap.parse import parse_file

NOW = datetime(2026, 10, 3, 14, 0, 0, tzinfo=UTC)


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write(tmp_path, "pyproject.toml", '[project.scripts]\nmytool = "app.cli:main"\n')
    write(tmp_path, "src/app/__init__.py", "")
    write(
        tmp_path,
        "src/app/cli.py",
        "from .db import connect\nimport requests\n\ndef main():\n    connect()\n",
    )
    write(
        tmp_path,
        "src/app/db.py",
        "class Db:\n    def query(self):\n        pass\n\ndef connect():\n    return Db()\n",
    )
    write(tmp_path, "src/app/pkg/__init__.py", "")
    write(tmp_path, "src/app/pkg/util.py", "def helper():\n    pass\n")
    write(
        tmp_path, "src/app/uses_pkg.py", "from app.pkg import util\nfrom . import db\n"
    )
    write(
        tmp_path,
        "web/package.json",
        json.dumps({"main": "src/index.ts", "dependencies": {}}),
    )
    write(
        tmp_path,
        "web/src/index.ts",
        "import { api } from './api';\nimport React from 'react';\nimport x from '@scope/lib/sub';\n",
    )
    write(tmp_path, "web/src/api.ts", "export const api = () => 1;\n")
    write(tmp_path, "mobile/pubspec.yaml", "name: mobile\n")
    write(
        tmp_path,
        "mobile/lib/main.dart",
        "import 'package:flutter/material.dart';\nimport 'dart:io';\n"
        "import 'package:mobile/core/theme.dart';\nimport 'screens/home.dart';\n"
        "class App {}\n",
    )
    write(tmp_path, "mobile/lib/core/theme.dart", "class Theme {}\n")
    write(tmp_path, "mobile/lib/screens/home.dart", "import '../core/theme.dart';\n")
    write(tmp_path, "notes.unknownext", "just text\n")
    write(tmp_path, "node_modules/dep/index.js", "module.exports = 1;\n")
    write(tmp_path, ".hidden/secret.py", "x = 1\n")
    write(tmp_path, "ignored_by_git.log", "log\n")
    write(tmp_path, ".gitignore", "*.log\n")
    (tmp_path / "image.bin").write_bytes(b"\x89PNG\x00\x00data")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "first commit")
    write(
        tmp_path,
        "src/app/db.py",
        (tmp_path / "src/app/db.py").read_text() + "# change\n",
    )
    git(tmp_path, "commit", "-q", "-am", "touch db")
    return tmp_path


def test_file_listing_respects_git_and_fixed_ignores(repo: Path) -> None:
    files = list_repo_files(repo)
    assert "src/app/cli.py" in files
    assert "node_modules/dep/index.js" not in files
    assert ".hidden/secret.py" not in files
    assert "ignored_by_git.log" not in files
    assert "image.bin" not in files


def test_file_listing_without_git_uses_fixed_list(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "")
    write(tmp_path, "node_modules/x.js", "")
    write(tmp_path, ".cache/y.py", "")
    assert list_repo_files(tmp_path) == ["a.py"]


def test_python_symbols_and_imports() -> None:
    result = parse_file(
        "m.py",
        "import os, x.y as z\nfrom .m import a, b\nclass C:\n  def f(self): pass\n",
    )
    assert result.parsed == "full"
    assert [(s.kind, s.name, s.parent) for s in result.symbols] == [
        ("class", "C", None),
        ("function", "f", "C"),
    ]
    assert [(i.module, i.names) for i in result.imports] == [
        ("os", []),
        ("x.y", []),
        (".m", ["a"]),
        (".m", ["b"]),
    ]
    assert [i.line for i in result.imports] == [1, 1, 2, 2]


def test_js_imports() -> None:
    source = "import a from './a';\nexport * from '../b';\nconst c = require('c');\nimport('./d');\n"
    result = parse_file("x/y.tsx", source)
    assert result.parsed == "full"
    assert [i.module for i in result.imports] == ["./a", "../b", "c", "./d"]
    assert [i.line for i in result.imports] == [1, 2, 3, 4]


def test_other_grammar_gets_symbols_only() -> None:
    result = parse_file("scripts/run.sh", "source ./env.sh\nbuild() {\n  make\n}\n")
    assert result.parsed == "symbols"
    assert [s.name for s in result.symbols] == ["build"]
    assert result.imports == []


def test_unsupported_language_falls_back() -> None:
    result = parse_file("notes.unknownext", "just text\n")
    assert result.parsed == "fallback"
    assert result.language is None


def test_parse_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from plumb.repomap import parse

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("grammar download failed")

    monkeypatch.setattr(parse.tslp, "process", boom)
    assert parse.parse_file("a.py", "x = 1\n").parsed == "fallback"


def test_build_map(repo: Path) -> None:
    repo_map = build_repo_map(repo, repo / ".tutor", NOW)
    files = repo_map.files

    assert files["src/app/cli.py"].parsed == "full"
    assert files["mobile/lib/main.dart"].parsed == "full"
    assert files["notes.unknownext"].parsed == "fallback"
    assert files["notes.unknownext"].git.commits == 1

    graph = repo_map.import_graph
    assert graph["src/app/cli.py"] == ["src/app/db.py"]
    assert graph["src/app/uses_pkg.py"] == ["src/app/db.py", "src/app/pkg/util.py"]
    assert graph["web/src/index.ts"] == ["web/src/api.ts"]
    assert files["src/app/cli.py"].external_imports == ["requests"]
    assert files["web/src/index.ts"].external_imports == ["@scope/lib", "react"]
    assert graph["mobile/lib/main.dart"] == [
        "mobile/lib/core/theme.dart",
        "mobile/lib/screens/home.dart",
    ]
    assert graph["mobile/lib/screens/home.dart"] == ["mobile/lib/core/theme.dart"]
    assert files["mobile/lib/main.dart"].external_imports == ["dart:io", "flutter"]

    db_git = files["src/app/db.py"].git
    assert db_git.commits == 2
    assert db_git.recent_subjects == ["touch db", "first commit"]
    assert [c.subject for c in repo_map.recent_commits] == ["touch db", "first commit"]

    entries = {e.path: e.reason for e in repo_map.entry_points}
    assert "pyproject script mytool" in entries["src/app/cli.py"]
    assert entries["web/src/index.ts"] == (
        "package.json main; file name index.ts; import-graph root"
    )
    assert entries["mobile/lib/main.dart"] == (
        "pubspec lib/main.dart; import-graph root"
    )

    assert (repo / ".tutor" / "repo-map.json").exists()


def test_refresh_reuses_unchanged_and_reparses_changed(repo: Path) -> None:
    tutor_dir = repo / ".tutor"
    first = build_repo_map(repo, tutor_dir, NOW)
    summary = Summary(
        text="Database helpers.",
        sha256=first.files["src/app/db.py"].sha256,
        written_at=NOW,
    )
    first.files["src/app/db.py"].summary = summary
    first.files["src/app/cli.py"].summary = summary.model_copy(
        update={"sha256": first.files["src/app/cli.py"].sha256}
    )
    from plumb.memory.io import save_json

    save_json(tutor_dir / "repo-map.json", first)

    # Touch db.py without changing content: mtime changes, hash matches, summary kept.
    db = repo / "src/app/db.py"
    os.utime(db, ns=(db.stat().st_atime_ns, db.stat().st_mtime_ns + 10_000))
    # Change cli.py: re-parsed, summary dropped.
    write(repo, "src/app/cli.py", "def main():\n    pass\n")
    # Delete api.ts; add a new file.
    (repo / "web/src/api.ts").unlink()
    write(repo, "src/app/new.py", "from .db import Db\n")

    second = build_repo_map(repo, tutor_dir, NOW)
    assert second.files["src/app/db.py"].summary == summary
    assert second.files["src/app/cli.py"].summary is None
    assert second.import_graph["src/app/cli.py"] == []
    assert "web/src/api.ts" not in second.files
    assert second.import_graph["web/src/index.ts"] == []
    assert second.import_graph["src/app/new.py"] == ["src/app/db.py"]


def test_build_without_git_history(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "import b\n")
    write(tmp_path, "b.py", "")
    repo_map = build_repo_map(tmp_path, tmp_path / ".tutor", NOW)
    assert repo_map.recent_commits == []
    assert repo_map.import_graph["a.py"] == ["b.py"]
    assert ".tutor/repo-map.json" not in repo_map.files


def test_many_parses_do_not_read_freed_memory() -> None:
    # Regression: Node.text read freed bytes and crashed the process.
    for i in range(300):
        source = f"from .m{i} import a as b, c\nimport x{i}.y as z\n" * 20
        result = parse_file(f"f{i}.py", source)
        assert result.imports[0].module == f".m{i}"


def test_dart_imports() -> None:
    source = (
        "import 'package:a/b.dart';\nimport '../c.dart' as c show C;\n"
        "export 'src/d.dart';\npart 'e.g.dart';\nclass X {}\n"
    )
    result = parse_file("lib/x.dart", source)
    assert result.parsed == "full"
    assert [(i.module, i.line) for i in result.imports] == [
        ("package:a/b.dart", 1),
        ("../c.dart", 2),
        ("src/d.dart", 3),
        ("e.g.dart", 4),
    ]


def test_python_prefers_the_copy_nearest_the_importer(tmp_path: Path) -> None:
    # Two `app` packages: the importer's own folder must win over the shorter path.
    write(tmp_path, "app/__init__.py", "")
    write(tmp_path, "app/database.py", "")
    write(tmp_path, "backend/app/__init__.py", "")
    write(tmp_path, "backend/app/database.py", "")
    write(tmp_path, "backend/app/main.py", "from app.database import x\n")
    write(tmp_path, "app/main.py", "from app.database import x\n")
    repo_map = build_repo_map(tmp_path, tmp_path / ".tutor", NOW)
    assert repo_map.import_graph["backend/app/main.py"] == ["backend/app/database.py"]
    assert repo_map.import_graph["app/main.py"] == ["app/database.py"]
