from dataclasses import dataclass, field
from pathlib import Path

import pytest

from plumb.config import load_config
from plumb.setup.ollama import OllamaError, Probe
from plumb.setup.system import recommend_model
from plumb.setup.wizard import Deps, SetupFailed, run_setup


@dataclass
class FakeOllama:
    running: bool = True
    models: set[str] = field(default_factory=set)
    pull_error: str | None = None
    signed_in: bool = True
    retired: set[str] = field(default_factory=set)
    pulled: list[str] = field(default_factory=list)
    started: bool = False

    def version(self) -> str | None:
        return "0.35.1" if self.running else None

    def wait_until_up(self, seconds: float = 0) -> bool:
        return self.running

    def start_server(self, binary: str) -> bool:
        self.started = True
        self.running = True
        return True

    def has_model(self, name: str) -> bool:
        return name in self.models

    def pull(self, name: str):  # type: ignore[no-untyped-def]
        if self.pull_error:
            error, self.pull_error = self.pull_error, None
            raise OllamaError(error)
        yield "pulling manifest", 0, 0
        yield "downloading", 50, 100
        yield "downloading", 100, 100
        self.pulled.append(name)
        self.models.add(name)

    def probe(self, name: str) -> Probe:
        if name in self.retired:
            return Probe(ok=False, error=f"{name} was retired")
        if not self.signed_in:
            return Probe(ok=False, needs_signin=True, error="unauthorized")
        return Probe(ok=True)


def deps(
    ollama: FakeOllama,
    answers: list[str],
    *,
    binary: str | None = "ollama",
    ram: float | None = 16.0,
    installs: bool = True,
    confirms: list[bool] | None = None,
) -> tuple[Deps, list[str], dict]:
    said: list[str] = []
    calls: dict = {"install": 0, "signin": 0, "run": [], "opened": []}
    replies = iter(answers)
    yes = iter(confirms or [])
    state = {"binary": binary}

    def install() -> bool:
        calls["install"] += 1
        if installs:
            state["binary"] = "ollama"
            ollama.running = True
        return installs

    def signin(_: str) -> bool:
        calls["signin"] += 1
        ollama.signed_in = True
        return True

    return (
        Deps(
            server=ollama,  # type: ignore[arg-type]
            find_binary=lambda: state["binary"],
            install=install,
            update_command=lambda: ["winget", "upgrade", "Ollama.Ollama"],
            run=lambda cmd: calls["run"].append(cmd) or True,
            signin=signin,
            ram_gb=lambda: ram,
            confirm=lambda text, default: next(yes, default),
            prompt=lambda text, default: (
                next(replies, default) or default
            ),  # Enter = default
            say=said.append,
            open_url=lambda url: calls["opened"].append(url),
        ),
        said,
        calls,
    )


def test_ram_recommendation() -> None:
    assert recommend_model(8.0) == "gemma4:e2b"
    assert recommend_model(7.6) == "gemma4:e2b"
    assert recommend_model(16.0) == "gemma4:e4b"
    assert recommend_model(None) == "gemma4:e4b"


def test_happy_path_writes_the_config(tmp_path: Path) -> None:
    ollama = FakeOllama()
    d, said, _ = deps(ollama, ["", ""])  # Accept both defaults.
    path = tmp_path / "plumb/config.toml"
    run_setup(d, path)
    config, created = load_config(path)
    assert not created
    assert (config.model_name, config.fallback_name) == (
        "gemma4:e4b",
        "gemma4:31b-cloud",
    )
    assert ollama.pulled == ["gemma4:e4b"]
    assert "✓ gemma4:e4b is ready." in said and "✓ gemma4:31b-cloud answers." in said


def test_small_laptop_gets_the_small_model_and_no_fallback(tmp_path: Path) -> None:
    ollama = FakeOllama()
    d, said, _ = deps(ollama, ["", "4"], ram=8.0)
    run_setup(d, tmp_path / "config.toml")
    config, _ = load_config(tmp_path / "config.toml")
    assert (config.model_name, config.fallback_name) == ("gemma4:e2b", None)
    assert any("gemma4:e2b" in line and "recommended" in line for line in said)
    assert "✓ No fallback: everything stays on this computer." in said


def test_installs_ollama_when_missing(tmp_path: Path) -> None:
    ollama = FakeOllama(running=False)
    d, said, calls = deps(ollama, ["", ""], binary=None, confirms=[True])
    run_setup(d, tmp_path / "config.toml")
    assert calls["install"] == 1
    assert "✓ Ollama 0.35.1 is running." in said


def test_declining_install_shows_the_download_link(tmp_path: Path) -> None:
    ollama = FakeOllama(running=False)
    d, _, calls = deps(ollama, [""], binary=None, confirms=[False])
    with pytest.raises(SetupFailed, match="Ollama still isn't installed"):
        run_setup(d, tmp_path / "config.toml")
    assert calls["install"] == 0 and calls["opened"] == ["https://ollama.com/download"]
    assert not (tmp_path / "config.toml").exists()


def test_starts_a_stopped_server(tmp_path: Path) -> None:
    ollama = FakeOllama(running=False)
    d, _, _ = deps(ollama, ["", ""])
    run_setup(d, tmp_path / "config.toml")
    assert ollama.started


def test_skips_the_download_when_the_model_is_there(tmp_path: Path) -> None:
    ollama = FakeOllama(models={"gemma4:e4b"})
    d, said, _ = deps(ollama, ["", ""])
    run_setup(d, tmp_path / "config.toml")
    assert ollama.pulled == [] and "✓ gemma4:e4b is already downloaded." in said


def test_old_ollama_is_updated_then_the_pull_retried(tmp_path: Path) -> None:
    ollama = FakeOllama(
        pull_error="pull model manifest: 412: requires a newer version of Ollama"
    )
    d, said, calls = deps(ollama, ["", ""], confirms=[True])
    run_setup(d, tmp_path / "config.toml")
    assert calls["run"] == [["winget", "upgrade", "Ollama.Ollama"]]
    assert (
        ollama.pulled == ["gemma4:e4b"]
        and "Your Ollama is too old for Gemma 4." in said
    )


def test_other_pull_errors_stop_setup(tmp_path: Path) -> None:
    d, _, _ = deps(FakeOllama(pull_error="disk full"), ["", ""])
    with pytest.raises(SetupFailed, match="Couldn't download gemma4:e4b: disk full"):
        run_setup(d, tmp_path / "config.toml")


def test_cloud_fallback_signs_in_when_needed(tmp_path: Path) -> None:
    ollama = FakeOllama(signed_in=False)
    d, _, calls = deps(ollama, ["", "1"], confirms=[True])
    run_setup(d, tmp_path / "config.toml")
    assert calls["signin"] == 1
    assert load_config(tmp_path / "config.toml")[0].fallback_name == "gemma4:31b-cloud"


def test_unavailable_cloud_model_asks_again(tmp_path: Path) -> None:
    ollama = FakeOllama(retired={"gemma4:31b-cloud"})
    d, said, _ = deps(ollama, ["", "1", "3"])
    run_setup(d, tmp_path / "config.toml")
    assert any("gemma4:31b-cloud isn't available" in line for line in said)
    assert load_config(tmp_path / "config.toml")[0].fallback_name == "gpt-oss:20b-cloud"


def test_bad_choices_are_asked_again(tmp_path: Path) -> None:
    d, said, _ = deps(FakeOllama(), ["9", "x", "1", "4"])
    run_setup(d, tmp_path / "config.toml")
    assert said.count("Type a number from 1 to 2.") == 2
    assert load_config(tmp_path / "config.toml")[0].model_name == "gemma4:e2b"
