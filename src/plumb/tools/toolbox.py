"""The read-only tools, as the model sees them. Every tool returns text and
never raises: a refusal or failure comes back as "Error: <reason>"."""

from collections.abc import Callable
from functools import wraps
from pathlib import Path

from plumb.repomap.model import RepoMap
from plumb.tools.files import list_dir, read_file
from plumb.tools.git import git_log
from plumb.tools.grep import grep
from plumb.tools.memory import memory_tool
from plumb.tools.repo_map import repo_map_tool
from plumb.tools.scope import OutOfScope, Scope


def _never_raises[**P](tool: Callable[P, str]) -> Callable[P, str]:
    @wraps(tool)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> str:
        try:
            return tool(*args, **kwargs)
        except OutOfScope as refusal:
            return f"Error: {refusal}"
        except Exception as error:  # noqa: BLE001 - the model gets text, never a crash
            return f"Error: {type(error).__name__}: {error}"

    return wrapper


class Toolbox:
    def __init__(self, root: Path, tutor_dir: Path, repo_map: RepoMap) -> None:
        self.scope = Scope(root, repo_map)
        self.tutor_dir = tutor_dir
        self._repo_map = repo_map

    def refresh(self, repo_map: RepoMap) -> None:
        """Use a rebuilt repo map, e.g. after she changed files."""
        self.scope = Scope(self.scope.root, repo_map)
        self._repo_map = repo_map

    @property
    def files(self) -> frozenset[str]:
        return self.scope.files

    @_never_raises
    def read_file(
        self, path: str, start_line: int = 1, end_line: int | None = None
    ) -> str:
        """Read a file's lines, numbered. Up to 400 lines per call."""
        return read_file(self.scope, path, start_line, end_line)

    @_never_raises
    def grep(self, pattern: str, path: str = ".", ignore_case: bool = False) -> str:
        """Search file contents with a regex (or plain text). Up to 100 matches."""
        return grep(self.scope, pattern, path, ignore_case)

    @_never_raises
    def list_dir(self, path: str = ".") -> str:
        """List a folder's files and subfolders (folders end with /)."""
        return list_dir(self.scope, path)

    @_never_raises
    def git_log(self, path: str | None = None, limit: int = 10) -> str:
        """Recent commits for the repo, a folder or a file. Up to 50."""
        return git_log(self.scope, path, limit)

    @_never_raises
    def repo_map(self, path: str | None = None) -> str:
        """Without path: repo overview. With a file path: its symbols, imports,
        importers, git history and summary."""
        return repo_map_tool(self.scope, self._repo_map, path)

    @_never_raises
    def memory(self, kind: str, query: str | None = None) -> str:
        """Look up what the tutor remembers. kind: mastery, skipped, decisions
        or sessions. query filters by text."""
        return memory_tool(self.tutor_dir, kind, query)
