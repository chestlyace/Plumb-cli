"""Read what changed: uncommitted work, or everything since a git ref.

Only files the repo map lists are included, so .tutor/, ignored files and
binaries never reach the model.
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_DIFF_LINES = 400
MAX_NEW_FILE_LINES = 200


@dataclass
class FileDiff:
    path: str
    text: str
    added: int
    removed: int
    new: bool = False


@dataclass
class Diff:
    source: str  # What was compared, in words, e.g. "uncommitted changes".
    files: list[FileDiff] = field(default_factory=list)
    shown: list[FileDiff] = field(default_factory=list)
    listed: list[FileDiff] = field(default_factory=list)  # Too big to include.

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]

    def render(self) -> str:
        parts = [f.text for f in self.shown]
        if self.listed:
            parts.append(
                "Also changed (not shown; read them with the tools):\n"
                + "\n".join(f"  {f.path}  +{f.added} -{f.removed}" for f in self.listed)
            )
        return "\n".join(parts)


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _number(chunk: str) -> str:
    """Prefix kept and added lines with their line number in the current file,
    so the model can cite them without counting from the hunk header."""
    out: list[str] = []
    line_no: int | None = None
    for line in chunk.splitlines():
        hunk = HUNK.match(line)
        if hunk:
            line_no = int(hunk.group(1))
            out.append(line)
        elif line_no is None or line.startswith("\\"):
            out.append(line)
        elif line.startswith("-"):
            out.append(f"{'':>5}  {line}")
        else:
            out.append(f"{line_no:>5}  {line}")
            line_no += 1
    return "\n".join(out)


class DiffError(Exception):
    """The diff could not be read; the message says why, for her to see."""


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        )
    except FileNotFoundError:
        raise DiffError("git is not installed") from None
    except subprocess.CalledProcessError as error:
        raise DiffError(
            error.stderr.strip() or f"git {' '.join(args)} failed"
        ) from None
    return result.stdout


def _split(diff_text: str, allowed: set[str]) -> list[FileDiff]:
    files: list[FileDiff] = []
    for chunk in diff_text.split("diff --git ")[1:]:
        header = chunk.splitlines()[0]
        old_path = header.split(" b/", 1)[0].removeprefix("a/")
        path = header.rsplit(" b/", 1)[-1]
        lines = chunk.splitlines()
        added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
        if "\n+++ /dev/null" in chunk:
            # Deleted: no longer in the map, so only its name is passed on.
            files.append(
                FileDiff(old_path, f"deleted file: {old_path} (-{removed})", 0, removed)
            )
            continue
        if path not in allowed or (not added and not removed):
            continue  # Ignored, binary, or a pure rename.
        text = _number("diff --git " + chunk.rstrip("\n"))
        files.append(FileDiff(path, text, added, removed))
    return files


def _new_file(root: Path, path: str) -> FileDiff:
    lines = (root / path).read_text(encoding="utf-8", errors="replace").splitlines()
    body = [f"{n:>5}  +{l}" for n, l in enumerate(lines[:MAX_NEW_FILE_LINES], 1)]
    if len(lines) > MAX_NEW_FILE_LINES:
        body.append(f"+… ({len(lines) - MAX_NEW_FILE_LINES} more lines)")
    text = (
        f"diff --git a/{path} b/{path}\nnew file\n--- /dev/null\n+++ b/{path}\n"
        + "\n".join(body)
    )
    return FileDiff(path, text, len(lines), 0, new=True)


def read_diff(root: Path, allowed: set[str], ref: str | None = None) -> Diff:
    """Uncommitted changes (with new files), or changes since `ref`. With no
    uncommitted changes and no ref, the last commit."""
    if ref is not None:
        if ref.startswith("-"):
            raise DiffError(f"{ref!r} is not a git ref")
        _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        files = _split(_git(root, "diff", "--no-color", "-M", ref, "--"), allowed)
        return Diff(f"changes since {ref}", files)

    _git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    files = _split(_git(root, "diff", "--no-color", "-M", "HEAD", "--"), allowed)
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z").split(
        "\0"
    )
    files += [_new_file(root, p) for p in untracked if p in allowed]
    if files:
        return Diff("uncommitted changes", files)
    try:
        last = _split(
            _git(root, "diff", "--no-color", "-M", "HEAD~1", "HEAD", "--"), allowed
        )
    except DiffError:
        return Diff("uncommitted changes", [])
    subject = _git(root, "log", "-1", "--format=%h %s").strip()
    return Diff(f"the last commit ({subject}), since nothing is uncommitted", last)


def fit(diff: Diff, first: set[str], limit: int = MAX_DIFF_LINES) -> Diff:
    """Choose which whole file diffs to show, files in `first` first, within
    `limit` lines; the rest are listed by name."""
    ordered = sorted(diff.files, key=lambda f: (f.path not in first, f.path))
    used = 0
    diff.shown, diff.listed = [], []
    for file in ordered:
        size = len(file.text.splitlines())
        if used + size <= limit:
            diff.shown.append(file)
            used += size
        else:
            diff.listed.append(file)
    return diff
