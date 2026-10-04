"""Memory updates and the session transcript. Plain code, no model calls."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from plumb.engine.citations import Citation, Reason
from plumb.engine.schemas import Question
from plumb.memory.commands import Command
from plumb.memory.decisions import Decision, load_decisions, save_decisions
from plumb.memory.mastery import Concept, load_mastery, save_mastery
from plumb.memory.sessions import SessionEvent, append_event, new_session_path
from plumb.memory.skipped import SkippedQuestion, load_skipped, save_skipped

MAX_LOGGED_TOOL_RESULT = 2000


class Recorder:
    def __init__(
        self, tutor_dir: Path, command: Command, now: Callable[[], datetime]
    ) -> None:
        self.tutor_dir = tutor_dir
        self.now = now
        self.session_path = new_session_path(tutor_dir, command, now())
        # The flow being recorded (plan, ask, ...); a chat runs several.
        self.command: Command = command
        self.changes: list[str] = []

    def start(self, flow: Command) -> None:
        """Begin recording one request of the given flow."""
        self.command = flow
        self.changes = []

    def log(self, type_: str, **payload: Any) -> None:
        if (
            type_ == "tool_result"
            and len(payload.get("content", "")) > MAX_LOGGED_TOOL_RESULT
        ):
            payload["content"] = (
                payload["content"][:MAX_LOGGED_TOOL_RESULT] + " …(truncated)"
            )
        payload.setdefault("flow", self.command)
        append_event(
            self.session_path, SessionEvent(ts=self.now(), type=type_, payload=payload)
        )

    def _touch(self, question: Question, citation: Citation | None) -> Concept:
        mastery = load_mastery(self.tutor_dir)
        concept = mastery.concepts.get(question.concept)
        if concept is None:
            concept = Concept(
                name=question.concept_name, level="solid", last_seen=self.now()
            )
        concept.last_seen = self.now()
        concept.times_asked += 1
        if citation and citation.ok and citation.file not in concept.files:
            concept.files.append(citation.file)
        mastery.concepts[question.concept] = concept
        save_mastery(self.tutor_dir, mastery)
        self._clear_skipped(question.concept)
        return concept

    def _set_level(self, slug: str, level: str, correct: bool = False) -> None:
        mastery = load_mastery(self.tutor_dir)
        concept = mastery.concepts[slug]
        concept.level = level  # type: ignore[assignment]
        if correct:
            concept.times_correct += 1
        save_mastery(self.tutor_dir, mastery)

    def _clear_skipped(self, slug: str) -> None:
        skipped = load_skipped(self.tutor_dir)
        kept = [s for s in skipped.skipped if s.topic != slug]
        if len(kept) != len(skipped.skipped):
            skipped.skipped = kept
            save_skipped(self.tutor_dir, skipped)
            self.changes.append(f"{slug}: cleared from skipped questions")

    def _decide(
        self, decision: str, reason: str, label: str, citation: Citation
    ) -> None:
        decisions = load_decisions(self.tutor_dir)
        decisions.decisions.append(
            Decision(
                decision=decision,
                reason=reason,
                label=label,  # type: ignore[arg-type]
                source_file=citation.file,
                lines=(citation.start, citation.end),
                command=self.command,
                recorded_at=self.now(),
            )
        )
        save_decisions(self.tutor_dir, decisions)

    def reasons(self, reasons: list[Reason], commit_subjects: dict[str, str]) -> int:
        """Log each cited reason from an explanation; return how many."""
        logged = 0
        for reason in reasons:
            if reason.citation is None or not reason.citation.ok:
                self.log(
                    "reason_not_logged",
                    sentence=reason.sentence,
                    why="no valid citation",
                )
                continue
            if reason.label == "documented" and reason.source:
                subject = commit_subjects.get(reason.source.strip()[:7].lower())
                origin = (
                    f"commit {reason.source}: {subject}" if subject else reason.source
                )
            else:
                origin = "the tutor's inference"
            self._decide(reason.sentence, origin, reason.label, reason.citation)
            logged += 1
        if logged:
            self.changes.append(f"{logged} reasons logged to decisions.json")
        return logged

    def picked(
        self,
        question: Question,
        option: int,
        citation: Citation | None,
        counted: bool = False,
    ) -> None:
        """`counted`: the question was already counted (after "I don't understand")."""
        if not counted:
            self._touch(question, citation)
        chosen = question.options[option - 1]
        if citation and citation.ok:
            self._decide(
                f"{question.question} -> {chosen.label}",
                chosen.explanation,
                "confirmed",
                citation,
            )
            self.changes.append(f"decision confirmed: {chosen.label}")
        self.changes.append(f"{question.concept}: seen")

    def dont_understand(self, question: Question, citation: Citation | None) -> None:
        self._touch(question, citation)
        self._set_level(question.concept, "shaky")
        self.changes.append(f"{question.concept}: shaky")

    def checked(self, question: Question, correct: bool) -> None:
        self._set_level(
            question.concept, "solid" if correct else "shaky", correct=correct
        )
        self.changes.append(
            f"{question.concept}: {'solid' if correct else 'still shaky'}"
        )

    def skipped(self, question: Question) -> None:
        skipped = load_skipped(self.tutor_dir)
        skipped.skipped.append(
            SkippedQuestion(
                topic=question.concept,
                question=question.question,
                command=self.command,
                skipped_at=self.now(),
            )
        )
        save_skipped(self.tutor_dir, skipped)
        self.changes.append(f"{question.concept}: skipped for later")
