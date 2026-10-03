"""Build or refresh .tutor/repo-map.json."""

import hashlib
import posixpath
import re
from datetime import datetime
from pathlib import Path

from plumb.memory.io import load_json, save_json
from plumb.repomap.entry_points import find_entry_points
from plumb.repomap.files import list_repo_files
from plumb.repomap.git import read_history
from plumb.repomap.imports import (
    DartResolver,
    GenericResolver,
    JsResolver,
    PythonResolver,
)
from plumb.repomap.model import (
    FILE_NAME,
    MAP_VERSION,
    FileRecord,
    GitInfo,
    Import,
    RepoMap,
)
from plumb.repomap.parse import (
    FULL_LANGUAGES,
    ParseResult,
    detect_language,
    parse_file,
)
from plumb.repomap.worker import IsolatedParser


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


JS_LANGUAGES = frozenset({"javascript", "typescript", "tsx"})


def _parse(rel: str, source: str, isolated: IsolatedParser) -> ParseResult:
    """Python, JS/TS and Dart parse in-process; other grammars in the worker."""
    language = detect_language(rel)
    if language is None or language in FULL_LANGUAGES.values():
        return parse_file(rel, source)
    return isolated.parse(rel, source)


def _record(
    root: Path, rel: str, previous: FileRecord | None, isolated: IsolatedParser
) -> FileRecord:
    """Reuse the previous record when size and mtime match, or when the hash does."""
    path = root / rel
    stat = path.stat()
    if (
        previous
        and previous.size == stat.st_size
        and previous.mtime_ns == stat.st_mtime_ns
    ):
        return previous
    digest = _sha256(path)
    if previous and previous.sha256 == digest:
        return previous.model_copy(
            update={"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        )
    result = _parse(rel, path.read_text(encoding="utf-8", errors="replace"), isolated)
    return FileRecord(
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha256=digest,
        language=result.language,
        parsed=result.parsed,
        symbols=result.symbols,
        imports=[
            Import(module=i.module, line=i.line, names=i.names) for i in result.imports
        ],
    )


def _dart_packages(root: Path, files: dict[str, FileRecord]) -> dict[str, str]:
    """Package name -> folder, from each pubspec.yaml's top-level `name:` line."""
    packages: dict[str, str] = {}
    for rel in files:
        if posixpath.basename(rel) != "pubspec.yaml":
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r"^name:\s*['\"]?([A-Za-z0-9_]+)", text, re.MULTILINE)
        if match:
            packages[match.group(1)] = posixpath.dirname(rel)
    return packages


def _resolve(root: Path, files: dict[str, FileRecord]) -> dict[str, list[str]]:
    """Resolve every import against the current file set; return the import graph."""
    python = PythonResolver(files)
    js = JsResolver(files)
    dart = DartResolver(files, _dart_packages(root, files))
    generic = GenericResolver({rel: record.language for rel, record in files.items()})
    graph: dict[str, list[str]] = {}
    for rel, record in files.items():
        if record.parsed != "full":
            continue
        resolved_imports: list[Import] = []
        external: set[str] = set()
        edges: set[str] = set()
        for item in record.imports:
            if record.language == "python":
                resolution = python.resolve(rel, item.module, item.names)
            elif record.language == "dart":
                resolution = dart.resolve(rel, item.module)
            elif record.language in JS_LANGUAGES:
                resolution = js.resolve(rel, item.module)
            else:
                resolution = generic.resolve(rel, item.module)
            if resolution.external:
                external.add(resolution.external)
            targets = [t for t in resolution.targets if t != rel]
            edges.update(targets)
            resolved = targets[0] if targets else None
            resolved_imports.append(item.model_copy(update={"resolved": resolved}))
        record.imports = resolved_imports
        record.external_imports = sorted(external)
        graph[rel] = sorted(edges)
    return graph


def build_repo_map(root: Path, tutor_dir: Path, now: datetime) -> RepoMap:
    """Build the map, reusing unchanged files from the previous one, and save it."""
    map_path = tutor_dir / FILE_NAME
    previous = load_json(map_path, RepoMap)
    old_files = previous.files if previous.version == MAP_VERSION else {}

    files: dict[str, FileRecord] = {}
    with IsolatedParser() as isolated:
        for rel in list_repo_files(root):
            try:
                files[rel] = _record(root, rel, old_files.get(rel), isolated)
            except OSError:
                continue

    history, recent = read_history(root)
    for rel, record in files.items():
        record.git = history.get(rel, GitInfo())

    graph = _resolve(root, files)
    repo_map = RepoMap(
        root=str(root),
        built_at=now,
        files=files,
        import_graph=graph,
        entry_points=find_entry_points(root, list(files), graph),
        recent_commits=recent,
    )
    save_json(map_path, repo_map)
    return repo_map
