"""The Toolbox, wrapped as PydanticAI tools with the loop guard in front."""

from typing import Any

from pydantic_ai.toolsets import FunctionToolset

from plumb.engine.loop_guard import LoopGuard
from plumb.tools.scope import OutOfScope
from plumb.tools.toolbox import Toolbox


def build_toolset(
    toolbox: Toolbox, guard: LoopGuard, files_read: set[str]
) -> FunctionToolset:
    def note_file(path: str | None, result: str) -> None:
        if path and not result.startswith("Error:"):
            try:
                files_read.add(toolbox.scope.normalize(path))
            except OutOfScope:
                pass

    def guarded(name: str, args: dict[str, Any], call: Any) -> str:
        return guard.run(name, args, call)

    def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
        """Read a file from the repo, with line numbers. Up to 400 lines per call.

        Args:
            path: File path relative to the repo root, e.g. "src/app/db.py".
            start_line: First line to read (1-based).
            end_line: Last line to read; leave empty to read to the end.
        """
        args = {"path": path, "start_line": start_line, "end_line": end_line}
        result = guarded(
            "read_file", args, lambda: toolbox.read_file(path, start_line, end_line)
        )
        note_file(path, result)
        return result

    def grep(pattern: str, path: str = ".", ignore_case: bool = False) -> str:
        """Search the repo's files for a regex or plain text. Up to 100 matches.

        Args:
            pattern: Text or regular expression to find.
            path: File or folder to search in; "." is the whole repo.
            ignore_case: Match upper and lower case alike.
        """
        args = {"pattern": pattern, "path": path, "ignore_case": ignore_case}
        return guarded("grep", args, lambda: toolbox.grep(pattern, path, ignore_case))

    def list_dir(path: str = ".") -> str:
        """List a folder's files and subfolders. Folders end with "/".

        Args:
            path: Folder relative to the repo root; "." is the root.
        """
        return guarded("list_dir", {"path": path}, lambda: toolbox.list_dir(path))

    def git_log(path: str | None = None, limit: int = 10) -> str:
        """Recent git commits for the repo, a folder or a file.

        Args:
            path: File or folder; leave empty for the whole repo.
            limit: How many commits, up to 50.
        """
        args = {"path": path, "limit": limit}
        return guarded("git_log", args, lambda: toolbox.git_log(path, limit))

    def repo_map(path: str | None = None) -> str:
        """The repo map. Without a path: an overview (entry points, folders,
        languages, recent commits). With a file path: that file's functions and
        classes, imports, the files that import it, and its git history.

        Args:
            path: A file path, or empty for the overview.
        """
        result = guarded("repo_map", {"path": path}, lambda: toolbox.repo_map(path))
        note_file(path, result)
        return result

    def memory(kind: str, query: str | None = None) -> str:
        """What the tutor remembers about this learner and repo.

        Args:
            kind: One of "mastery", "skipped", "decisions" or "sessions".
            query: Optional text to filter by, e.g. a concept or a file name.
        """
        args = {"kind": kind, "query": query}
        return guarded("memory", args, lambda: toolbox.memory(kind, query))

    return FunctionToolset(
        [read_file, grep, list_dir, git_log, repo_map, memory], max_retries=1
    )
