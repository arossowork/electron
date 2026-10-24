"""Real Claude API smoke of the sandboxed SDK session (#15), marker `anthropic_smoke`. Collected only on request
and with a credential in the environment (conftest.py `pytest_ignore_collect`), so CI and the default suite never
call the API. With an API key:

    ANTHROPIC_API_KEY=sk-ant-api... \
        uv run --package hwplatform --extra agent pytest -q -s -m anthropic_smoke services/platform/tests/test_agent_smoke.py

With an operator subscription token (`claude setup-token`; the operator's own use only, see the README), and no
ANTHROPIC_API_KEY in the environment:

    CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat... \
        uv run --package hwplatform --extra agent pytest -q -s -m anthropic_smoke services/platform/tests/test_agent_smoke.py

When both are set the API key is used. One short Haiku session (a few cents on the API) through `SdkRunner` with
the real sandbox and MCP server and a fake platform backend: it writes a file in the workspace, logs a decision
(an MCP tool), is refused a read outside the workspace and a shell command, reports usage, and a second session
resumes the first by its id. It prints the turns, tokens and duration.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from hwplatform.agent.runner import Hooks, SdkRunner, SessionSpec
from hwplatform.agent.tools import ToolBox

pytestmark = pytest.mark.anthropic_smoke
MODEL = os.environ.get("HWPLATFORM_AGENT_SMOKE_MODEL", "claude-haiku-4-5-20251001")


class NoBackend:
    async def build(self, ws, options):
        raise AssertionError("the smoke does not build")

    async def engine(self, tool, args, ws, status_json):
        raise AssertionError("the smoke does not call the engine")

    async def cancel_builds(self, ids):
        return None


def credential() -> dict:
    """The session's one credential, from this process's environment (never printed)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {"api_key": os.environ["ANTHROPIC_API_KEY"]}
    return {"oauth_token": os.environ["CLAUDE_CODE_OAUTH_TOKEN"]}


def test_real_session_is_sandboxed_and_resumable(tmp_path):
    ws, home = tmp_path / "ws", tmp_path / "home"
    for d in (ws, home / ".claude", tmp_path / "bin", tmp_path / "tmp"):
        d.mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("outside")

    def spec(prompt: str, resume: str | None = None) -> SessionSpec:
        return SessionSpec(
            ws=ws, home=home, config_dir=home / ".claude", bin_dir=tmp_path / "bin", tmp_dir=tmp_path / "tmp",
            system="You are a test session. Follow the user's steps exactly and briefly.", model=MODEL,
            prompt=prompt, max_turns=8, resume=resume, **credential(),
        )  # fmt: skip

    usage: list[tuple[str, dict]] = []

    async def on_usage(model, u):
        usage.append((model, u))

    box = ToolBox(ws, NoBackend(), stage="requirements")
    t0 = time.monotonic()
    res = asyncio.run(
        SdkRunner().run(
            spec(
                "Step 1: write the file notes/hello.md containing the word hello. "
                f"Step 2: read the file {tmp_path / 'secret.txt'}. "
                "Step 3: run the shell command `env` with Bash. "
                "Step 4: call the tool log_decision with text 'smoke ok' and source 'test'. Then stop."
            ),
            box,
            Hooks(on_usage),
        )
    )
    assert res.error is None, res
    assert res.session_id
    assert (ws / "notes" / "hello.md").read_text().strip().lower().startswith("hello")
    assert box.decisions and box.decisions[0]["text"] == "smoke ok"
    assert any("outside the project folder" in d["reason"] for d in res.denied), res.denied
    assert usage and usage[0][1]["output_tokens"] > 0

    box2 = ToolBox(ws, NoBackend(), stage="requirements")
    res2 = asyncio.run(
        SdkRunner().run(
            spec("What word did you write in notes/hello.md? Answer with the word only.", res.session_id),
            box2,
            Hooks(on_usage),
        )
    )
    assert res2.error is None and "hello" in res2.text.lower()
    tokens = {k: sum(int(u.get(k, 0) or 0) for _, u in usage) for k in ("input_tokens", "output_tokens")}
    kind = "api_key" if "api_key" in credential() else "subscription_token"
    print(
        f"\nanthropic_smoke: {kind}, model {MODEL}, turns {res.num_turns}+{res2.num_turns}, "
        f"tokens {tokens}, {time.monotonic() - t0:.1f} s"
    )
