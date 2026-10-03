"""List the repo's files: git's view of the repo, then a fixed ignore list."""

import os
import subprocess
from pathlib import Path

IGNORED_DIRS = frozenset(
    {".git", ".tutor", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
)
BINARY_SNIFF_BYTES = 8192


def _ignored(rel_path: str) -> bool:
    folders = rel_path.split("/")[:-1]
    return any(part in IGNORED_DIRS or part.startswith(".") for part in folders)


def _git_files(root: Path) -> list[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        )
    except OSError, subprocess.CalledProcessError:
        return None
    return [p for p in result.stdout.decode("utf-8", "replace").split("\0") if p]


def _walk_files(root: Path) -> list[str]:
    found: list[str] = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
        for name in names:
            found.append((Path(folder) / name).relative_to(root).as_posix())
    return found


def is_binary(path: Path) -> bool:
    with path.open("rb") as handle:
        return b"\0" in handle.read(BINARY_SNIFF_BYTES)


def list_repo_files(root: Path) -> list[str]:
    """Repo-relative POSIX paths of readable text files, sorted."""
    candidates = _git_files(root)
    if candidates is None:
        candidates = _walk_files(root)
    files: list[str] = []
    for rel in candidates:
        if _ignored(rel):
            continue
        path = root / rel
        # Symlinks are skipped so the map never reaches outside the repo.
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if is_binary(path):
                continue
        except OSError:
            continue
        files.append(rel)
    return sorted(set(files))
