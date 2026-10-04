import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic_ai.exceptions import ModelAPIError

from plumb.config import DEFAULT_CONFIG, Config, ConfigError, load_config
from plumb.engine.events import (
    FallbackUsed,
    RepeatBlocked,
    Retry,
    TextDelta,
    ToolCall,
    ToolResult,
    TurnDone,
    TurnStopped,
)
from plumb.engine.loop_guard import LoopDetected, LoopGuard
from plumb.engine.pydantic_driver import PydanticAIDriver
from plumb.repomap.builder import build_repo_map
from plumb.tools.toolbox import Toolbox
from tests.fake_model import Calls, scripted

NOW = datetime(2026, 10, 3, 14, 0, 0, tzinfo=UTC)
CONFIG = Config(
    model_name="local", endpoint="http://unused/v1", fallback_name=None, step_limit=4
)


@pytest.fixture
def toolbox(tmp_path: Path) -> Toolbox:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/db.py").write_text("class Db:\n    pass\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return Toolbox(
        tmp_path,
        tmp_path / ".tutor",
        build_repo_map(tmp_path, tmp_path / ".tutor", NOW),
    )


async def run(driver: PydanticAIDriver, **kwargs: object) -> list[object]:
    return [
        e async for e in driver.run_turn("You are a tutor.", "Explain db.py", **kwargs)
    ]


def kinds(events: list[object]) -> list[str]:
    return [type(e).__name__ for e in events]


async def test_tool_using_turn_streams_and_finishes(toolbox: Toolbox) -> None:
    model = scripted(Calls([("read_file", {"path": "app/db.py"})]), "Db is a class.")
    events = await run(PydanticAIDriver(CONFIG, toolbox, model=model))
    assert kinds(events) == [
        "ToolCall",
        "ToolResult",
        "TextDelta",
        "TextDelta",
        "TurnDone",
    ]
    call, result, *_, done = events
    assert isinstance(call, ToolCall) and call.args == {"path": "app/db.py"}
    assert isinstance(result, ToolResult) and "1| class Db:" in result.content
    assert (
        "".join(e.text for e in events if isinstance(e, TextDelta)) == "Db is a class."
    )
    assert isinstance(done, TurnDone)
    assert done.text == "Db is a class." and done.files_read == ["app/db.py"]


async def test_history_carries_into_the_next_turn(toolbox: Toolbox) -> None:
    driver = PydanticAIDriver(CONFIG, toolbox, model=scripted("first", "second"))
    first = (await run(driver))[-1]
    assert isinstance(first, TurnDone)
    second = (await run(driver, history=first.history))[-1]
    assert isinstance(second, TurnDone) and second.text == "second"
    assert len(second.history) == len(first.history) + 2


async def test_step_limit_stops_the_turn(toolbox: Toolbox) -> None:
    steps = [Calls([("grep", {"pattern": f"x{i}"})]) for i in range(10)]
    events = await run(PydanticAIDriver(CONFIG, toolbox, model=scripted(*steps)))
    stop = events[-1]
    assert isinstance(stop, TurnStopped) and stop.reason == "step_limit"
    assert sum(isinstance(e, ToolCall) for e in events) == 4


async def test_invalid_tool_call_gets_one_retry(toolbox: Toolbox) -> None:
    model = scripted(
        Calls([("read_file", {"pth": "x"})]),
        Calls([("read_file", {"path": "app/db.py"})]),
        "ok",
    )
    events = await run(PydanticAIDriver(CONFIG, toolbox, model=model))
    assert kinds(events)[:3] == ["Retry", "ToolCall", "ToolResult"]
    assert isinstance(events[-1], TurnDone)


async def test_second_invalid_tool_call_stops_the_turn(toolbox: Toolbox) -> None:
    model = scripted(Calls([("read_file", {"pth": "x"})]))
    events = await run(PydanticAIDriver(CONFIG, toolbox, model=model))
    assert isinstance(events[0], Retry)
    stop = events[-1]
    assert isinstance(stop, TurnStopped) and stop.reason == "invalid_output"


async def test_repeated_call_is_blocked_then_ends_the_turn(toolbox: Toolbox) -> None:
    read = Calls([("read_file", {"path": "app/db.py"})])
    overlapping = Calls([("read_file", {"path": "app/db.py", "start_line": 2})])
    config = Config("local", "http://unused/v1", None, step_limit=10)
    events = await run(
        PydanticAIDriver(config, toolbox, model=scripted(read, overlapping, read))
    )
    blocked = [e for e in events if isinstance(e, RepeatBlocked)]
    assert len(blocked) == 1 and blocked[0].args["start_line"] == 2
    reminder = [e for e in events if isinstance(e, ToolResult)][1]
    assert reminder.content.startswith("You already called read_file")
    stop = events[-1]
    assert isinstance(stop, TurnStopped) and stop.reason == "loop"
    assert stop.files_read == ["app/db.py"]


class Options(BaseModel):
    question: str
    options: list[str]


