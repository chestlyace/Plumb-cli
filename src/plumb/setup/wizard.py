"""`tutor setup`: get Ollama running, pull the local model, pick a fallback.

Runs on the first `tutor`, and again whenever she runs `tutor setup`.
"""

import subprocess
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import typer
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TransferSpeedColumn,
)

from plumb.config import CONFIG_PATH, config_text
from plumb.setup import ollama as ol
from plumb.setup.system import (
    LARGER_MODEL,
    MODEL_SIZES,
    SMALL_MODEL,
    recommend_model,
    total_ram_gb,
)

CLOUD_CHOICES = [
    (
        "gemma4:31b-cloud",
        "Gemma 4 31B on Ollama's servers (recommended: the same family, much larger)",
    ),
    ("gpt-oss:120b-cloud", "OpenAI's open-weight gpt-oss 120B on Ollama's servers"),
    ("gpt-oss:20b-cloud", "OpenAI's open-weight gpt-oss 20B on Ollama's servers"),
]
NEWER_OLLAMA = "newer version"


def _run(command: list[str]) -> bool:
    return subprocess.run(command, check=False).returncode == 0


class SetupFailed(Exception):
    """Setup can't continue; the message says what to do."""


@dataclass
class Deps:
    """Everything setup touches outside Python, so tests can replace it."""

    server: ol.Ollama = field(default_factory=ol.Ollama)
    find_binary: Callable[[], str | None] = ol.find_binary
    install: Callable[[], bool] = ol.install
    update_command: Callable[[], list[str] | None] = ol.update_command
    run: Callable[[list[str]], bool] = lambda cmd: (
        __import__("subprocess").run(cmd, check=False).returncode == 0
    )
    signin: Callable[[str], bool] = ol.signin
    ram_gb: Callable[[], float | None] = total_ram_gb
    confirm: Callable[[str, bool], bool] = lambda text, default: typer.confirm(
        text, default=default
    )
    prompt: Callable[[str, str], str] = lambda text, default: typer.prompt(
        text, default=default
    )
    say: Callable[[str], None] = typer.echo
    open_url: Callable[[str], object] = webbrowser.open


def _choose(deps: Deps, title: str, options: list[str], default: int) -> int:
    deps.say(typer.style(title, bold=True))
    for number, option in enumerate(options, 1):
        deps.say(f"  {number}) {option}")
    while True:
        reply = deps.prompt("Choose", str(default)).strip()
        if reply.isdigit() and 1 <= int(reply) <= len(options):
            return int(reply)
        deps.say(f"Type a number from 1 to {len(options)}.")


def _ensure_ollama(deps: Deps) -> str:
    """Ollama installed and its server answering; returns the binary path."""
    binary = deps.find_binary()
    if binary is None and not deps.server.version():
        deps.say("Plumb runs its model with Ollama, which isn't installed yet.")
        if deps.confirm("Install Ollama now?", True):
            deps.say("Installing Ollama… (its installer may ask for permission)")
            if not deps.install():
                deps.say("The installer didn't finish.")
        binary = deps.find_binary()
        if binary is None and not deps.server.version():
            deps.say(
                f"Download Ollama from {ol.DOWNLOAD_PAGE}, install it, then come back."
            )
            deps.open_url(ol.DOWNLOAD_PAGE)
            deps.prompt("Press Enter once Ollama is installed", "")
            binary = deps.find_binary()
            if binary is None and not deps.server.version():
                raise SetupFailed(
                    f"Ollama still isn't installed. Get it from {ol.DOWNLOAD_PAGE}, then run `tutor setup`."
                )
    if not deps.server.version():
        deps.say("Starting Ollama…")
        if binary is None or not deps.server.start_server(binary):
            raise SetupFailed(
                "Ollama is installed but isn't running. Open the Ollama app, then run `tutor setup`."
            )
    deps.say(f"✓ Ollama {deps.server.version()} is running.")
    return binary or "ollama"


