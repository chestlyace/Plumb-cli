import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelAPIError

from plumb.cli.text_frontend import TextFrontEnd, plain_chat
from plumb.config import Config
from plumb.engine.chat import ChatSession
from plumb.engine.frontend import Info, Notice, Summary
from plumb.engine.pydantic_driver import PydanticAIDriver
from plumb.engine.schemas import Answer
from plumb.memory.decisions import load_decisions
from plumb.memory.mastery import load_mastery
from plumb.memory.sessions import read_session
from plumb.memory.skipped import load_skipped
from plumb.repomap.builder import build_repo_map
from plumb.tools.toolbox import Toolbox
from tests.fake_model import scripted
from tests.test_plan import QUESTIONS, FakeFrontEnd

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
CHANGE = json.dumps({"kind": "change"})
QUESTION = json.dumps({"kind": "question"})
ANSWER = "Db wraps every query so tests can fake it [app/db.py:1-3] (inferred)."


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/db.py").write_text(
        "class Db:\n    def query(self):\n        pass\n"
    )
    (tmp_path / "app/main.py").write_text("from app.db import Db\n")
    return tmp_path


def chat(
    repo: Path, model: object, frontend: object, fallback: object = None
) -> ChatSession:
    tutor_dir = repo / ".tutor"
    toolbox = Toolbox(repo, tutor_dir, build_repo_map(repo, tutor_dir, NOW))
    config = Config(
        "local", "http://unused/v1", "cloud" if fallback else None, step_limit=6
    )
    driver = PydanticAIDriver(config, toolbox, model=model, fallback_model=fallback)  # type: ignore[arg-type]
    return ChatSession(driver, toolbox, tutor_dir, frontend, lambda: NOW)  # type: ignore[arg-type]


def infos(frontend: FakeFrontEnd) -> list[str]:
    return [e.text for e in frontend.events if isinstance(e, Info)]


async def test_a_change_runs_the_plan_flow(repo: Path) -> None:
    frontend = FakeFrontEnd(Answer("option", 1), Answer("skip"))
    model = scripted(CHANGE, ANSWER, json.dumps(QUESTIONS))
    assert await chat(repo, model, frontend).handle("Add a report endpoint")
    assert [type(a).__name__ for a in frontend.asked] == ["AskQuestion", "AskQuestion"]
    assert "data-access-layer" in load_mastery(repo / ".tutor").concepts


async def test_a_question_gets_an_answer_without_a_quiz(repo: Path) -> None:
    frontend = FakeFrontEnd()
    model = scripted(QUESTION, ANSWER)
    await chat(repo, model, frontend).handle("Why is Db a class?")
    assert frontend.asked == []
    assert isinstance(frontend.events[-1], Summary)
    decisions = load_decisions(repo / ".tutor").decisions
    assert [(d.label, d.command) for d in decisions] == [("inferred", "ask")]
    assert load_mastery(repo / ".tutor").concepts == {}
    assert load_skipped(repo / ".tutor").skipped == []


async def test_slash_commands_force_the_flow(repo: Path) -> None:
    frontend = FakeFrontEnd(Answer("skip_rest"))
    # No classification call: the model's first reply is the explanation.
    session = chat(repo, scripted(ANSWER, json.dumps(QUESTIONS), ANSWER), frontend)
    await session.handle("/plan Why is Db a class?")
    assert len(frontend.asked) == 1
    await session.handle("/ask Add a report endpoint")
    assert len(frontend.asked) == 1  # Still one: /ask never quizzes.


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("/help", "/plan <change>"),
        ("/plan", "Add what you want after `/plan`."),
        ("/frobnicate", "Unknown command `/frobnicate`. Try `/help`."),
        ("/skipped", "Nothing skipped."),
    ],
)
async def test_commands_that_need_no_model(
    repo: Path, message: str, expected: str
) -> None:
    frontend = FakeFrontEnd()
    assert await chat(repo, scripted(ModelAPIError("x", "unused")), frontend).handle(
        message
    )
    assert expected in infos(frontend)[0]


async def test_quit_and_blank_messages(repo: Path) -> None:
    session = chat(repo, scripted("unused"), FakeFrontEnd())
    assert await session.handle("   ") is True
    assert await session.handle("/quit") is False
    assert await session.handle("/exit") is False


async def test_skipped_lists_skipped_questions(repo: Path) -> None:
    frontend = FakeFrontEnd(Answer("skip_rest"))
    session = chat(repo, scripted(ANSWER, json.dumps(QUESTIONS)), frontend)
    await session.handle("/plan Add a report")
    await session.handle("/skipped")
    listing = infos(frontend)[-1]
    assert "data-access-layer: Where should the new query live?" in listing
    assert "imports: How should main reach Db?" in listing


async def test_unclear_classification_answers_as_a_question(repo: Path) -> None:
    frontend = FakeFrontEnd()
    model = scripted('{"kind": "banana"}', '{"kind": "banana"}', ANSWER)
    await chat(repo, model, frontend).handle("hmm")
    assert frontend.asked == []
    assert [d.command for d in load_decisions(repo / ".tutor").decisions] == ["ask"]


async def test_one_transcript_per_chat_with_flows_tagged(repo: Path) -> None:
    frontend = FakeFrontEnd(Answer("skip_rest"))
    session = chat(
        repo,
        scripted(QUESTION, ANSWER, CHANGE, ANSWER, json.dumps(QUESTIONS)),
        frontend,
    )
    await session.handle("Why is Db a class?")
    await session.handle("Add a report")
    files = list((repo / ".tutor/sessions").glob("*.jsonl"))
    assert [f.name for f in files] == ["20261004T120000Z-chat.jsonl"]
    requests = [e for e in read_session(files[0]) if e.type == "request"]
    assert [(e.payload["flow"], e.payload["text"]) for e in requests] == [
        ("ask", "Why is Db a class?"),
        ("plan", "Add a report"),
    ]


async def test_fallback_notice_shows_even_when_classification_hits_it(
    repo: Path,
) -> None:
    frontend = FakeFrontEnd()
    session = chat(
        repo,
        scripted(ModelAPIError("local", "missing")),
        frontend,
        fallback=scripted(QUESTION, ANSWER),
    )
    await session.handle("Why is Db a class?")
    notices = [e for e in frontend.events if isinstance(e, Notice)]
    assert len(notices) == 1 and notices[0].model == "cloud"


async def test_plain_chat_loop(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    replies = iter(["Why is Db a class?", "/skipped", "/quit", "never read"])
    frontend = TextFrontEnd(read_line=lambda _: next(replies))
    session = chat(repo, scripted(QUESTION, ANSWER), frontend)
    await plain_chat(session, frontend, first_message="/help")
    out = capsys.readouterr().out
    assert "> /help" in out and "/plan <change>" in out
    assert "Db wraps every query" in out and "Nothing skipped." in out
    assert next(replies) == "never read"  # Stopped at /quit.


async def test_plain_chat_ends_at_end_of_input(repo: Path) -> None:
    def eof(_: str) -> str:
        raise EOFError

    frontend = TextFrontEnd(read_line=eof)
    await plain_chat(chat(repo, scripted("unused"), frontend), frontend)
