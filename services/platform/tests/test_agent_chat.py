"""Chat turn boundaries of the real runner (#54): `SdkRunner.run` against a scripted ClaudeSDKClient (no CLI, no API).

The session takes the person's chat before the first prompt and after every turn (a turn ends at its
ResultMessage); the agent's reply to a turn that carried chat goes to `Hooks.on_reply`."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")
import claude_agent_sdk  # noqa: E402
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock  # noqa: E402

from hwplatform.agent.runner import Hooks, SdkRunner, SessionSpec  # noqa: E402


class ScriptedClient:
    """Stands in for ClaudeSDKClient: one scripted turn (a list of reply texts) per query."""

    instances: list[ScriptedClient] = []
    turns: list[str] = []

    def __init__(self, options=None):
        self.queries: list[str] = []
        ScriptedClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        self.queries.append(prompt)

    async def interrupt(self):
        return None

    async def receive_response(self):
        n = len(self.queries)
        text = self.turns[min(n, len(self.turns)) - 1]
        yield AssistantMessage(content=[TextBlock(text=text)], model="m")
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=n,
            session_id="sess-1",
            result=text,
            usage={"input_tokens": 1, "output_tokens": 1},
        )


class Box:
    ended = False


def run_session(monkeypatch, tmp_path: Path, turns: list[str], chat_batches: list[list[str]], box=None):
    ScriptedClient.instances, ScriptedClient.turns = [], turns
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", ScriptedClient)
    monkeypatch.setattr(SdkRunner, "options", lambda self, spec, box, guard, stderr: None)
    spec = SessionSpec(
        ws=tmp_path,
        home=tmp_path,
        config_dir=tmp_path,
        bin_dir=tmp_path,
        tmp_dir=tmp_path,
        system="s",
        model="m",
        prompt="Run the stage.",
        api_key="k",
    )
    replies: list[str] = []
    taken = 0
    batches = list(chat_batches)

    async def take_chat() -> list[str]:
        nonlocal taken
        taken += 1
        return batches.pop(0) if batches else []

    async def on_usage(model, usage):
        return None

    async def on_reply(text: str) -> None:
        replies.append(text)

    hooks = Hooks(on_usage, take_chat=take_chat, on_reply=on_reply)
    res = asyncio.run(SdkRunner(cli="x").run(spec, box or Box(), hooks))
    return res, ScriptedClient.instances[0].queries, replies, taken


def test_no_chat_is_one_turn_and_no_reply(monkeypatch, tmp_path):
    res, queries, replies, taken = run_session(monkeypatch, tmp_path, ["stage done"], [])
    assert queries == ["Run the stage."] and replies == [] and taken == 2 and res.text == "stage done"


def test_chat_waiting_at_the_start_rides_the_first_prompt(monkeypatch, tmp_path):
    res, queries, replies, _ = run_session(monkeypatch, tmp_path, ["I made it 5 V."], [["use 5 V please"]])
    assert len(queries) == 1 and queries[0].startswith(
        "Run the stage.\n\nChat message from the product person"
    )
    assert "> use 5 V please" in queries[0]
    assert replies == ["I made it 5 V."]


def test_chat_that_arrives_while_the_agent_works_is_delivered_at_the_turn_boundary(monkeypatch, tmp_path):
    # nothing at the start; one message by the end of turn 1, two by the end of turn 2; then quiet
    res, queries, replies, _ = run_session(
        monkeypatch,
        tmp_path,
        ["stage done", "ok, LED added", "ok, both"],
        [[], ["add a status LED"], ["and a reset button", "and a fuse"], []],
    )
    assert len(queries) == 3 and queries[0] == "Run the stage."
    assert queries[1].endswith("> add a status LED") and "Chat message" in queries[1]
    assert queries[2].splitlines()[-2:] == ["> and a reset button", "> and a fuse"]
    assert replies == ["ok, LED added", "ok, both"]  # the first turn carried no chat: no reply
    assert res.text == "ok, both" and res.error is None


def test_chat_waits_while_the_session_asked_the_person(monkeypatch, tmp_path):
    box = Box()
    box.ended = True  # ask_product_person ended the turn: the session ends, the chat stays queued
    res, queries, replies, taken = run_session(monkeypatch, tmp_path, ["asked"], [[], ["still there?"]], box)
    assert len(queries) == 1 and replies == [] and taken == 1