def _pull(deps: Deps, model: str) -> None:
    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
    ) as progress:
        task = progress.add_task(f"Pulling {model}", total=None)
        for status, completed, total in deps.server.pull(model):
            if total:
                progress.update(
                    task,
                    total=total,
                    completed=completed,
                    description=f"Pulling {model}",
                )
            else:
                progress.update(task, description=status.capitalize())


def _local_model(deps: Deps) -> str:
    ram = deps.ram_gb()
    recommended = recommend_model(ram)
    memory = f"{ram:.0f} GB of memory" if ram else "an unknown amount of memory"
    options = [
        f"{SMALL_MODEL} ({MODEL_SIZES[SMALL_MODEL]} download) - lighter, for 8 GB laptops",
        f"{LARGER_MODEL} ({MODEL_SIZES[LARGER_MODEL]} download) - stronger, needs more memory",
    ]
    default = 1 if recommended == SMALL_MODEL else 2
    options[default - 1] += "  ← recommended"
    choice = _choose(
        deps, f"Local model (this computer has {memory})", options, default
    )
    model = SMALL_MODEL if choice == 1 else LARGER_MODEL
    if deps.server.has_model(model):
        deps.say(f"✓ {model} is already downloaded.")
        return model
    try:
        _pull(deps, model)
    except ol.OllamaError as error:
        if NEWER_OLLAMA not in str(error):
            raise SetupFailed(f"Couldn't download {model}: {error}") from None
        deps.say("Your Ollama is too old for Gemma 4.")
        command = deps.update_command()
        if (
            command is None
            or not deps.confirm("Update Ollama now?", True)
            or not deps.run(command)
        ):
            raise SetupFailed(
                f"Update Ollama from {ol.DOWNLOAD_PAGE}, then run `tutor setup`."
            ) from None
        deps.server.wait_until_up()
        try:
            _pull(deps, model)
        except ol.OllamaError as again:
            raise SetupFailed(f"Couldn't download {model}: {again}") from None
    deps.say(f"✓ {model} is ready.")
    return model


def _fallback(deps: Deps, binary: str) -> str | None:
    deps.say("")
    deps.say(
        "If the local model fails (say it runs out of memory), Plumb can switch to a "
        "cloud model for the rest of that session. A cloud model runs on Ollama's "
        "servers, so the files it reads leave this computer while it's in use. "
        "Plumb always tells you when that happens."
    )
    options = [f"{name} - {about}" for name, about in CLOUD_CHOICES]
    options.append("No fallback - stay fully local")
    while True:
        choice = _choose(deps, "Fallback model", options, 1)
        if choice == len(options):
            deps.say("✓ No fallback: everything stays on this computer.")
            return None
        name = CLOUD_CHOICES[choice - 1][0]
        probe = deps.server.probe(name)
        if not probe.ok and probe.needs_signin:
            deps.say(
                "Cloud models need an ollama.com account. Signing in opens your browser."
            )
            if deps.confirm("Sign in to Ollama now?", True):
                deps.signin(binary)
                probe = deps.server.probe(name)
        if probe.ok:
            deps.say(f"✓ {name} answers.")
            return name
        deps.say(
            f"{name} isn't available: {probe.error}. Pick another, or no fallback."
        )


def run_setup(deps: Deps | None = None, path: Path = CONFIG_PATH) -> None:
    deps = deps or Deps()
    deps.say(typer.style("Welcome to Plumb! Let's get it ready.", bold=True))
    deps.say("This takes a few minutes the first time: the model is a big download.\n")
    binary = _ensure_ollama(deps)
    model = _local_model(deps)
    fallback = _fallback(deps, binary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config_text(model=model, fallback=fallback), encoding="utf-8")
    deps.say("")
    deps.say(typer.style("All set!", bold=True) + f" Settings saved to {path}.")
    deps.say(
        "Run `tutor` inside any project to start. `tutor setup` changes these choices."
    )
