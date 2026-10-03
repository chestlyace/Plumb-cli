import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from plumb.repomap.builder import build_repo_map
from plumb.repomap.entry_points import is_test_file
from plumb.repomap.parse import parse_file
from plumb.repomap.worker import IsolatedParser

NOW = datetime(2026, 10, 3, 14, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        (
            "main.go",
            'package main\nimport (\n  "fmt"\n  u "github.com/me/proj/pkg/util"\n)\n',
            ['"fmt"', '"github.com/me/proj/pkg/util"'],
        ),
        (
            "A.java",
            "import com.me.db.Repo;\nimport static com.me.U.trim;\nimport java.util.*;\n",
            ["com.me.db.Repo", "com.me.U.trim", "java.util"],
        ),
        ("A.kt", "import com.me.db.Repo\nimport x.y as z\n", ["com.me.db.Repo", "x.y"]),
        ("A.swift", "import UIKit\n", ["UIKit"]),
        (
            "main.rs",
            "mod db;\nuse crate::db::Repo;\nuse std::io::{self, Read};\n",
            ["db", "crate::db::Repo", "std::io", "std::io::Read"],
        ),
        (
            "a.c",
            '#include <stdio.h>\n#include "util/str.h"\n',
            ["<stdio.h>", '"util/str.h"'],
        ),
        (
            "A.cs",
            "using System;\nusing X = Me.Other.Thing;\n",
            ["System", "Me.Other.Thing"],
        ),
        ("a.rb", "require 'json'\nrequire_relative 'lib/db'\n", ['"json"', '"lib/db"']),
        (
            "a.php",
            "<?php\nuse App\\Models\\User;\nrequire_once 'lib/db.php';\n",
            ["App\\Models\\User", '"lib/db.php"'],
        ),
        ("a.lua", "local x = require('mod.sub')\n", ['"mod.sub"']),
        (
            "a.php",
            "<?php\nuse function A\\b;\nuse App\\{X, Y as Z};\n",
            ["A\\b", "App\\X", "App\\Y"],
        ),
        ("lib.rs", "use {a::b, c};\n", ["a::b", "c"]),
    ],
)
def test_generic_import_extraction(path: str, source: str, expected: list[str]) -> None:
    result = parse_file(path, source)
    assert result.parsed == "full"
    assert [i.module for i in result.imports] == expected


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_generic_resolution(tmp_path: Path) -> None:
    # Go: package folders by import path ending.
    write(
        tmp_path,
        "go/cmd/main.go",
        'package main\nimport (\n  "fmt"\n  "github.com/me/proj/pkg/util"\n)\n',
    )
    write(tmp_path, "go/pkg/util/a.go", "package util\n")
    write(tmp_path, "go/pkg/util/b.go", "package util\n")
    # Java and Kotlin: packages mirror folders; a static import names a member.
    write(
        tmp_path,
        "jvm/src/main/java/com/me/App.java",
        "import com.me.db.Repo;\nimport static com.me.db.Repo.find;\nimport java.util.List;\n",
    )
    write(tmp_path, "jvm/src/main/java/com/me/db/Repo.java", "class Repo {}\n")
    write(tmp_path, "jvm/src/main/kotlin/com/me/Ui.kt", "import com.me.db.Repo\n")
    # Rust: mod and crate:: paths.
    write(tmp_path, "rs/src/main.rs", "mod db;\nuse crate::db::Repo;\nuse std::io;\n")
    write(tmp_path, "rs/src/db.rs", "pub struct Repo;\n")
    # C: quoted include from an include folder; system include is external.
    write(tmp_path, "c/src/main.c", '#include <stdio.h>\n#include "util/str.h"\n')
    write(tmp_path, "c/include/util/str.h", "int f(void);\n")
    # Ruby: require_relative.
    write(tmp_path, "rb/app.rb", "require 'json'\nrequire_relative 'lib/db'\n")
    write(tmp_path, "rb/lib/db.rb", "class Db; end\n")

    repo_map = build_repo_map(tmp_path, tmp_path / ".tutor", NOW)
    graph = repo_map.import_graph
    files = repo_map.files

    assert graph["go/cmd/main.go"] == ["go/pkg/util/a.go", "go/pkg/util/b.go"]
    assert files["go/cmd/main.go"].external_imports == ["fmt"]
    assert graph["jvm/src/main/java/com/me/App.java"] == [
        "jvm/src/main/java/com/me/db/Repo.java"
    ]
    assert files["jvm/src/main/java/com/me/App.java"].external_imports == ["java"]
    assert graph["jvm/src/main/kotlin/com/me/Ui.kt"] == [
        "jvm/src/main/java/com/me/db/Repo.java"
    ]
    assert graph["rs/src/main.rs"] == ["rs/src/db.rs"]
    assert files["rs/src/main.rs"].external_imports == ["std"]
    assert graph["c/src/main.c"] == ["c/include/util/str.h"]
    assert files["c/src/main.c"].external_imports == ["stdio.h"]
    assert graph["rb/app.rb"] == ["rb/lib/db.rb"]
    assert files["rb/app.rb"].external_imports == ["json"]


