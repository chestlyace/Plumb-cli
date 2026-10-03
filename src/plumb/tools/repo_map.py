"""repo_map: an overview of the repo, or one file's entry."""

from collections import Counter

from plumb.repomap.model import RepoMap
from plumb.tools.scope import Scope, cut

OVERVIEW_ENTRY_POINTS = 30
OVERVIEW_COMMITS = 10
MAX_LIST_ITEMS = 100


def _capped(items: list[str], what: str) -> list[str]:
    if len(items) <= MAX_LIST_ITEMS:
        return items
    return [*items[:MAX_LIST_ITEMS], f"  … {len(items) - MAX_LIST_ITEMS} more {what}"]


def _overview(repo_map: RepoMap) -> str:
    folders = Counter(
        rel.split("/", 1)[0] + "/" if "/" in rel else "." for rel in repo_map.files
    )
    languages = Counter(
        record.language for record in repo_map.files.values() if record.language
    )
    lines = [f"Repo: {len(repo_map.files)} files", "", "Entry points:"]
    entries = repo_map.entry_points[:OVERVIEW_ENTRY_POINTS]
    lines += [f"  {e.path}  ({e.reason})" for e in entries] or ["  none found"]
    if len(repo_map.entry_points) > len(entries):
        lines.append(f"  … {len(repo_map.entry_points) - len(entries)} more")
    lines += ["", "Top-level folders (files):"]
    lines += [f"  {name} ({count})" for name, count in sorted(folders.items())]
    lines += ["", "Languages (files):"]
    lines += [f"  {name} ({count})" for name, count in languages.most_common()]
    lines += ["", "Recent commits:"]
    lines += [
        f"  {c.date.date()}  {cut(c.subject)}"
        for c in repo_map.recent_commits[:OVERVIEW_COMMITS]
    ] or ["  none"]
    return "\n".join(lines)


def _file(repo_map: RepoMap, rel: str) -> str:
    record = repo_map.files[rel]
    imported_by = sorted(
        source for source, targets in repo_map.import_graph.items() if rel in targets
    )
    lines = [
        f"{rel}",
        f"Language: {record.language or 'unknown'} ({record.parsed} parse)",
    ]
    lines += ["", "Symbols:"]
    symbols = [
        f"  {s.kind} {s.parent + '.' if s.parent else ''}{s.name}  lines {s.start_line}-{s.end_line}"
        for s in record.symbols
    ]
    lines += _capped(symbols, "symbols") or ["  none"]
    lines += ["", "Imports:"]
    imports = [
        f"  line {i.line}: {i.module}{' ' + ', '.join(i.names) if i.names else ''}"
        f" -> {i.resolved or 'external/unresolved'}"
        for i in record.imports
    ]
    lines += _capped(imports, "imports") or ["  none"]
    lines += ["", "Imported by:"]
    lines += _capped([f"  {p}" for p in imported_by], "files") or ["  none"]
    lines += ["", "Git:"]
    if record.git.commits:
        lines.append(
            f"  {record.git.commits} commits, last on {record.git.last_commit.date() if record.git.last_commit else '?'}"
        )
        lines += [f"  - {cut(s)}" for s in record.git.recent_subjects]
    else:
        lines.append("  no commits")
    lines += ["", "Summary:"]
    if record.summary and record.summary.sha256 == record.sha256:
        lines.append(f"  {record.summary.text}")
    else:
        lines.append("  not written yet")
    return "\n".join(lines)


def repo_map_tool(scope: Scope, repo_map: RepoMap, path: str | None = None) -> str:
    if not path:
        return _overview(repo_map)
    rel, _ = scope.file(path)
    return _file(repo_map, rel)
