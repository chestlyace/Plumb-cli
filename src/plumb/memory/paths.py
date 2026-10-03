"""Find the .tutor/ folder: the git root of the cwd, or the cwd itself."""

from pathlib import Path

TUTOR_DIR_NAME = ".tutor"


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from `start` to the folder holding .git; fall back to `start`."""
    start = (start or Path.cwd()).resolve()
    for folder in (start, *start.parents):
        if (folder / ".git").exists():
            return folder
    return start


def find_tutor_dir(start: Path | None = None) -> Path:
    """Return the .tutor/ path for `start`. It is not created here."""
    return find_project_root(start) / TUTOR_DIR_NAME
