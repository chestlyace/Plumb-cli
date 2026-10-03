"""decisions.json: each decision, its reason, its label and the source file."""

from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, Field

from plumb.memory.commands import Command
from plumb.memory.io import load_json, save_json

FILE_NAME = "decisions.json"

Label = Literal["documented", "inferred", "confirmed"]


class Decision(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    decision: str
    reason: str
    label: Label
    source_file: str
    lines: tuple[int, int] | None = None
    command: Command
    recorded_at: AwareDatetime


class Decisions(BaseModel):
    decisions: list[Decision] = Field(default_factory=list)


def load_decisions(tutor_dir: Path) -> Decisions:
    return load_json(tutor_dir / FILE_NAME, Decisions)


def save_decisions(tutor_dir: Path, decisions: Decisions) -> None:
    save_json(tutor_dir / FILE_NAME, decisions)
