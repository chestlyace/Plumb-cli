"""mastery.json: per concept, solid or shaky, and when it was last seen."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints

from plumb.memory.io import load_json, save_json

FILE_NAME = "mastery.json"

ConceptSlug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
Level = Literal["solid", "shaky"]


class Concept(BaseModel):
    name: str
    level: Level
    last_seen: AwareDatetime
    times_asked: int = Field(default=0, ge=0)
    times_correct: int = Field(default=0, ge=0)
    files: list[str] = Field(default_factory=list)


class Mastery(BaseModel):
    concepts: dict[ConceptSlug, Concept] = Field(default_factory=dict)


def load_mastery(tutor_dir: Path) -> Mastery:
    return load_json(tutor_dir / FILE_NAME, Mastery)


def save_mastery(tutor_dir: Path, mastery: Mastery) -> None:
    save_json(tutor_dir / FILE_NAME, mastery)
