"""memory: look up mastery, skipped questions, decisions and past sessions."""

import json
from pathlib import Path

from plumb.memory.decisions import load_decisions
from plumb.memory.io import CorruptMemoryFile
from plumb.memory.mastery import load_mastery
from plumb.memory.sessions import DIR_NAME, read_session
from plumb.memory.skipped import load_skipped
from plumb.tools.scope import cut

MAX_ENTRIES = 50
KINDS = ("mastery", "skipped", "decisions", "sessions")


def _matches(query: str | None, *fields: str | None) -> bool:
    if not query:
        return True
    needle = query.lower()
    return any(needle in (f or "").lower() for f in fields)


def _mastery(tutor_dir: Path, query: str | None) -> list[str]:
    concepts = load_mastery(tutor_dir).concepts
    return [
        f"{slug}: {c.name} - {c.level}, last seen {c.last_seen.date()}, "
        f"{c.times_correct}/{c.times_asked} correct, files: {', '.join(c.files) or 'none'}"
        for slug, c in sorted(concepts.items())
        if _matches(query, slug, c.name, *c.files)
    ]


def _skipped(tutor_dir: Path, query: str | None) -> list[str]:
    return [
        f"{s.skipped_at.date()} [{s.command}] {s.topic}: {s.question}"
        for s in load_skipped(tutor_dir).skipped
        if _matches(query, s.topic, s.question)
    ]


def _decisions(tutor_dir: Path, query: str | None) -> list[str]:
    found = []
    for d in load_decisions(tutor_dir).decisions:
        if not _matches(query, d.decision, d.reason, d.source_file):
            continue
        where = d.source_file + (f":{d.lines[0]}-{d.lines[1]}" if d.lines else "")
        found.append(
            f"{d.recorded_at.date()} [{d.command}] {d.decision} - {d.reason} "
            f"({d.label}; {where})"
        )
    return found


def _sessions(tutor_dir: Path, query: str | None) -> list[str]:
    found = []
    folder = tutor_dir / DIR_NAME
    paths = sorted(folder.glob("*.jsonl"), reverse=True) if folder.is_dir() else []
    for path in paths:
        for event in read_session(path):
            payload = json.dumps(event.payload, ensure_ascii=False)
            if _matches(query, event.type, payload):
                found.append(
                    cut(f"{path.stem} {event.ts:%H:%M} {event.type}: {payload}")
                )
    return found


def memory_tool(tutor_dir: Path, kind: str, query: str | None = None) -> str:
    readers = {
        "mastery": _mastery,
        "skipped": _skipped,
        "decisions": _decisions,
        "sessions": _sessions,
    }
    if kind not in readers:
        return f"Error: kind must be one of {', '.join(KINDS)}."
    try:
        entries = readers[kind](tutor_dir, query)
    except CorruptMemoryFile as error:
        return f"Error: a memory file is unreadable: {error.path.name}"
    label = f"{kind}" + (f" matching {query!r}" if query else "")
    if not entries:
        return f"No {label}."
    shown = entries[:MAX_ENTRIES]
    lines = [f"{len(entries)} {label}", *shown]
    if len(entries) > len(shown):
        lines.append(f"… {len(entries) - len(shown)} more; add or narrow the query.")
    return "\n".join(lines)
