"""Loop detection: stop the model re-running the same tool call in one turn.

A call repeats an earlier one when it has the same tool and arguments, or,
for read_file, the same file with an overlapping line range. The second such
call is not run: the model gets a reminder instead. The third ends the turn.
"""

import json
from collections.abc import Callable
from typing import Any

REMINDER_LINES = 5


class LoopDetected(Exception):
    def __init__(self, name: str, args: dict[str, Any]) -> None:
        super().__init__(f"{name} was called again with the same arguments: {args}")
        self.name = name
        self.args_ = args


def _overlaps(a: tuple[int, float], b: tuple[int, float]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


class LoopGuard:
    def __init__(self) -> None:
        self._results: dict[str, str] = {}
        self._ranges: dict[str, list[tuple[tuple[int, float], str]]] = {}
        self._repeats: dict[str, int] = {}
        self.blocked: list[tuple[str, dict[str, Any]]] = []

    def _earlier(self, name: str, args: dict[str, Any]) -> tuple[str | None, str]:
        """(earlier result if this call repeats one, key to store this call under)."""
        if name == "read_file":
            path = str(args.get("path", "")).strip().lstrip("./")
            start = int(args.get("start_line") or 1)
            end = args.get("end_line")
            span = (start, float(end) if end is not None else float("inf"))
            for earlier_span, result in self._ranges.get(path, []):
                if _overlaps(span, earlier_span):
                    return result, path
            return None, path
        key = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
        return self._results.get(key), key

    def run(self, name: str, args: dict[str, Any], call: Callable[[], str]) -> str:
        earlier, key = self._earlier(name, args)
        if earlier is not None:
            self._repeats[key] = self._repeats.get(key, 0) + 1
            if self._repeats[key] >= 2:
                raise LoopDetected(name, args)
            self.blocked.append((name, args))
            head = "\n".join(earlier.splitlines()[:REMINDER_LINES])
            return (
                f"You already called {name} with these arguments in this turn, so it "
                f"was not run again. The earlier result began:\n{head}\n"
                "Use what you have, or call a different tool."
            )
        result = call()
        if name == "read_file":
            start = int(args.get("start_line") or 1)
            end = args.get("end_line")
            span = (start, float(end) if end is not None else float("inf"))
            self._ranges.setdefault(key, []).append((span, result))
        else:
            self._results[key] = result
        return result
