"""repo-map.json schema."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

FILE_NAME = "repo-map.json"
MAP_VERSION = 4

ParseLevel = Literal["full", "symbols", "fallback"]


class Symbol(BaseModel):
    kind: str
    name: str
    parent: str | None = None
    start_line: int
    end_line: int


class Import(BaseModel):
    module: str
    line: int
    # Python `from x import a, b`: the imported names, which may be modules.
    names: list[str] = Field(default_factory=list)
    resolved: str | None = None


class GitInfo(BaseModel):
    commits: int = 0
    last_commit: AwareDatetime | None = None
    recent_subjects: list[str] = Field(default_factory=list)


class Summary(BaseModel):
    text: str
    sha256: str
    written_at: AwareDatetime


class FileRecord(BaseModel):
    size: int
    mtime_ns: int
    sha256: str
    language: str | None = None
    parsed: ParseLevel = "fallback"
    symbols: list[Symbol] = Field(default_factory=list)
    imports: list[Import] = Field(default_factory=list)
    external_imports: list[str] = Field(default_factory=list)
    git: GitInfo = Field(default_factory=GitInfo)
    summary: Summary | None = None


class EntryPoint(BaseModel):
    path: str
    reason: str


class Commit(BaseModel):
    date: AwareDatetime
    subject: str


class RepoMap(BaseModel):
    version: int = MAP_VERSION
    root: str = ""
    built_at: AwareDatetime | None = None
    files: dict[str, FileRecord] = Field(default_factory=dict)
    import_graph: dict[str, list[str]] = Field(default_factory=dict)
    entry_points: list[EntryPoint] = Field(default_factory=list)
    recent_commits: list[Commit] = Field(default_factory=list)
