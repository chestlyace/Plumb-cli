"""The `tutor` command."""

from collections import Counter
from datetime import UTC, datetime

import typer

from plumb.memory.paths import find_project_root, find_tutor_dir
from plumb.repomap.builder import build_repo_map

app = typer.Typer(
    help="Teaches you your own codebase, and checks you understand each architectural decision.",
    no_args_is_help=True,
)


def _refresh_repo_map() -> None:
    """Build or refresh .tutor/repo-map.json before any command runs."""
    repo_map = build_repo_map(find_project_root(), find_tutor_dir(), datetime.now(UTC))
    levels = Counter(record.parsed for record in repo_map.files.values())
    typer.echo(
        f"Repo map: {len(repo_map.files)} files "
        f"({levels['full']} full, {levels['symbols']} symbols only, "
        f"{levels['fallback']} fallback), "
        f"{len(repo_map.entry_points)} entry points."
    )


@app.command()
def plan(
    request: str = typer.Argument(..., help="The feature you want to add."),
) -> None:
    """Talk through an architectural decision before the code is written."""
    _refresh_repo_map()
    typer.echo("plan is not implemented yet.")


@app.command()
def review() -> None:
    """Check understanding of a change after it was made."""
    _refresh_repo_map()
    typer.echo("review is not implemented yet.")


@app.command()
def tour() -> None:
    """Walk through the repo from its entry points outward."""
    _refresh_repo_map()
    typer.echo("tour is not implemented yet.")
