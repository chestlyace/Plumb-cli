"""Check what the tutor wrote: [file:lines] citations and (label) tags.

Citations must name a file the repo map lists and lines inside it, and
span at most MAX_CITATION_LINES lines: a wider range teaches nothing. A
"documented" label must point at a commit or doc the tutor actually saw in
this turn's tool results; otherwise it is rewritten to "inferred".
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from plumb.tools.scope import OutOfScope, Scope

CITATION = re.compile(r"\[([^\[\]\s:]+):(\d+)(?:\s*-\s*(\d+))?\]")
LABEL = re.compile(r"\((inferred|documented:\s*([^)]+?))\s*\)", re.IGNORECASE)
COMMIT = re.compile(r"^(?:commit\s+)?([0-9a-f]{7,40})$", re.IGNORECASE)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
REASON_WORDS = re.compile(
    r"\b(because|so that|in order to|to avoid|which means|likely|probably|presumably)\b",
    re.IGNORECASE,
)

Label = Literal["documented", "inferred"]

MAX_CITATION_LINES = 60


@dataclass(frozen=True)
class Citation:
    file: str
    start: int
    end: int
    ok: bool
    problem: str | None = None

    def __str__(self) -> str:
        lines = (
            f"{self.start}-{self.end}" if self.end != self.start else str(self.start)
        )
        return f"{self.file}:{lines}"


@dataclass(frozen=True)
class Reason:
    sentence: str
    label: Label
    source: str | None  # The commit or doc a documented reason came from.
    citation: Citation | None


@dataclass
class Report:
    text: str  # The explanation with labels corrected.
    citations: list[Citation] = field(default_factory=list)
    reasons: list[Reason] = field(default_factory=list)
    downgraded: list[str] = field(default_factory=list)  # Sources that were not seen.
    untagged: list[str] = field(default_factory=list)  # Reasons shown as inferred.


def parse_citation(scope: Scope, raw: str) -> Citation | None:
    match = CITATION.fullmatch(f"[{raw.strip().strip('[]')}]")
    return check_citation(scope, match) if match else None


def check_citation(scope: Scope, match: re.Match[str]) -> Citation:
    path, start_text, end_text = match.groups()
    start = int(start_text)
    end = int(end_text) if end_text else start
    try:
        rel, real = scope.file(path)
        total = len(real.read_text(encoding="utf-8", errors="replace").splitlines())
    except (OutOfScope, OSError) as error:
        return Citation(path, start, end, ok=False, problem=str(error))
    if start < 1 or end < start or end > total:
        return Citation(rel, start, end, ok=False, problem=f"{rel} has {total} lines")
    if end - start + 1 > MAX_CITATION_LINES:
        return Citation(
            rel,
            start,
            end,
            ok=False,
            problem=f"too broad: {end - start + 1} lines (at most {MAX_CITATION_LINES})",
        )
    return Citation(rel, start, end, ok=True)


def _source_seen(source: str, seen: str, files_read: list[str]) -> bool:
    commit = COMMIT.match(source.strip())
    if commit:
        return commit.group(1).lower()[:7] in seen.lower()
    path = source.strip().split(":")[0]
    return path in files_read


def check(text: str, scope: Scope, seen: str, files_read: list[str]) -> Report:
    """`seen` is every tool result of the turn, `files_read` the files opened."""
    report = Report(text=text)
    citations = {m.group(0): check_citation(scope, m) for m in CITATION.finditer(text)}
    report.citations = list(citations.values())

    def relabel(match: re.Match[str]) -> str:
        source = match.group(2)
        if source and not _source_seen(source, seen, files_read):
            report.downgraded.append(source.strip())
            return "(inferred)"
        return match.group(0)

    report.text = LABEL.sub(relabel, text)
    for sentence in SENTENCE_END.split(report.text):
        sentence = sentence.strip()
        if not sentence:
            continue
        cited = next(
            (
                citations[m.group(0)]
                for m in CITATION.finditer(sentence)
                if m.group(0) in citations
            ),
            None,
        )
        tag = LABEL.search(sentence)
        if tag is None:
            if REASON_WORDS.search(sentence):
                report.untagged.append(sentence)
                report.reasons.append(Reason(sentence, "inferred", None, cited))
            continue
        source = tag.group(2)
        label: Label = "documented" if source else "inferred"
        report.reasons.append(
            Reason(sentence, label, source.strip() if source else None, cited)
        )
    return report
