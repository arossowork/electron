"""Real Claude API smoke of the sandboxed SDK session (#15). Collected only with HWPLATFORM_AGENT_SMOKE=1 and
ANTHROPIC_API_KEY set (conftest.py `collect_ignore`), so CI and the default suite never call the API:

    HWPLATFORM_AGENT_SMOKE=1 ANTHROPIC_API_KEY=sk-ant-... \
        uv run --package hwplatform pytest -q services/platform/tests/test_agent_smoke.py

One short Haiku session (a few cents) through `SdkRunner` with the real sandbox and MCP server and a fake platform
backend: it writes a file in the workspace, logs a decision, is refused a read outside the workspace and a shell
command, reports usage, and a second session resumes the first by its id.
"""

from __future__ import annotations

import asyncio
import os

from hwplatform.agent.runner import Hooks, SdkRunner, SessionSpec
from hwplatform.agent.tools import ToolBox

MODEL = os.environ.get("HWPLATFORM_AGENT_SMOKE_MODEL", "claude-haiku-4-5-20251001")


class NoBackend:
    async def build(self, ws, options):
        raise AssertionError("the smoke does not build")

    async def engine(self, tool, args, ws, status_json):
        raise AssertionError("the smoke does not call the engine")

    async def cancel_builds(self, ids):
        return None


def test_real_session_is_sandboxed_and_resumable(tmp_path):
    ws, home = tmp_path / "ws", tmp_path / "home"
    for d in (ws, home / ".claude", tmp_path / "bin", tmp_path / "tmp"):
        d.mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("outside")

    def spec(prompt: str, resume: str | None = None) -> SessionSpec:
        return SessionSpec(
            ws=ws, home=home, config_dir=home / ".claude", bin_dir=tmp_path / "bin", tmp_dir=tmp_path / "tmp",
            system="You are a test session. Follow the user's steps exactly and briefly.", model=MODEL,
            prompt=prompt, api_key=os.environ["ANTHROPIC_API_KEY"], max_turns=8, resume=resume,
        )  # fmt: skip

    usage: list[tuple[str, dict]] = []

    async def on_usage(model, u):
        usage.append((model, u))

    box = ToolBox(ws, NoBackend(), stage="requirements")
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
