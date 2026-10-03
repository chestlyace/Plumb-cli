"""Parse files in a child process, so a native crash or a hang in a grammar
only costs the one file it happened on.

Protocol: one JSON request per line on stdin ({"rel": ..., "source": ...}),
one JSON ParseResult per line on the protocol pipe.
"""

import json
import os
import selectors
import subprocess
import sys
import time
from types import TracebackType
from typing import Self

from pydantic import TypeAdapter

from plumb.repomap.parse import ParseResult, detect_language, parse_file

TIMEOUT_SECONDS = 30.0

_RESULT = TypeAdapter(ParseResult)


def main() -> None:
    # Keep the protocol on its own descriptor; anything the parsing libraries
    # print to stdout goes to stderr instead.
    protocol = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    for line in sys.stdin.buffer:
        request = json.loads(line)
        result = parse_file(request["rel"], request["source"])
        protocol.write(_RESULT.dump_json(result) + b"\n")
        protocol.flush()


class IsolatedParser:
    """Sends files to a worker process, started on first use and restarted
    after a crash or timeout."""

    def __init__(
        self,
        timeout: float = TIMEOUT_SECONDS,
        command: list[str] | None = None,
    ) -> None:
        self.timeout = timeout
        self.command = command or [sys.executable, "-m", "plumb.repomap.worker"]
        self._process: subprocess.Popen[bytes] | None = None
        self._buffer = b""

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _start(self) -> subprocess.Popen[bytes]:
        if self._process is None or self._process.poll() is not None:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self._buffer = b""
        return self._process

    def _read_line(self, process: subprocess.Popen[bytes]) -> bytes | None:
        """One line from the worker, or None on EOF or timeout."""
        assert process.stdout is not None
        fd = process.stdout.fileno()
        deadline = time.monotonic() + self.timeout
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            while b"\n" not in self._buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    return None
                chunk = os.read(fd, 65536)
                if not chunk:
                    return None
                self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line

    def _kill(self) -> None:
        if self._process is not None:
            self._process.kill()
            self._process.wait()
            self._process = None

    def parse(self, rel: str, source: str) -> ParseResult:
        process = self._start()
        assert process.stdin is not None
        request = json.dumps({"rel": rel, "source": source}).encode() + b"\n"
        try:
            process.stdin.write(request)
            process.stdin.flush()
        except OSError:
            line = None
        else:
            line = self._read_line(process)
        if line is None:
            self._kill()
            return ParseResult(language=detect_language(rel), parsed="fallback")
        return _RESULT.validate_json(line)

    def close(self) -> None:
        if self._process is None:
            return
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._kill()
        self._process = None


if __name__ == "__main__":
    main()
