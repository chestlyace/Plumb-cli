"""Widgets for the tutor screen."""

import re

from textual.containers import Vertical, VerticalScroll
from textual.widgets import Markdown, OptionList, Static
from textual.widgets.option_list import Option

from plumb.engine.citations import CITATION, Report
from plumb.engine.frontend import AskCheck, AskQuestion

REFRESH_SECONDS = 0.05


def _highlight(text: str, bad: set[str] | None = None) -> str:
    """Show citations as inline code; struck through when the check failed."""

    def mark(match: re.Match[str]) -> str:
        code = f"`{match.group(0)}`"
        return f"~~{code}~~" if bad and match.group(0) in bad else code

    return CITATION.sub(mark, text)


class ExplanationLog(VerticalScroll):
    """The left pane: explanations, checks, feedback and notices, in order."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._block: Markdown | None = None
        self._text = ""
        self._refresh_pending = False

    def append_text(self, chunk: str) -> None:
        if self._block is None:
            self._block = Markdown(classes="explanation")
            self._text = ""
            self.mount(self._block)
        self._text += chunk
        if not self._refresh_pending:
            self._refresh_pending = True
            self.set_timer(REFRESH_SECONDS, self._render_block)

    def _render_block(self) -> None:
        self._refresh_pending = False
        if self._block is not None:
            self._block.update(_highlight(self._text))
            self.scroll_end(animate=False)

    def end_text(self) -> None:
        self._render_block()
        self._block = None

    def checked(self, report: Report) -> None:
        """Re-render the last explanation with bad citations struck through."""
        bad = {
            f"[{c.file}:{c.start}{'-' + str(c.end) if c.end != c.start else ''}]"
            for c in report.citations
            if not c.ok
        }
        blocks = list(self.query(".explanation").results(Markdown))
        if blocks and bad:
            blocks[-1].update(_highlight(report.text, bad))
        wrong = [c for c in report.citations if not c.ok]
        line = f"✓ {len(report.citations) - len(wrong)} citations ok"
        if wrong:
            line += f", ✗ {len(wrong)} wrong: " + "; ".join(
                f"{c} ({c.problem})" for c in wrong
            )
        if report.downgraded:
            line += f" · {len(report.downgraded)} 'documented' label(s) never seen, shown as inferred"
        if report.untagged:
            line += f" · {len(report.untagged)} untagged reason(s) count as inferred"
        self.add_line(line, "check")

    def add_line(self, text: str, kind: str) -> None:
        self.mount(Static(text, classes=kind, markup=False))
        self.scroll_end(animate=False)


class QuestionPanel(Vertical):
    """The right pane: the open question and its options, or the summary."""

    def compose(self):  # type: ignore[no-untyped-def]
        yield Static("Waiting for the tutor…", id="question-title", markup=False)
        yield Markdown("", id="question-body")
        yield OptionList(id="options")

    def _show(self, title: str, body: str, options: list[tuple[str, str]]) -> None:
        self.query_one("#question-title", Static).update(title)
        self.query_one("#question-body", Markdown).update(body)
        option_list = self.query_one("#options", OptionList)
        option_list.clear_options()
        items: list[Option | None] = []
        for key, text in options:
            if items:
                items.append(None)  # A separator line keeps long answers apart.
            items.append(Option(f"{key}  {text}", id=key))
        option_list.add_options(items)
        option_list.display = bool(options)
        if options:
            option_list.highlighted = 0
            option_list.focus()

    def show_question(self, prompt: AskQuestion | AskCheck) -> list[str]:
        """Show the prompt; return the keys it accepts."""
        if isinstance(prompt, AskCheck):
            check = prompt.check
            options = [(str(n), text) for n, text in enumerate(check.options, 1)]
            options.append(("s", "Skip"))
            self._show("Check question", check.question, options)
        else:
            q = prompt.question
            options = [
                (str(n), f"{o.label} - {o.explanation}")
                for n, o in enumerate(q.options, 1)
            ]
            if prompt.allow_dont_understand:
                options.append(("?", "I don't understand"))
            options += [("s", "Skip"), ("q", "Skip the rest")]
            self._show(
                f"Question {prompt.number}/{prompt.total} · {q.concept_name}",
                f"{q.question}\n\n`[{q.citation}]`",
                options,
            )
        return [key for key, _ in options]

    def show_summary(self, changes: list[str]) -> None:
        body = (
            "\n".join(f"- {c}" for c in changes)
            if changes
            else "Nothing to remember this time."
        )
        self._show("Memory updated", body + "\n\n**Press q or Enter to exit.**", [])

    def show_waiting(self, text: str) -> None:
        self._show(text, "", [])