async def test_structured_output_with_one_retry(toolbox: Toolbox) -> None:
    good = {"question": "Why a class?", "options": ["Testing", "State"]}
    model = scripted("Here is my question: why a class?", json.dumps(good))
    events = await run(
        PydanticAIDriver(CONFIG, toolbox, model=model), output_type=Options
    )
    assert isinstance(events[0], Retry)
    done = events[-1]
    assert (
        isinstance(done, TurnDone)
        and done.output == Options(**good)
        and done.text is None
    )


async def test_fallback_after_model_error(toolbox: Toolbox) -> None:
    config = Config("local", "http://unused/v1", "cloud-model", step_limit=4)
    driver = PydanticAIDriver(
        config,
        toolbox,
        model=scripted(ModelAPIError("local", "model not found")),
        fallback_model=scripted("from the fallback"),
    )
    events = await run(driver)
    assert isinstance(events[0], FallbackUsed)
    assert (events[0].from_model, events[0].to_model) == ("local", "cloud-model")
    assert isinstance(events[-1], TurnDone) and events[-1].text == "from the fallback"


async def test_model_error_without_fallback(toolbox: Toolbox) -> None:
    driver = PydanticAIDriver(
        CONFIG, toolbox, model=scripted(ModelAPIError("local", "down"))
    )
    stop = (await run(driver))[-1]
    assert isinstance(stop, TurnStopped) and stop.reason == "model_error"


async def test_fallback_failing_too(toolbox: Toolbox) -> None:
    config = Config("local", "http://unused/v1", "cloud-model", step_limit=4)
    error = ModelAPIError("m", "down")
    driver = PydanticAIDriver(
        config, toolbox, model=scripted(error), fallback_model=scripted(error)
    )
    events = await run(driver)
    assert kinds(events) == ["FallbackUsed", "TurnStopped"]
    assert "fallback model failed too" in events[-1].detail


def test_loop_guard_ranges() -> None:
    guard = LoopGuard()
    ok = lambda: "result"
    guard.run("read_file", {"path": "a.py", "start_line": 1, "end_line": 10}, ok)
    assert (
        guard.run("read_file", {"path": "a.py", "start_line": 11, "end_line": 20}, ok)
        == "result"
    )
    assert guard.run(
        "read_file", {"path": "./a.py", "start_line": 5, "end_line": 6}, ok
    ).startswith("You already")
    with pytest.raises(LoopDetected):
        guard.run("read_file", {"path": "a.py", "start_line": 1, "end_line": 2}, ok)


def test_loop_guard_other_tools_need_identical_args() -> None:
    guard = LoopGuard()
    ok = lambda: "r"
    guard.run("grep", {"pattern": "x", "path": "."}, ok)
    assert guard.run("grep", {"pattern": "y", "path": "."}, ok) == "r"
    assert guard.run("grep", {"path": ".", "pattern": "x"}, ok).startswith(
        "You already"
    )


def test_config_created_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "plumb" / "config.toml"
    config, created = load_config(path)
    assert created and path.read_text() == DEFAULT_CONFIG
    assert config == Config(
        "gemma4:e4b", "http://localhost:11434/v1", "gemma4:31b-cloud", 12
    )
    assert load_config(path)[1] is False


def test_config_missing_keys_take_defaults_and_empty_fallback_disables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[model]\nname = "gemma4:e2b"\nfallback_name = ""\n')
    config, _ = load_config(path)
    assert config == Config("gemma4:e2b", "http://localhost:11434/v1", None, 12)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[limits]\nstep_limit = 0\n", "at least 1"),
        ('[limits]\nstep_limit = "many"\n', "whole number"),
        ('[model]\nname = ""\n', "[model] name must be set"),
        ("not toml [", "cannot be read"),
    ],
)
def test_config_errors(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.raises(
        ConfigError, match=message.replace("[", r"\[").replace("]", r"\]")
    ):
        load_config(path)


async def test_text_before_a_tool_call_is_not_a_retry(toolbox: Toolbox) -> None:
    good = {"question": "Why a class?", "options": ["Testing", "State"]}
    read = Calls([("read_file", {"path": "app/db.py"})], say="Let me read it.")
    events = await run(
        PydanticAIDriver(CONFIG, toolbox, model=scripted(read, json.dumps(good))),
        output_type=Options,
    )
    assert kinds(events) == ["ToolCall", "ToolResult", "TurnDone"]


async def test_fallback_sticks_for_the_rest_of_the_session(toolbox: Toolbox) -> None:
    config = Config("local", "http://unused/v1", "cloud-model", step_limit=4)
    local_calls = {"n": 0}
    failing = scripted(ModelAPIError("local", "model not found"))
    original = failing.stream_function

    async def counting(messages, info):  # type: ignore[no-untyped-def]
        local_calls["n"] += 1
        async for chunk in original(messages, info):  # type: ignore[misc]
            yield chunk

    failing.stream_function = counting  # type: ignore[assignment]
    driver = PydanticAIDriver(
        config, toolbox, model=failing, fallback_model=scripted("first", "second")
    )
    first = await run(driver)
    second = await run(driver)
    assert kinds(first) == ["FallbackUsed", "TextDelta", "TextDelta", "TurnDone"]
    assert kinds(second) == ["TextDelta", "TextDelta", "TurnDone"]  # No second notice.
    assert local_calls["n"] == 1  # The local model is not retried.
