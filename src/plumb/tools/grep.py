"""grep: Python regex over the files the repo map lists."""

import re

from plumb.tools.scope import OutOfScope, Scope, cut

MAX_MATCHES = 100


def grep(scope: Scope, pattern: str, path: str = ".", ignore_case: bool = False) -> str:
    if not pattern:
        return "Error: pattern is empty."
    rel = scope.file_or_folder(path)
    flags = re.IGNORECASE if ignore_case else 0
    note = ""
    try:
        regex = re.compile(pattern, flags)
    except re.error:
        # Small models often send unescaped text; search it literally instead.
        regex = re.compile(re.escape(pattern), flags)
        note = " (not a valid regex; searched as plain text)"
    matches: list[str] = []
    more = False
    for item in scope.files_under(rel):
        try:
            _, real = scope.file(item)
            text = real.read_text(encoding="utf-8", errors="replace")
        except OSError, OutOfScope:
            continue  # A file that vanished or became a symlink is skipped.
        for number, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                if len(matches) == MAX_MATCHES:
                    more = True
                    break
                matches.append(f"{item}:{number}: {cut(line.strip())}")
        if more:
            break
    if not matches:
        return f"No matches for {pattern!r} in {rel or '.'}{note}."
    header = f"{len(matches)} matches for {pattern!r} in {rel or '.'}{note}"
    if more:
        matches.append(
            f"… stopped at {MAX_MATCHES} matches; narrow the pattern or path."
        )
    return "\n".join([header, *matches])
