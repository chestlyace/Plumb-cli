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
    no_args_is_help=True,
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


@app.command()
def plan(
    request: str = typer.Argument(..., help="The feature you want to add."),
    plain: bool = typer.Option(
        False, "--plain", help="Plain text instead of the full-screen interface."
    ),
) -> None:
    """Talk through an architectural decision before the code is written."""
    from plumb.cli.text_frontend import TextFrontEnd
    from plumb.engine.frontend import FrontEnd
    from plumb.engine.plan import PlanSession
    from plumb.engine.pydantic_driver import PydanticAIDriver
    from plumb.tools.toolbox import Toolbox

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

        async def run_session(frontend: FrontEnd) -> None:
            await PlanSession(driver, toolbox, tutor_dir, frontend, _now).run(request)

        if plain or not (sys.stdin.isatty() and sys.stdout.isatty()):
            asyncio.run(run_session(TextFrontEnd()))
        else:
            from plumb.ui.app import TutorApp

            TutorApp(request, config.model_name, run_session).run()
    except CorruptMemoryFile as error:
        typer.echo(
            f"{error.path} is unreadable; fix or delete it, then try again.", err=True
        )
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        typer.echo("\nStopped. Anything already answered is saved.")
        raise typer.Exit(130) from None


@app.command()
def review() -> None:
    """Check understanding of a change after it was made."""
    _refresh_repo_map(find_project_root(), find_tutor_dir())
    typer.echo("review is not implemented yet.")


@app.command()
def tour() -> None:
    """Walk through the repo from its entry points outward."""
    _refresh_repo_map(find_project_root(), find_tutor_dir())
    typer.echo("tour is not implemented yet.")
