"""The `review` flow: read what changed, compare it with recorded decisions,
ask about it, then offer to revisit a skipped question."""

from datetime import timedelta

from plumb.engine import prompts
from plumb.engine.diff import DiffError, fit, read_diff
from plumb.engine.frontend import AskChoice, Info, Status
from plumb.engine.plan import PlanSession
from plumb.engine.schemas import Question, Questions
from plumb.memory.decisions import Decision, load_decisions
from plumb.memory.skipped import load_skipped
from plumb.repomap.builder import build_repo_map

MAX_DECISIONS = 10
MAX_REVISIT_CHOICES = 3
# Confirmed decisions this close to the newest one count as "the last plan".
LAST_PLAN_WINDOW = timedelta(hours=2)


def relevant_decisions(decisions: list[Decision], paths: list[str]) -> list[Decision]:
    """Decisions about the changed files, newest first, then the confirmed
    decisions from her most recent plan; at most MAX_DECISIONS."""
    changed = set(paths)
    by_file = sorted(
        (d for d in decisions if d.source_file in changed),
        key=lambda d: d.recorded_at,
        reverse=True,
    )
    confirmed = [d for d in decisions if d.command == "plan" and d.label == "confirmed"]
    last_plan: list[Decision] = []
    if confirmed:
        newest = max(d.recorded_at for d in confirmed)
        last_plan = [d for d in confirmed if d.recorded_at >= newest - LAST_PLAN_WINDOW]
    chosen: list[Decision] = []
    for decision in [*by_file, *last_plan]:
        if decision.id not in {c.id for c in chosen}:
            chosen.append(decision)
    return chosen[:MAX_DECISIONS]


def _describe(d: Decision) -> str:
    lines = f":{d.lines[0]}-{d.lines[1]}" if d.lines else ""
    return f"{d.decision} - {d.reason} ({d.label}; {d.source_file}{lines})"


class ReviewFlow:
    def __init__(self, session: PlanSession) -> None:
        self.session = session

    async def run(self, ref: str | None = None) -> None:
        s = self.session
        s.recorder.start("review")
        s.recorder.log("request", text=f"/review {ref or ''}".strip())
        s.frontend.emit(Status("Reading what changed"))

        root = s.toolbox.scope.root
        s.toolbox.refresh(build_repo_map(root, s.tutor_dir, s.recorder.now()))
        try:
            diff = read_diff(root, set(s.toolbox.files), ref)
        except DiffError as error:
            s.recorder.log("diff_error", error=str(error))
            s.frontend.emit(Info(f"I couldn't read the changes: {error}"))
            s._finish()
            return
        if not diff.files:
            s.frontend.emit(Info(f"Nothing to review: no {diff.source}."))
            s._finish()
            return

        decisions = relevant_decisions(
            load_decisions(s.tutor_dir).decisions, diff.paths
        )
        fit(diff, first={d.source_file for d in decisions})
        added = sum(f.added for f in diff.files)
        removed = sum(f.removed for f in diff.files)
        s.recorder.log(
            "diff",
            source=diff.source,
            shown=[f.path for f in diff.shown],
            listed=[f.path for f in diff.listed],
            decisions=[d.id for d in decisions],
        )
        s.frontend.emit(
            Info(
                f"Reviewing {diff.source}: {len(diff.files)} files, +{added} -{removed}, "
                f"against {len(decisions)} recorded decisions."
            )
        )
        explained = await s._explain(
            "review",
            prompts.review_instructions(s._memory_context()),
            prompts.review_prompt(
                diff.source, diff.render(), [_describe(d) for d in decisions]
            ),
        )
        if explained is not None:
            _, report = explained
            s.frontend.emit(Status("Writing questions"))
            asked = await s._turn(
                "questions",
                prompts.REVIEW_QUESTIONS_INSTRUCTIONS,
                prompts.questions_prompt(
                    diff.source, report, s._snippets(report), label="What changed"
                ),
                output_type=Questions,
            )
            if asked.done is not None:
                await s._ask_all(asked.done.output.questions)
        await self._revisit()
        s._finish()

    async def _revisit(self) -> None:
        """At the calm end of a review, offer one skipped question again."""
        s = self.session
        skipped = list(reversed(load_skipped(s.tutor_dir).skipped))
        latest: dict[str, str] = {}
        for entry in skipped:
            latest.setdefault(entry.topic, entry.question)
        if not latest:
            return
        topics = list(latest.items())[:MAX_REVISIT_CHOICES]
        answer = await s.frontend.ask(
            AskChoice(
                f"You skipped {len(skipped)} question{'s' if len(skipped) != 1 else ''} "
                "before. Revisit one?",
                [f"{topic}: {question}" for topic, question in topics] + ["Not now"],
            )
        )
        s.recorder.log("revisit_offer", kind=answer.kind, option=answer.option)
        if answer.kind != "option" or not answer.option or answer.option > len(topics):
            return
        topic, question = topics[answer.option - 1]
        s.frontend.emit(Status("Rebuilding that question against your current code"))
        rebuilt = await s._turn(
            "revisit",
            prompts.REVISIT_INSTRUCTIONS,
            prompts.revisit_prompt(topic, question),
            output_type=Question,
        )
        if rebuilt.done is None:
            return
        # Keep the stored topic so answering clears it from the skipped list.
        fresh: Question = rebuilt.done.output.model_copy(update={"concept": topic})
        await s._ask_all([fresh])
