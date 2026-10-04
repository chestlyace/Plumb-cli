"""tour.json: where each tour got to, so it can resume."""

from pathlib import Path

from pydantic import AwareDatetime, BaseModel, Field

from plumb.memory.io import load_json, save_json

FILE_NAME = "tour.json"


class TourProgress(BaseModel):
    visited: list[str] = Field(default_factory=list)
    queue: list[str] = Field(default_factory=list)
    updated_at: AwareDatetime


class Tours(BaseModel):
    # Keyed by the entry point the tour started from.
    tours: dict[str, TourProgress] = Field(default_factory=dict)


def load_tours(tutor_dir: Path) -> Tours:
    return load_json(tutor_dir / FILE_NAME, Tours)


def save_tours(tutor_dir: Path, tours: Tours) -> None:
    save_json(tutor_dir / FILE_NAME, tours)
