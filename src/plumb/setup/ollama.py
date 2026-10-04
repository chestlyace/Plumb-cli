"""Talking to Ollama: is it there, start it, pull a model, try a model."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "http://localhost:11434"
WINDOWS_INSTALLER = "https://ollama.com/download/OllamaSetup.exe"
DOWNLOAD_PAGE = "https://ollama.com/download"
SERVER_WAIT_SECONDS = 60


class OllamaError(Exception):
    """Something Ollama refused or reported; the message is shown to her."""


@dataclass(frozen=True)
class Probe:
    ok: bool
    needs_signin: bool = False
    error: str | None = None


def find_binary() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    if sys.platform == "win32":
        # A fresh install isn't on this terminal's PATH yet.
        default = (
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
        )
        if default.exists():
            return str(default)
    return None


class Ollama:
    def __init__(self, host: str = DEFAULT_HOST) -> None:
        self.host = host.rstrip("/")

    def _request(self, path: str, body: dict | None = None, timeout: float = 10):  # type: ignore[no-untyped-def]
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.host + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        return urllib.request.urlopen(request, timeout=timeout)

    def version(self) -> str | None:
        """The server's version, or None if it isn't running."""
        try:
            with self._request("/api/version", timeout=3) as response:
                return json.load(response).get("version")
        except OSError, ValueError:
            return None

    def wait_until_up(self, seconds: float = SERVER_WAIT_SECONDS) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.version():
                return True
            time.sleep(1)
        return False

    def start_server(self, binary: str) -> bool:
        """Start `ollama serve` in the background and wait for it."""
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        subprocess.Popen(
            [binary, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            start_new_session=sys.platform != "win32",
        )
        return self.wait_until_up()

    def has_model(self, name: str) -> bool:
        try:
            with self._request("/api/tags") as response:
                models = json.load(response).get("models", [])
        except OSError, ValueError:
            return False
        wanted = name if ":" in name else f"{name}:latest"
        return any(m.get("name") == wanted or m.get("model") == wanted for m in models)

    def pull(self, name: str) -> Iterator[tuple[str, int, int]]:
        """Pull a model, yielding (status, completed bytes, total bytes)."""
        try:
            response = self._request(
                "/api/pull", {"model": name, "stream": True}, timeout=600
            )
        except urllib.error.HTTPError as error:
            raise OllamaError(_error_text(error)) from None
        except OSError as error:
            raise OllamaError(f"Ollama isn't reachable: {error}") from None
        with response:
            for raw in response:
                if not raw.strip():
                    continue
                event = json.loads(raw)
                if "error" in event:
                    raise OllamaError(event["error"])
                yield (
                    event.get("status", ""),
                    event.get("completed", 0),
                    event.get("total", 0),
                )

    def probe(self, name: str) -> Probe:
        """Ask the model for one token: is it reachable, and are we signed in?"""
        body = {
            "model": name,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "options": {"num_predict": 1},
        }
        try:
            with self._request("/api/chat", body, timeout=60) as response:
                reply = json.load(response)
        except urllib.error.HTTPError as error:
            text = _error_text(error)
            signin = (
                error.code in (401, 403)
                or "sign" in text.lower()
                or "unauthorized" in text.lower()
            )
            return Probe(ok=False, needs_signin=signin, error=text)
        except (OSError, ValueError) as error:
            return Probe(ok=False, error=str(error))
        if "error" in reply:
            return Probe(ok=False, error=reply["error"])
        return Probe(ok=True)


def _error_text(error: urllib.error.HTTPError) -> str:
    try:
        return json.loads(error.read()).get("error", str(error))
    except OSError, ValueError:
        return str(error)


def install_command() -> list[str] | None:
    """How to install Ollama on this machine, or None to download it by hand."""
    if sys.platform == "win32":
        if shutil.which("winget"):
            return [
                "winget",
                "install",
                "--id",
                "Ollama.Ollama",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ]
        return None  # Falls back to downloading OllamaSetup.exe.
    if sys.platform == "darwin":
        return ["brew", "install", "ollama"] if shutil.which("brew") else None
    return ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]


def install() -> bool:
    """Run Ollama's installer. Returns whether it reported success."""
    command = install_command()
    if command is not None:
        return subprocess.run(command, check=False).returncode == 0
    if sys.platform == "win32":
        target = Path(tempfile.gettempdir()) / "OllamaSetup.exe"
        urllib.request.urlretrieve(WINDOWS_INSTALLER, target)
        return subprocess.run([str(target)], check=False).returncode == 0
    return False


def update_command() -> list[str] | None:
    if sys.platform == "win32" and shutil.which("winget"):
        return [
            "winget",
            "upgrade",
            "--id",
            "Ollama.Ollama",
            "-e",
            "--accept-source-agreements",
            "--accept-package-agreements",
        ]
    return install_command()


def signin(binary: str) -> bool:
    """`ollama signin`: she finishes it in her browser."""
    return subprocess.run([binary, "signin"], check=False).returncode == 0
