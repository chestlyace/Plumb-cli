"""git_log, read with the git command line."""

import subprocess

from plumb.tools.scope import Scope, cut

MAX_COMMITS = 50


def git_log(scope: Scope, path: str | None = None, limit: int = 10) -> str:
    rel = scope.file_or_folder(path) if path else ""
    count = max(1, min(limit, MAX_COMMITS))
    command = ["git", "log", f"-n{count}", "--format=%cs  %h  %s"]
    if rel:
        command += ["--", rel]
    try:
        result = subprocess.run(
            command, cwd=scope.root, capture_output=True, check=True, text=True
        )
    except OSError, subprocess.CalledProcessError:
        return "Error: no git history is available for this repo."
    lines = [cut(line) for line in result.stdout.splitlines() if line]
    if not lines:
        return f"No commits touch {rel or 'the repo'}."
    header = f"Last {len(lines)} commits for {rel or 'the repo'}"
    if limit > MAX_COMMITS:
        header += f" (limit capped at {MAX_COMMITS})"
    return "\n".join([header, *lines])
