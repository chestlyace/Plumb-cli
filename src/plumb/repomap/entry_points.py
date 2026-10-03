"""Entry points: manifests, file-name rules and import-graph roots."""

import json
import posixpath
import re
import tomllib
from pathlib import Path

from plumb.repomap.imports import JsResolver, PythonResolver
from plumb.repomap.model import EntryPoint

ENTRY_FILE_NAMES = frozenset(
    {
        "main.py",
        "__main__.py",
        "app.py",
        "manage.py",
        "index.js",
        "index.ts",
        "index.tsx",
        "main.js",
        "main.ts",
    }
)


TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec", "integration_test"})
TEST_NAME = re.compile(
    r"^(test_.+\.py|.+_test\.py|.+_test\.dart|conftest\.py"
    r"|.+\.(test|spec)\.(js|jsx|ts|tsx))$"
)


def is_test_file(rel: str) -> bool:
    """Files under a test folder, or named like a test."""
    parts = rel.split("/")
    return any(p in TEST_DIRS for p in parts[:-1]) or bool(TEST_NAME.match(parts[-1]))


def _pyproject(root: Path, manifest: str, resolver: PythonResolver) -> list[EntryPoint]:
    try:
        data = tomllib.loads((root / manifest).read_text(encoding="utf-8"))
    except OSError, tomllib.TOMLDecodeError:
        return []
    scripts = data.get("project", {}).get("scripts", {})
    found: list[EntryPoint] = []
    for name, target in scripts.items() if isinstance(scripts, dict) else []:
        module = str(target).split(":")[0]
        resolution = resolver.resolve(manifest, module, [])
        for path in resolution.targets:
            found.append(EntryPoint(path=path, reason=f"pyproject script {name}"))
    return found


def _package_json(root: Path, manifest: str, files: set[str]) -> list[EntryPoint]:
    try:
        data = json.loads((root / manifest).read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    folder = posixpath.dirname(manifest)
    resolver = JsResolver(files)
    importer = posixpath.join(folder, "package.json")
    found: list[EntryPoint] = []

    def add(spec: object, reason: str) -> None:
        if not isinstance(spec, str):
            return
        target = resolver.resolve(importer, "./" + spec.removeprefix("./")).targets
        if target:
            found.append(EntryPoint(path=target[0], reason=reason))

    add(data.get("main"), "package.json main")
    bin_field = data.get("bin")
    if isinstance(bin_field, str):
        add(bin_field, "package.json bin")
    elif isinstance(bin_field, dict):
        for name, spec in bin_field.items():
            add(spec, f"package.json bin {name}")
    scripts = data.get("scripts")
    if isinstance(scripts, dict):
        for name, command in scripts.items():
            for token in str(command).split():
                if posixpath.normpath(posixpath.join(folder, token)) in files:
                    add(token, f"package.json script {name}")
    return found


def _pubspec(manifest: str, files: set[str]) -> list[EntryPoint]:
    main = posixpath.join(posixpath.dirname(manifest), "lib/main.dart")
    return (
        [EntryPoint(path=main, reason="pubspec lib/main.dart")] if main in files else []
    )


def find_entry_points(
    root: Path, files: list[str], import_graph: dict[str, list[str]]
) -> list[EntryPoint]:
    file_set = set(files)
    python = PythonResolver(files)
    found: list[EntryPoint] = []
    for rel in files:
        name = posixpath.basename(rel)
        if name == "pyproject.toml":
            found += _pyproject(root, rel, python)
        elif name == "package.json":
            found += _package_json(root, rel, file_set)
        elif name == "pubspec.yaml":
            found += _pubspec(rel, file_set)
        if name in ENTRY_FILE_NAMES:
            found.append(EntryPoint(path=rel, reason=f"file name {name}"))

    imported = {target for targets in import_graph.values() for target in targets}
    for rel, targets in import_graph.items():
        if targets and rel not in imported and not is_test_file(rel):
            found.append(EntryPoint(path=rel, reason="import-graph root"))

    reasons: dict[str, list[str]] = {}
    for entry in found:
        reasons.setdefault(entry.path, [])
        if entry.reason not in reasons[entry.path]:
            reasons[entry.path].append(entry.reason)
    return [EntryPoint(path=p, reason="; ".join(r)) for p, r in sorted(reasons.items())]