def test_test_files_are_not_import_graph_roots(tmp_path: Path) -> None:
    write(tmp_path, "pkg/core.py", "")
    write(tmp_path, "pkg/cli.py", "from pkg import core\n")
    write(tmp_path, "tests/test_core.py", "from pkg import core\n")
    write(tmp_path, "pkg/core_test.py", "from pkg import core\n")
    write(tmp_path, "web/a.test.ts", "import x from './b';\n")
    write(tmp_path, "web/b.ts", "")
    repo_map = build_repo_map(tmp_path, tmp_path / ".tutor", NOW)
    roots = [e.path for e in repo_map.entry_points if "import-graph root" in e.reason]
    assert roots == ["pkg/cli.py"]
    assert repo_map.import_graph["tests/test_core.py"] == ["pkg/core.py"]


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("tests/x.py", True),
        ("a/__tests__/b.js", True),
        ("mobile/integration_test/app.dart", True),
        ("test_x.py", True),
        ("x_test.dart", True),
        ("src/x.spec.tsx", True),
        ("conftest.py", True),
        ("src/latest.py", False),
        ("src/contest.ts", False),
    ],
)
def test_is_test_file(rel: str, expected: bool) -> None:
    assert is_test_file(rel) is expected


FAKE_WORKER = """
import json, os, sys, time
proto = os.fdopen(os.dup(1), "wb")
for line in sys.stdin.buffer:
    req = json.loads(line)
    if req["rel"] == "crash.go":
        os.abort()
    if req["rel"] == "hang.go":
        time.sleep(60)
    out = {"language": "go", "parsed": "full", "symbols": [], "imports": []}
    proto.write(json.dumps(out).encode() + b"\\n")
    proto.flush()
"""


def test_isolated_parser_survives_crash_and_hang() -> None:
    command = [sys.executable, "-c", FAKE_WORKER]
    with IsolatedParser(timeout=1.0, command=command) as parser:
        assert parser.parse("ok.go", "").parsed == "full"
        crashed = parser.parse("crash.go", "")
        assert (crashed.language, crashed.parsed) == ("go", "fallback")
        assert parser.parse("ok.go", "").parsed == "full"
        assert parser.parse("hang.go", "").parsed == "fallback"
        assert parser.parse("ok.go", "").parsed == "full"


def test_real_worker_parses() -> None:
    with IsolatedParser() as parser:
        result = parser.parse("main.go", 'package main\nimport "fmt"\nfunc main() {}\n')
    assert result.parsed == "full"
    assert [i.module for i in result.imports] == ['"fmt"']
    assert [s.name for s in result.symbols] == ["main"]


def test_one_segment_name_does_not_match_a_folder(tmp_path: Path) -> None:
    write(tmp_path, "ios/AppDelegate.swift", "import Flutter\nimport UIKit\n")
    write(tmp_path, "macos/Flutter/Registrant.swift", "import Foundation\n")
    write(tmp_path, "rs/main.rs", "mod db;\n")
    write(tmp_path, "rs/db/mod.rs", "pub fn f() {}\n")
    repo_map = build_repo_map(tmp_path, tmp_path / ".tutor", NOW)
    assert repo_map.import_graph["ios/AppDelegate.swift"] == []
    assert repo_map.files["ios/AppDelegate.swift"].external_imports == [
        "Flutter",
        "UIKit",
    ]
    assert repo_map.import_graph["rs/main.rs"] == ["rs/db/mod.rs"]
