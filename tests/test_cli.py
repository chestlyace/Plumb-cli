from typer.testing import CliRunner

from plumb.cli.app import app

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("plan", "review", "tour"):
        assert command in result.output
