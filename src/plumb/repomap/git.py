"""Git history highlights, read with the git command line."""

import subprocess
from datetime import datetime
from pathlib import Path

from plumb.repomap.model import Commit, GitInfo

RECENT_SUBJECTS_PER_FILE = 3
RECENT_COMMITS = 20
RECORD_SEPARATOR = "\x1e"
FIELD_SEPARATOR = "\x1f"


def read_history(root: Path) -> tuple[dict[str, GitInfo], list[Commit]]:
    """Per-file highlights and the repo's recent commits. Empty when there is no history."""
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.quotePath=false",
                "log",
                f"--format={RECORD_SEPARATOR}%cI{FIELD_SEPARATOR}%s",
                "--name-only",
            ],
            cwd=root,
            capture_output=True,
            check=True,
        )
    except OSError, subprocess.CalledProcessError:
        return {}, []

    per_file: dict[str, GitInfo] = {}
    recent: list[Commit] = []
    for record in result.stdout.decode("utf-8", "replace").split(RECORD_SEPARATOR):
        lines = [line for line in record.splitlines() if line]
        if not lines or FIELD_SEPARATOR not in lines[0]:
            continue
        stamp, subject = lines[0].split(FIELD_SEPARATOR, 1)
        date = datetime.fromisoformat(stamp)
        if len(recent) < RECENT_COMMITS:
            recent.append(Commit(date=date, subject=subject))
        for path in lines[1:]:
            info = per_file.setdefault(path, GitInfo())
            info.commits += 1
            if info.last_commit is None:
                info.last_commit = date
            if len(info.recent_subjects) < RECENT_SUBJECTS_PER_FILE:
                info.recent_subjects.append(subject)
    return per_file, recent
