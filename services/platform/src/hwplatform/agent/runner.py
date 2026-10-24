"""One Claude Agent SDK session of a design stage (ADR 0001, 7.1).

`AgentRunner.run(spec, box, hooks)` runs the session to the end of its turn and returns a `SessionResult`: the
session id (for resume), the classified API error if one ended it, and the result text. Usage is reported
through `hooks.on_usage(model, usage)` after every ResultMessage (BYOK accounting, ADR 7.4) and progress
through `hooks.on_message(line)`.

`SdkRunner` is the real one (`ClaudeSDKClient`, sandboxed by hwplatform.agent.sandbox); tests use a scripted
runner that calls the ToolBox handlers directly.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..logredact import redact
from .sandbox import BUILTIN_TOOLS, CLI_SWITCHES, DENIED_TOOLS, PathGuard, bundled_cli, write_launcher
from .tools import ALLOWED, ToolBox, mcp_server

log = logging.getLogger("hwplatform.agent")

# ResultMessage / AssistantMessage outcomes -> what the activity does (ADR 7.4)
KEY_INVALID, KEY_EXHAUSTED, RATE_LIMITED, FAILED = "key_invalid", "key_exhausted", "rate_limited", "failed"
LIMIT_STOPS = ("error_max_turns", "error_max_budget_usd")
_ASSISTANT_ERRORS = {
    "authentication_failed": KEY_INVALID,
    "billing_error": KEY_EXHAUSTED,
    "rate_limit": RATE_LIMITED,
}
_MODEL_USAGE_KEYS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheReadInputTokens": "cache_read_input_tokens",
    "cacheCreationInputTokens": "cache_creation_input_tokens",
}


def classify_status(status: int | None) -> str | None:
    if status in (401, 403):
        return KEY_INVALID
    if status == 402:
        return KEY_EXHAUSTED
    if status in (429, 529):
        return RATE_LIMITED
    return None


@dataclass
class SessionSpec:
    ws: Path
    home: Path
    config_dir: Path
    bin_dir: Path
    tmp_dir: Path
    system: str
    model: str
    prompt: str
    api_key: str = field(repr=False)
    effort: str | None = None
    max_turns: int | None = None
    resume: str | None = None
    max_budget_usd: float | None = None
    fallback_model: str | None = None

    def env(self) -> dict[str, str]:
        """The subprocess environment set on top of the allowlist (the launcher drops everything else)."""
        return {
            **CLI_SWITCHES,
            "HOME": str(self.home),
            "CLAUDE_CONFIG_DIR": str(self.config_dir),
            "TMPDIR": str(self.tmp_dir),
            "ANTHROPIC_API_KEY": self.api_key,
        }


@dataclass
class SessionResult:
    session_id: str | None
    error: str | None = None  # None | KEY_INVALID | KEY_EXHAUSTED | RATE_LIMITED | FAILED
    retry_after_s: float | None = None
    text: str = ""
    num_turns: int = 0
    stop: str | None = None  # the ResultMessage subtype (success, error_max_turns, ...)
    denied: list[dict] = field(default_factory=list)


@dataclass
class Hooks:
    on_usage: Callable[[str, dict], Awaitable[None]]
    on_message: Callable[[str], None] = lambda line: None
    on_session: Callable[[str], None] = lambda sid: None


class AgentRunner(Protocol):
    async def run(self, spec: SessionSpec, box: ToolBox, hooks: Hooks) -> SessionResult: ...


def usage_by_model(result, fallback_model: str) -> list[tuple[str, dict]]:
    """[(model, {input_tokens, output_tokens, cache_*})] of one ResultMessage: per model when the CLI reports
    modelUsage (main session and anything it delegated), else the session total under the session's model."""
    mu = getattr(result, "model_usage", None) or {}
    out = []
    for model, u in sorted(mu.items()):
        if isinstance(u, dict):
            out.append(
                (
                    str(u.get("canonicalModel") or model),
                    {v: int(u.get(k) or 0) for k, v in _MODEL_USAGE_KEYS.items()},
                )
            )
    if not out and getattr(result, "usage", None):
        u = result.usage
        out.append((fallback_model, {v: int(u.get(v) or 0) for v in _MODEL_USAGE_KEYS.values()}))
    return out


