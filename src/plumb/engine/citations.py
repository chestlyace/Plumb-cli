"""Check what the tutor wrote: file and line citations, and (label) tags.

Citations are found in every form a model writes them: [f:15], [f:15-20],
[f:15–20], [f:15 to 20], [f:15-20, 28-29], [f:15-20; 28], [f#L15-L20],
[f:L15-L20], [f line 15], [f:lines 15-20], and the same in parentheses,
backticks or bare in the text, several files to a bracket included.

Each cited range must be in a file the repo map lists, inside the file, and at
most MAX_CITATION_LINES lines: a wider range teaches nothing. A reference that
is not in square brackets and names no real file (a URL, a host:port) is not
treated as a citation. A "documented" label must point at a commit or doc the
tutor actually saw in this turn's tool results; otherwise it becomes "inferred".
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from plumb.tools.scope import OutOfScope, Scope

MAX_CITATION_LINES = 60

_NUMBER = r"L?\d+"
_RANGE = rf"{_NUMBER}(?:\s*(?:-|–|—|to)\s*{_NUMBER})?"
_RANGES = rf"{_RANGE}(?:\s*(?:,|;|and)\s*(?:lines?\s+)?{_RANGE})*"
# A path with a folder, or a file name with an extension.
_PATH = r"(?:[\w.@+-]+/)+[\w.@+-]+|[\w@+-][\w.@+-]*\.[A-Za-z0-9]+"
# Between path and lines: ":", ":lines", "#L", or " line(s) ".
_SEPARATOR = r"(?::\s*(?:lines?\s*)?|#|,?\s+lines?\s+)"
REFERENCE = re.compile(
    rf"(?<![\w/.])(?P<path>{_PATH}){_SEPARATOR}(?P<lines>{_RANGES})(?![\w])",
    re.IGNORECASE,
)
_ONE_RANGE = re.compile(
    rf"({_NUMBER})(?:\s*(?:-|–|—|to)\s*({_NUMBER}))?", re.IGNORECASE
)
BRACKETS = re.compile(r"\[([^\[\]\n]{1,300})\](?!\()")

LABEL = re.compile(r"\((inferred|documented:\s*([^)]+?))\s*\)", re.IGNORECASE)
COMMIT = re.compile(r"^(?:commit\s+)?([0-9a-f]{7,40})$", re.IGNORECASE)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
REASON_WORDS = re.compile(
    r"\b(because|so that|in order to|to avoid|which means|likely|probably|presumably)\b",
    re.IGNORECASE,
)

Label = Literal["documented", "inferred"]


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
class Found:
    """One citation as written: where it is in the text, and its ranges."""

    start: int
    end: int
    path: str
    ranges: tuple[tuple[int, int], ...]
    bracketed: bool


def _ranges(text: str) -> tuple[tuple[int, int], ...]:
    found = []
    for match in _ONE_RANGE.finditer(text):
        start = int(match.group(1).lstrip("Ll"))
        end = int(match.group(2).lstrip("Ll")) if match.group(2) else start
        found.append((start, end))
    return tuple(found)


def find(text: str) -> list[Found]:
    """Every citation in `text`, in order."""
    bracket_spans = [(m.start(), m.end()) for m in BRACKETS.finditer(text)]
    found = []
    for match in REFERENCE.finditer(text):
        bracketed = any(
            a <= match.start() and match.end() <= b for a, b in bracket_spans
        )
        found.append(
            Found(
                match.start(),
                match.end(),
                match.group("path"),
                _ranges(match.group("lines")),
                bracketed,
            )
        )
    return found


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
    # The text of each citation as written, and whether all its ranges passed.
    written: dict[str, bool] = field(default_factory=dict)


def check_range(scope: Scope, path: str, start: int, end: int) -> Citation:
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


def _is_real_file(scope: Scope, path: str) -> bool:
    try:
        scope.file(path)
    except OutOfScope:
        return False
    return True


def check_found(scope: Scope, item: Found) -> list[Citation] | None:
    """The checked ranges of one citation, or None if it is not a citation
    (outside square brackets and naming no real file)."""
    if not item.bracketed and not _is_real_file(scope, item.path):
        return None
    return [check_range(scope, item.path, a, b) for a, b in item.ranges]


def parse_citation(scope: Scope, raw: str) -> Citation | None:
    """The first range of the first citation in `raw` (e.g. a question's)."""
    for item in find(f"[{raw.strip().strip('[]')}]"):
        checked = check_found(scope, item)
        if checked:
            return checked[0]
    return None


def _source_seen(source: str, seen: str, files_read: list[str]) -> bool:
    commit = COMMIT.match(source.strip())
    if commit:
        return commit.group(1).lower()[:7] in seen.lower()
    path = source.strip().split(":")[0]
    return path in files_read


def check(text: str, scope: Scope, seen: str, files_read: list[str]) -> Report:
    """`seen` is every tool result of the turn, `files_read` the files opened."""
    report = Report(text=text)

    def relabel(match: re.Match[str]) -> str:
        source = match.group(2)
        if source and not _source_seen(source, seen, files_read):
            report.downgraded.append(source.strip())
            return "(inferred)"
        return match.group(0)

    report.text = LABEL.sub(relabel, text)
    checked: dict[str, list[Citation]] = {}
    for item in find(report.text):
        raw = report.text[item.start : item.end]
        if raw in checked:
            continue
        ranges = check_found(scope, item)
        if ranges is None:
            continue
        checked[raw] = ranges
        report.written[raw] = all(c.ok for c in ranges)
        report.citations += [c for c in ranges if c not in report.citations]

    for sentence in SENTENCE_END.split(report.text):
        sentence = sentence.strip()
        if not sentence:
            continue
        cited = next(
            (
                c
                for item in find(sentence)
                for c in checked.get(sentence[item.start : item.end], [])
                if c.ok
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


def strip_citations(text: str) -> str:
    """The text without citations, their brackets or labels (for summaries)."""
    for item in reversed(find(text)):
        start, end = item.start, item.end
        before, after = text[:start], text[end:]
        # Drop the whole [..], (..) or `..` around a citation when it holds nothing else.
        for left, right in (("[", "]"), ("(", ")"), ("`", "`")):
            opened = before.rstrip().endswith(left)
            closed = after.lstrip().startswith(right)
            if opened and closed:
                before = before.rstrip()[:-1]
                after = after.lstrip()[1:]
        text = before.rstrip() + after
    text = LABEL.sub("", text)
    text = re.sub(r"\[\s*[,;]?\s*\]|\(\s*\)", "", text)
    return re.sub(r"\s+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()
