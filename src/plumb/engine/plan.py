"""The `plan` flow: explain a change against her code, then ask about it."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from plumb.engine import prompts
from plumb.engine.citations import Report, check, parse_citation
from plumb.engine.driver import Driver
from plumb.engine.events import (
    FallbackUsed,
    RepeatBlocked,
    Retry,
    TextDelta,
    ToolCall,
    ToolResult,
    TurnDone,
    TurnStopped,
)
from plumb.engine.frontend import (
    AskCheck,
    AskQuestion,
    Checked,
    Feedback,
    FrontEnd,
    Notice,
    Status,
    Summary,
    Text,
    TextEnd,
)
from plumb.engine.recorder import Recorder
from plumb.engine.schemas import CheckQuestion, Question, Questions
from plumb.memory.decisions import load_decisions
from plumb.memory.mastery import load_mastery
from plumb.tools.toolbox import Toolbox

MAX_SNIPPETS = 6
MAX_SNIPPET_LINES = 40
GIT_LOG_LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2}\s+([0-9a-f]{7,40})\s+(.*)$", re.MULTILINE
)

STOP_MESSAGES = {
    "step_limit": "The tutor ran out of steps before finishing.",
    "loop": "The tutor kept repeating itself, so it was stopped.",
    "invalid_output": "The model's answer wasn't in the expected shape, even after one retry.",
    "model_error": "The model could not be reached.",
}


@dataclass
class TurnOutcome:
    done: TurnDone | None
    text: str = ""
    seen: list[str] = field(default_factory=list)  # Tool results, for label checks.


class PlanSession:
    def __init__(
        self,
        driver: Driver,
        toolbox: Toolbox,
        tutor_dir: Path,
        frontend: FrontEnd,
        now: Callable[[], datetime],
    ) -> None:
        self.driver = driver
        self.toolbox = toolbox
        self.frontend = frontend
        self.recorder = Recorder(tutor_dir, "plan", now)
        self.tutor_dir = tutor_dir

    async def _turn(
        self,
        job: str,
        instructions: str,
        prompt: str,
        output_type: type[Any] | None = None,
    ) -> TurnOutcome:
        outcome = TurnOutcome(done=None)
        self.recorder.log("turn_start", job=job, prompt=prompt)
        async for event in self.driver.run_turn(
            instructions, prompt, output_type=output_type
        ):
            match event:
                case TextDelta(text=chunk):
                    outcome.text += chunk
                    self.frontend.emit(Text(chunk))
                case ToolCall(name=name, args=args):
                    self.recorder.log("tool_call", name=name, args=args)
                    target = (
                        args.get("path")
                        or args.get("pattern")
                        or args.get("kind")
                        or ""
                    )
                    self.frontend.emit(Status(f"{name} {target}".strip()))
                case ToolResult(name=name, content=content):
                    outcome.seen.append(content)
                    self.recorder.log("tool_result", name=name, content=content)
                case FallbackUsed(to_model=to_model, reason=reason):
                    self.recorder.log("fallback", to_model=to_model, reason=reason)
                    if outcome.text:
                        self.frontend.emit(TextEnd())
                    outcome = TurnOutcome(done=None)
                    self.frontend.emit(
                        Notice(
                            f"The local model failed, so {to_model} is answering instead. "
                            "If it is a hosted model, the files it reads leave your machine.",
                            model=to_model,
                        )
                    )
                case Retry(name=name, reason=reason):
                    self.recorder.log("retry", name=name, reason=reason)
                case RepeatBlocked(name=name, args=args):
                    self.recorder.log("repeat_blocked", name=name, args=args)
                case TurnStopped(reason=reason, detail=detail):
                    self.recorder.log(
                        "turn_stopped", job=job, reason=reason, detail=detail
                    )
                    self.frontend.emit(Notice(STOP_MESSAGES[reason]))
                case TurnDone() as done:
                    outcome.done = done
                    self.recorder.log(
                        "turn_done", job=job, text=done.text, files=done.files_read
                    )
        if outcome.text:
            self.frontend.emit(TextEnd())
        return outcome

    def _memory_context(self) -> str:
        concepts = load_mastery(self.tutor_dir).concepts
        shaky = [c.name for c in concepts.values() if c.level == "shaky"]
        solid = [c.name for c in concepts.values() if c.level == "solid"]
        confirmed = [
            d.decision
            for d in load_decisions(self.tutor_dir).decisions
            if d.label == "confirmed"
        ][-5:]
        lines = [
            f"- Shaky on: {', '.join(shaky) or 'nothing yet'}",
            f"- Solid on: {', '.join(solid) or 'nothing yet'}",
        ]
        lines += [f"- She decided: {d}" for d in confirmed]
        return "\n".join(lines)

    def _snippets(self, report: Report) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        for citation in report.citations:
            if not citation.ok or str(citation) in seen or len(parts) == MAX_SNIPPETS:
                continue
            seen.add(str(citation))
            end = min(citation.end, citation.start + MAX_SNIPPET_LINES - 1)
            parts.append(self.toolbox.read_file(citation.file, citation.start, end))
        return "\n\n".join(parts) or "(no valid citations)"

    async def run(self, request: str) -> None:
        self.recorder.log("request", text=request)
        self.frontend.emit(Status("Looking at your code"))
        explained = await self._turn(
            "explain", prompts.explain_instructions(self._memory_context()), request
        )
        if explained.done is None:
            self.frontend.emit(Summary(self.recorder.changes))
            return

        seen = "\n".join(explained.seen)
        report = check(
            explained.text, self.toolbox.scope, seen, explained.done.files_read
        )
        self.recorder.log(
            "checked",
            citations=[
                {"citation": str(c), "ok": c.ok, "problem": c.problem}
                for c in report.citations
            ],
            downgraded=report.downgraded,
            untagged=report.untagged,
        )
        self.frontend.emit(Checked(report))
        commits = {h[:7].lower(): s for h, s in GIT_LOG_LINE.findall(seen)}
        self.recorder.reasons(report.reasons, commits)

        self.frontend.emit(Status("Writing questions"))
        asked = await self._turn(
            "questions",
            prompts.QUESTIONS_INSTRUCTIONS,
            prompts.questions_prompt(request, report, self._snippets(report)),
            output_type=Questions,
        )
        if asked.done is not None:
            await self._ask_all(asked.done.output.questions)
        self.recorder.log("summary", changes=self.recorder.changes)
        self.frontend.emit(Summary(self.recorder.changes))

    async def _ask_all(self, questions: list[Question]) -> None:
        for number, question in enumerate(questions, 1):
            citation = parse_citation(self.toolbox.scope, question.citation)
            prompt = AskQuestion(question, number, len(questions))
            answer = await self.frontend.ask(prompt)
            self.recorder.log(
                "answer",
                question=question.model_dump(),
                kind=answer.kind,
                option=answer.option,
            )
            counted = answer.kind == "dont_understand"
            if counted:
                self.recorder.dont_understand(question, citation)
                await self._teach(question)
                answer = await self.frontend.ask(
                    AskQuestion(
                        question, number, len(questions), allow_dont_understand=False
                    )
                )
                self.recorder.log(
                    "answer",
                    question=question.model_dump(),
                    kind=answer.kind,
                    option=answer.option,
                )
            if answer.kind == "option" and answer.option:
                self.recorder.picked(question, answer.option, citation, counted=counted)
            elif answer.kind == "skip_rest":
                for rest in questions[number - 1 :]:
                    self.recorder.skipped(rest)
                return
            elif answer.kind != "option":
                self.recorder.skipped(question)

    async def _teach(self, question: Question) -> None:
        self.frontend.emit(Status(f"Explaining {question.concept_name} with your code"))
        taught = await self._turn(
            "concept",
            prompts.CONCEPT_INSTRUCTIONS,
            prompts.concept_prompt(
                question.concept_name, question.question, question.citation
            ),
        )
        if taught.done is None:
            return
        report = check(
            taught.text,
            self.toolbox.scope,
            "\n".join(taught.seen),
            taught.done.files_read,
        )
        self.frontend.emit(Checked(report))
        made = await self._turn(
            "check_question",
            prompts.CHECK_INSTRUCTIONS,
            prompts.check_prompt(question.concept_name, report.text),
            output_type=CheckQuestion,
        )
        if made.done is None:
            return
        check_question: CheckQuestion = made.done.output
        answer = await self.frontend.ask(AskCheck(check_question))
        self.recorder.log(
            "check_answer",
            check=check_question.model_dump(),
            kind=answer.kind,
            option=answer.option,
        )
        if answer.kind != "option" or not answer.option:
            self.frontend.emit(Feedback("Skipped. No problem."))
            return
        correct = answer.option == check_question.correct
        self.recorder.checked(question, correct)
        verdict = (
            "Right."
            if correct
            else f"Not quite - it's option {check_question.correct}."
        )
        self.frontend.emit(Feedback(f"{verdict} {check_question.why}"))
