"""Shared read and write helpers for the .tutor/ files."""

import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ValidationError


class CorruptMemoryFile(Exception):
    """A .tutor/ file exists but cannot be read. It is left untouched."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path


def load_json[M: BaseModel](path: Path, model: type[M]) -> M:
    """Load a JSON file into `model`. A missing file loads as an empty model."""
    if not path.exists():
        return model()
    try:
        return model.model_validate_json(path.read_bytes())
    except ValidationError as error:
        raise CorruptMemoryFile(path, str(error)) from error


def save_json(path: Path, data: BaseModel) -> None:
    """Write a JSON file atomically: write a temp file, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data.model_dump_json(indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def append_jsonl(path: Path, data: BaseModel) -> None:
    """Append one JSON line and flush it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(data.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    """Read every line of a JSONL file. A missing file reads as empty."""
    if not path.exists():
        return []
    items: list[M] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            items.append(model.model_validate_json(line))
        except ValidationError as error:
            raise CorruptMemoryFile(path, f"line {number}: {error}") from error
    return items
