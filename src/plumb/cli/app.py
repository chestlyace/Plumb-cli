"""The `tutor` command."""

import asyncio
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import typer

from plumb.config import CONFIG_PATH, ConfigError, load_config
from plumb.memory.io import CorruptMemoryFile
from plumb.memory.paths import find_project_root, find_tutor_dir
from plumb.repomap.builder import build_repo_map
from plumb.repomap.model import RepoMap

app = typer.Typer(
    help="Teaches you your own codebase, and checks you understand each architectural decision.",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _refresh_repo_map(root: Path, tutor_dir: Path) -> RepoMap:
    """Build or refresh .tutor/repo-map.json before any command runs."""
    repo_map = build_repo_map(root, tutor_dir, _now())
    levels = Counter(record.parsed for record in repo_map.files.values())
    typer.echo(
        typer.style(
            f"Repo map: {len(repo_map.files)} files "
            f"({levels['full']} full, {levels['symbols']} symbols only, "
            f"{levels['fallback']} fallback), "
            f"{len(repo_map.entry_points)} entry points.",
            dim=True,
        )
    )
    return repo_map


def _chat(first_message: str | None, plain: bool) -> None:
    """Open the chat in the current repo."""
    from plumb.cli.text_frontend import TextFrontEnd, plain_chat
    from plumb.engine.chat import ChatSession
    from plumb.engine.frontend import FrontEnd
    from plumb.engine.pydantic_driver import PydanticAIDriver
    from plumb.tools.toolbox import Toolbox

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not CONFIG_PATH.exists() and interactive:
        _setup()  # The first run sets everything up.
    try:
        config, created = load_config()
    except ConfigError as error:
        typer.echo(f"Config problem: {error}", err=True)
        raise typer.Exit(1) from None
    if created:
        typer.echo(
            typer.style(f"Created {CONFIG_PATH} with default settings.", dim=True)
        )
    root, tutor_dir = find_project_root(), find_tutor_dir()
    try:
        repo_map = _refresh_repo_map(root, tutor_dir)
        toolbox = Toolbox(root, tutor_dir, repo_map)
        driver = PydanticAIDriver(config, toolbox)

        def make_chat(frontend: FrontEnd) -> ChatSession:
            return ChatSession(driver, toolbox, tutor_dir, frontend, _now)

        if plain or not interactive:
            frontend = TextFrontEnd()
            asyncio.run(plain_chat(make_chat(frontend), frontend, first_message))
        else:
            from plumb.ui.app import run_chat

            run_chat(root.name, config.model_name, make_chat, first_message)
    except CorruptMemoryFile as error:
        typer.echo(
            f"{error.path} is unreadable; fix or delete it, then try again.", err=True
        )
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        typer.echo("\nStopped. Anything already answered is saved.")
        raise typer.Exit(130) from None


def _setup() -> None:
    from plumb.setup.wizard import SetupFailed, run_setup

    try:
        run_setup()
    except SetupFailed as error:
        typer.echo(f"\n{error}", err=True)
        raise typer.Exit(1) from None
    except KeyboardInterrupt, EOFError:
        typer.echo("\nSetup stopped. Run `tutor setup` any time to finish it.")
        raise typer.Exit(130) from None


PLAIN = typer.Option(
    False, "--plain", help="A plain-text chat instead of the full screen."
)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context, plain: bool = PLAIN) -> None:
    """Open a chat about the codebase you're in."""
    if ctx.invoked_subcommand is None:
        _chat(None, plain)


@app.command()
def plan(
    request: str = typer.Argument(..., help="The change you want to make."),
    plain: bool = PLAIN,
) -> None:
    """Open the chat and talk through a change before the code is written."""
    _chat(f"/plan {request}", plain)


@app.command()
def review(
    ref: str = typer.Argument(
        "", help="Review everything since this git ref; default: uncommitted changes."
    ),
    plain: bool = PLAIN,
) -> None:
    """Open the chat and review a change after it was made."""
    _chat(f"/review {ref}".strip(), plain)


@app.command()
def tour(
    start: str = typer.Argument(
        "", help="The file to start from; default: pick an entry point."
    ),
    plain: bool = PLAIN,
) -> None:
    """Open the chat and take a tour of the repo from an entry point outward."""
    _chat(f"/tour {start}".strip(), plain)


@app.command()
def setup() -> None:
    """Set up Ollama, the local model and the fallback model (again)."""
    _setup()
