"""skipped.json: questions she skipped, with topic and date."""

from pathlib import Path
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, Field

from plumb.memory.commands import Command
from plumb.memory.io import load_json, save_json

FILE_NAME = "skipped.json"


class SkippedQuestion(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    topic: str
    question: str
    command: Command
    skipped_at: AwareDatetime


class Skipped(BaseModel):
    skipped: list[SkippedQuestion] = Field(default_factory=list)


def load_skipped(tutor_dir: Path) -> Skipped:
    return load_json(tutor_dir / FILE_NAME, Skipped)


def save_skipped(tutor_dir: Path, skipped: Skipped) -> None:
    save_json(tutor_dir / FILE_NAME, skipped)
