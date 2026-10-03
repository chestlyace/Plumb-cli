"""sessions/*.jsonl: one append-only transcript per session."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field

from plumb.memory.commands import Command
from plumb.memory.io import append_jsonl, read_jsonl

DIR_NAME = "sessions"


class SessionEvent(BaseModel):
    ts: AwareDatetime
    # Event types are finalized in phase 6.
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


def new_session_path(tutor_dir: Path, command: Command, started_at: datetime) -> Path:
    """sessions/<UTC timestamp>-<command>.jsonl, e.g. 20261003T140000Z-plan.jsonl."""
    stamp = started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return tutor_dir / DIR_NAME / f"{stamp}-{command}.jsonl"


def append_event(session_path: Path, event: SessionEvent) -> None:
    append_jsonl(session_path, event)


def read_session(session_path: Path) -> list[SessionEvent]:
    return read_jsonl(session_path, SessionEvent)
