"""The agent sandbox (ADR 0001, 7.5): the subprocess environment, the launcher, the path guard and the SDK
options. No API call, no database."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hwplatform.agent.runner import (
    FAILED,
    KEY_EXHAUSTED,
    KEY_INVALID,
    RATE_LIMITED,
    SdkRunner,
    SessionSpec,
    classify_status,
    usage_by_model,
)
from hwplatform.agent.sandbox import (
    BUILTIN_TOOLS,
    DENIED_TOOLS,
    REF_DIR,
    PathGuard,
    bundled_cli,
    child_env,
    write_launcher,
)
from hwplatform.agent.tools import ALLOWED, ToolBox

SECRETS = {
    "HWPLATFORM_DATABASE_URL": "postgresql+asyncpg://u:dbpass@db/x",
    "HWPLATFORM_MASTER_KEY": "bWFzdGVy",
    "HWPLATFORM_S3_SECRET_KEY": "s3secret",
    "AWS_SECRET_ACCESS_KEY": "aws",
    "TEMPORAL_TLS_KEY": "tls",
    "PYTHONPATH": "/evil",
    "LD_PRELOAD": "/evil.so",
}


def test_child_env_keeps_the_allowlist_only():
    env = child_env(
        {
            **SECRETS,
            "PATH": "/usr/bin",
            "HOME": "/h",
            "ANTHROPIC_API_KEY": "sk-ant-x",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
        }
    )
    assert set(env) == {
        "PATH",
        "HOME",
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "DISABLE_TELEMETRY",
    }


def test_launcher_scrubs_the_environment_and_holds_no_secret(tmp_path):
    """The SDK merges os.environ into the CLI's environment; the launcher drops it to the allowlist."""
    launcher = write_launcher(tmp_path / "bin", sys.executable, "/usr/bin/env")
    text = launcher.read_text()
    assert "sk-ant" not in text and "/usr/bin/env" in text and oct(launcher.stat().st_mode)[-3:] == "500"
    out = subprocess.run(
        [str(launcher)],
        env={**os.environ, **SECRETS, "ANTHROPIC_API_KEY": "sk-ant-test", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    seen = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    assert seen["ANTHROPIC_API_KEY"] == "sk-ant-test"
    for k in SECRETS:
        assert k not in seen, k
    assert "HWPLATFORM_AGENT_REAL_CLI" not in seen
    assert not any(v in out for v in SECRETS.values())


def test_session_spec_never_shows_the_key(tmp_path):
    spec = SessionSpec(
        ws=tmp_path, home=tmp_path, config_dir=tmp_path, bin_dir=tmp_path, tmp_dir=tmp_path,
        system="s", model="m", prompt="p", api_key="sk-ant-secret-123",
    )  # fmt: skip
    assert "sk-ant-secret-123" not in repr(spec) and spec.env()["ANTHROPIC_API_KEY"] == "sk-ant-secret-123"
    assert spec.env()["CLAUDE_CONFIG_DIR"] == str(tmp_path)


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    (w / "notes").mkdir(parents=True)
    (w / "design.yaml").write_text("a: 1\n")
    (w / REF_DIR / "docs").mkdir(parents=True)
    (w / REF_DIR / "docs" / "design-yaml.md").write_text("ref")
    (tmp_path / "outside.txt").write_text("secret")
    (w / "link").symlink_to(tmp_path / "outside.txt")
    (w / "dirlink").symlink_to(tmp_path)
    return w


ALLOW = [
    ("Read", {"file_path": "design.yaml"}),
    ("Read", {"file_path": "{ws}/design.yaml"}),
    ("Write", {"file_path": "notes/new.md", "content": "x"}),
    ("Edit", {"file_path": "{ws}/notes/../design.yaml"}),
    ("Read", {"file_path": f"{REF_DIR}/docs/design-yaml.md"}),
    ("Glob", {"pattern": "**/*.yaml"}),
    ("Grep", {"pattern": "R1", "path": "notes"}),
    ("Grep", {"pattern": "a..b"}),  # a regex, not a path: Grep's pattern is only a path with path chars
    ("WebSearch", {"query": "TPS62840 datasheet"}),
    ("mcp__hwflow__build", {"until": "sim"}),
]
DENY = [
    ("Read", {"file_path": "/etc/passwd"}),
    ("Read", {"file_path": "../outside.txt"}),
    ("Read", {"file_path": "link"}),  # a symlink out of the workspace
    ("Read", {"file_path": "dirlink/outside.txt"}),
    ("Read", {"file_path": "~/.claude/.credentials.json"}),
    ("Read", {"file_path": "/proc/self/environ"}),
    ("Write", {"file_path": f"{REF_DIR}/docs/design-yaml.md", "content": "x"}),
    ("Edit", {"file_path": f"{REF_DIR}/x"}),
    ("Glob", {"pattern": "/etc/*"}),
    ("Glob", {"pattern": "../**/*.py"}),
    ("Glob", {"pattern": "*.py", "path": "/"}),
    ("Grep", {"pattern": "x", "path": "/home"}),
    ("Grep", {"pattern": "x", "glob": "~/*"}),
    ("Bash", {"command": "env"}),
    ("WebFetch", {"url": "http://169.254.169.254/"}),
    ("Task", {"prompt": "x"}),
    ("NotebookEdit", {"notebook_path": "a.ipynb"}),
]


@pytest.mark.parametrize(("tool", "inp"), ALLOW)
def test_guard_allows_workspace_calls(ws, tool, inp):
    inp = {k: v.format(ws=ws) if isinstance(v, str) else v for k, v in inp.items()}
    assert PathGuard(ws).check(tool, inp) is None


@pytest.mark.parametrize(("tool", "inp"), DENY)
def test_guard_denies_everything_else(ws, tool, inp):
    g = PathGuard(ws)
    assert g.check(tool, inp) is not None
    out = asyncio.run(g.pre_tool_use({"tool_name": tool, "tool_input": inp}, "t1", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    res = asyncio.run(g.can_use_tool(tool, inp, None))
    assert type(res).__name__ == "PermissionResultDeny"
    assert len(g.denied) == 2 and g.denied[0]["tool"] == tool


def test_guard_hook_has_no_opinion_on_allowed_calls(ws):
    g = PathGuard(ws)
    assert (
        asyncio.run(g.pre_tool_use({"tool_name": "Read", "tool_input": {"file_path": "a"}}, "t", None)) == {}
    )
    assert (
        type(asyncio.run(g.can_use_tool("Read", {"file_path": "a"}, None))).__name__
        == "PermissionResultAllow"
    )


def test_tool_lists_do_not_overlap():
    assert not set(BUILTIN_TOOLS) & set(DENIED_TOOLS)
    assert "Bash" in DENIED_TOOLS and "WebFetch" in DENIED_TOOLS and "Task" in DENIED_TOOLS
    assert all(t.startswith("mcp__hwflow__") for t in ALLOWED)


class _NoBackend:
    pass


def test_sdk_options_are_sandboxed(tmp_path):
    cli = bundled_cli()
    assert cli and Path(cli).is_file(), "claude-agent-sdk ships the CLI binary"
    spec = SessionSpec(
        ws=tmp_path / "ws", home=tmp_path / "h", config_dir=tmp_path / "h/.claude", bin_dir=tmp_path / "bin",
        tmp_dir=tmp_path / "tmp", system="SYS", model="claude-sonnet-5-5", prompt="p", api_key="sk-ant-k",
        max_turns=10, effort="high",
    )  # fmt: skip
    (tmp_path / "ws").mkdir()
    box = ToolBox(tmp_path / "ws", _NoBackend(), stage="circuit")
    o = SdkRunner(cli=cli).options(spec, box, PathGuard(spec.ws), lambda s: None)
    assert o.tools == list(BUILTIN_TOOLS) and "Bash" in o.disallowed_tools
    assert o.setting_sources == [] and o.strict_mcp_config and list(o.mcp_servers) == ["hwflow"]
    assert o.system_prompt == {"type": "preset", "preset": "claude_code", "append": "SYS"}
    assert o.cwd == str(spec.ws) and o.model == "claude-sonnet-5-5" and o.max_turns == 10
    assert Path(o.cli_path).parent == spec.bin_dir and "sk-ant-k" not in Path(o.cli_path).read_text()
    assert o.env["ANTHROPIC_API_KEY"] == "sk-ant-k" and o.env["DISABLE_AUTOUPDATER"] == "1"
    assert o.can_use_tool is not None and o.hooks["PreToolUse"]
    assert o.permission_mode == "default"


def test_classify_and_usage():
    assert classify_status(401) == classify_status(403) == KEY_INVALID
    assert classify_status(402) == KEY_EXHAUSTED
    assert classify_status(429) == classify_status(529) == RATE_LIMITED
    assert classify_status(500) is None and classify_status(None) is None and FAILED == "failed"
    r = SimpleNamespace(
        model_usage={
            "claude-sonnet-5-5": {"inputTokens": 10, "outputTokens": 5, "cacheReadInputTokens": 100},
            "claude-haiku-4-5-20251001": {"inputTokens": 1, "outputTokens": 1},
        },
        usage={"input_tokens": 999},
    )
    got = dict(usage_by_model(r, "x"))
    assert got["claude-sonnet-5-5"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 0,
    }
    assert set(got) == {"claude-sonnet-5-5", "claude-haiku-4-5-20251001"}
    only = usage_by_model(
        SimpleNamespace(model_usage=None, usage={"input_tokens": 3, "output_tokens": 4}), "m"
    )
    assert only == [
        (
            "m",
            {
                "input_tokens": 3,
                "output_tokens": 4,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        )
    ]


def test_write_launcher_twice_in_one_sandbox(tmp_path):
    """A resumed stage writes the launcher again into the same read-only stub; it must replace it."""
    first = write_launcher(tmp_path / "bin", sys.executable, "/opt/claude-a")
    second = write_launcher(tmp_path / "bin", sys.executable, "/opt/claude-b")
    assert first == second
    assert "/opt/claude-b" in second.read_text()
    assert oct(second.stat().st_mode & 0o777) == "0o500"
    assert [p.name for p in (tmp_path / "bin").iterdir()] == ["claude"]
