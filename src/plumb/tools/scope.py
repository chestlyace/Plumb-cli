"""Path checks: tools only see files the repo map lists, inside the repo root."""

import posixpath
from pathlib import Path

from plumb.repomap.model import RepoMap

MAX_LINE_CHARS = 300


class OutOfScope(Exception):
    """A path the tools may not touch. The message is shown to the model."""


def cut(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + " …"


class Scope:
    def __init__(self, root: Path, repo_map: RepoMap) -> None:
        self.root = root.resolve()
        self.files = frozenset(repo_map.files)
        self.dirs = frozenset(
            parent for rel in self.files for parent in _parents(rel)
        ) | {""}

    def normalize(self, path: str | None) -> str:
        """Repo-relative POSIX path; "" is the root. Raises OutOfScope."""
        raw = (path or ".").strip()
        if Path(raw).is_absolute():
            try:
                raw = Path(raw).resolve().relative_to(self.root).as_posix()
            except ValueError:
                raise OutOfScope("path is outside the repo") from None
        rel = posixpath.normpath(raw.replace("\\", "/"))
        if rel == ".":
            return ""
        if rel == ".." or rel.startswith("../"):
            raise OutOfScope("path is outside the repo")
        return rel

    def _real_path_inside(self, rel: str) -> Path:
        path = self.root / rel
        if path.is_symlink():
            raise OutOfScope(f"{rel} is a symlink, which the tutor does not follow")
        try:
            path.resolve().relative_to(self.root)
        except ValueError:
            raise OutOfScope("path is outside the repo") from None
        return path

    def file(self, path: str | None) -> tuple[str, Path]:
        rel = self.normalize(path)
        if rel not in self.files:
            if rel in self.dirs:
                raise OutOfScope(f"{rel} is a folder; use list_dir")
            raise OutOfScope(
                f"{rel or '.'} is not a file the tutor can read "
                "(missing, ignored, binary or a symlink)"
            )
        return rel, self._real_path_inside(rel)

    def folder(self, path: str | None) -> str:
        rel = self.normalize(path)
        if rel not in self.dirs:
            if rel in self.files:
                raise OutOfScope(f"{rel} is a file; use read_file")
            raise OutOfScope(f"{rel or '.'} is not a folder the tutor can see")
        self._real_path_inside(rel)
        return rel

    def file_or_folder(self, path: str | None) -> str:
        rel = self.normalize(path)
        if rel in self.files:
            return self.file(rel)[0]
        return self.folder(rel)

    def files_under(self, rel: str) -> list[str]:
        if rel in self.files:
            return [rel]
        prefix = f"{rel}/" if rel else ""
        return sorted(f for f in self.files if f.startswith(prefix))


def _parents(rel: str) -> list[str]:
    parts = rel.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]
