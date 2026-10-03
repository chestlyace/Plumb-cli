"""The `tutor` command."""

import typer

app = typer.Typer(
    help="Teaches you your own codebase, and checks you understand each architectural decision.",
    no_args_is_help=True,
)


@app.command()
def plan(
    request: str = typer.Argument(..., help="The feature you want to add."),
) -> None:
    """Talk through an architectural decision before the code is written."""
    typer.echo("plan is not implemented yet.")


@app.command()
def review() -> None:
    """Check understanding of a change after it was made."""
    typer.echo("review is not implemented yet.")


@app.command()
def tour() -> None:
    """Walk through the repo from its entry points outward."""
    typer.echo("tour is not implemented yet.")