class SdkRunner:
    """The Claude Agent SDK session, sandboxed: built-in tools limited to the workspace, the MCP server
    `hwflow` for everything else, no settings from disk, the subprocess started through the env-scrubbing
    launcher."""

    def __init__(self, cli: str | None = None, python: str | None = None):
        self.cli = cli or bundled_cli()
        self.python = python or sys.executable

    def options(self, spec: SessionSpec, box: ToolBox, guard: PathGuard, stderr: Callable[[str], None]):
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

        if not self.cli:
            raise FileNotFoundError("the Claude Code CLI is missing (install claude-agent-sdk)")
        return ClaudeAgentOptions(
            tools=list(BUILTIN_TOOLS),
            disallowed_tools=list(DENIED_TOOLS),
            allowed_tools=list(ALLOWED),
            system_prompt={"type": "preset", "preset": "claude_code", "append": spec.system},
            mcp_servers={"hwflow": mcp_server(box)},
            strict_mcp_config=True,
            setting_sources=[],
            permission_mode="default",
            can_use_tool=guard.can_use_tool,
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[guard.pre_tool_use])]},
            cwd=str(spec.ws),
            cli_path=str(write_launcher(spec.bin_dir, self.python, self.cli)),
            env=spec.env(),
            model=spec.model,
            fallback_model=spec.fallback_model,
            effort=spec.effort,
            max_turns=spec.max_turns,
            max_budget_usd=spec.max_budget_usd,
            resume=spec.resume,
            stderr=stderr,
        )

    async def run(self, spec: SessionSpec, box: ToolBox, hooks: Hooks) -> SessionResult:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeSDKClient,
            ProcessError,
            RateLimitEvent,
            ResultMessage,
            SystemMessage,
            TextBlock,
            ToolUseBlock,
        )

        guard = PathGuard(spec.ws)
        res = SessionResult(session_id=spec.resume)
        tail: list[str] = []

        def stderr(line: str) -> None:
            tail.append(redact(line)[:500])
            del tail[:-20]

        after_ask = 0
        try:
            async with ClaudeSDKClient(options=self.options(spec, box, guard, stderr)) as client:
                await client.query(spec.prompt)
                async for msg in client.receive_response():
                    sid = getattr(msg, "session_id", None) or (
                        (getattr(msg, "data", None) or {}).get("session_id")
                        if isinstance(msg, SystemMessage)
                        else None
                    )
                    if sid and sid != res.session_id:
                        res.session_id = sid
                        hooks.on_session(sid)
                    if isinstance(msg, AssistantMessage):
                        if msg.error and res.error is None:
                            res.error = _ASSISTANT_ERRORS.get(msg.error, None)
                        for b in msg.content:
                            if isinstance(b, ToolUseBlock):
                                hooks.on_message(f"{b.name}")
                            elif isinstance(b, TextBlock) and b.text.strip():
                                hooks.on_message(b.text.strip().splitlines()[0][:200])
                        if box.ended:  # asked the product person: the turn must end now
                            after_ask += 1
                            if after_ask > 3:
                                await client.interrupt()
                    elif isinstance(msg, RateLimitEvent):
                        info = msg.rate_limit_info
                        if info.status == "rejected" and info.resets_at:
                            import time

                            res.retry_after_s = max(1.0, float(info.resets_at) - time.time())
                    elif isinstance(msg, ResultMessage):
                        for model, usage in usage_by_model(msg, spec.model):
                            await hooks.on_usage(model, usage)
                        res.num_turns, res.stop, res.text = (
                            msg.num_turns,
                            msg.subtype,
                            (msg.result or "")[:4000],
                        )
                        if msg.is_error and msg.subtype not in LIMIT_STOPS:
                            res.error = res.error or classify_status(msg.api_error_status) or FAILED
        except ProcessError as e:
            if getattr(e, "subtype", None) in LIMIT_STOPS:  # max turns / budget: the stage ends, not an error
                res.stop = e.subtype
                res.denied = list(guard.denied)
                return res
            status = getattr(e, "api_error_status", None)
            res.error = res.error or classify_status(status) or FAILED
            res.text = res.text or redact(f"{type(e).__name__}: {e}")[:2000]
            log.warning("agent session ended: %s (stderr tail: %s)", res.error, " | ".join(tail[-5:]))
        if box.ended and res.error == FAILED and res.stop is None:
            res.error = None  # interrupted on purpose after the questions
        res.denied = list(guard.denied)
        return res
