"""read_file and list_dir."""

from plumb.tools.scope import Scope, cut

MAX_READ_LINES = 400
MAX_DIR_ENTRIES = 500


def read_file(
    scope: Scope, path: str, start_line: int = 1, end_line: int | None = None
) -> str:
    rel, real = scope.file(path)
    lines = real.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    start = max(1, start_line)
    if total == 0:
        return f"{rel} is empty."
    if start > total:
        return f"Error: {rel} has {total} lines; start_line {start} is past the end."
    end = min(total, end_line if end_line is not None else total)
    if end < start:
        return f"Error: end_line {end} is before start_line {start}."
    shown_end = min(end, start + MAX_READ_LINES - 1)
    width = len(str(shown_end))
    body = [f"{n:>{width}}| {cut(lines[n - 1])}" for n in range(start, shown_end + 1)]
    header = f"{rel} (lines {start}-{shown_end} of {total})"
    if shown_end < end:
        body.append(
            f"… truncated at {MAX_READ_LINES} lines; "
            f"call read_file with start_line={shown_end + 1} for more."
        )
    return "\n".join([header, *body])


def list_dir(scope: Scope, path: str = ".") -> str:
    rel = scope.folder(path)
    prefix = f"{rel}/" if rel else ""
    entries: set[str] = set()
    for item in scope.files_under(rel):
        rest = item[len(prefix) :]
        head, sep, _ = rest.partition("/")
        entries.add(head + "/" if sep else head)
    ordered = sorted(entries, key=lambda e: (not e.endswith("/"), e))
    shown = ordered[:MAX_DIR_ENTRIES]
    lines = [f"{rel or '.'}/ ({len(ordered)} entries)", *shown]
    if len(ordered) > len(shown):
        lines.append(f"… {len(ordered) - len(shown)} more entries not shown.")
    return "\n".join(lines)
