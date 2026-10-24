"""The agent sandbox (ADR 0001, 7.5 and 7.6): what the Claude Code subprocess may touch.

Layers, from the outside in:
1. The pod (agent image, Helm): uid 10001, read-only root fs, the workspace on an emptyDir, no service-account
   token, egress only to the Anthropic API and the platform's internal services (NetworkPolicy).
2. The subprocess environment: `child_env` keeps an allowlist (PATH, HOME, the Anthropic key, Claude Code
   switches) and drops everything else the worker holds (database, S3, master key, Temporal). The SDK merges
   its options over the parent's environment, so the CLI is started through `launch` (cli_path), which applies
   the allowlist before it execs the real binary. The worker process is made non-dumpable, so the subprocess
   (same uid) cannot read `/proc/<worker>/environ`.
3. The tool set: built-in tools are only Read, Write, Edit, Glob, Grep and WebSearch (`tools=`); Bash,
   WebFetch, NotebookEdit, Task/Agent, Skill and the rest are not in the session at all, and are also listed
   in `disallowed_tools`. Every engine action is an MCP tool of server `hwflow` (hwplatform.agent.tools).
4. Paths: a PreToolUse hook (runs for every tool call, a deny there is final) and the `can_use_tool` callback
   both reject any path that leaves the workspace after `realpath`, and writes to the read-only reference
   copy.
5. No settings or CLAUDE.md from the workspace are loaded (`setting_sources=[]`), and `.claude/`, `.mcp.json`
   never reach the workspace (hwplatform.agent.workspace), so an uploaded design cannot add hooks or MCP
   servers.
"""

from __future__ import annotations

import ctypes
import os
import re
from pathlib import Path

BUILTIN_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "WebSearch")
DENIED_TOOLS = (
    "Bash",
    "BashOutput",
    "KillShell",
    "KillBash",
    "WebFetch",
    "NotebookEdit",
    "NotebookRead",
    "Task",
    "Agent",
    "Skill",
    "SlashCommand",
    "AskUserQuestion",
    "ExitPlanMode",
    "Monitor",
    "ScheduleWakeup",
    "SendMessage",
    "CronCreate",
    "RemoteTrigger",
    "PushNotification",
    "EnterWorktree",
    "ExitWorktree",
)
WRITE_TOOLS = ("Write", "Edit", "MultiEdit")
REF_DIR = ".hwflow-ref"  # read-only reference copies (docs, fw framework), never saved
MCP_PREFIX = "mcp__hwflow__"

# The subprocess environment: these names, and names with these prefixes. Everything else is dropped.
ENV_KEEP = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TZ",
        "TMPDIR",
        "PWD",
        "USER",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CONFIG_DIR",
        "HTTPS_PROXY",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_EXTRA_CA_CERTS",
    }
)
ENV_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_", "DISABLE_", "MAX_THINKING_TOKENS", "MCP_")
# Claude Code switches for a headless, offline-by-default session (no auto-update, telemetry or error
# reports).
CLI_SWITCHES = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "400000",  # as the local harness (hwharness.agent)
}
REAL_CLI_ENV = "HWPLATFORM_AGENT_REAL_CLI"


def child_env(environ: dict[str, str]) -> dict[str, str]:
    """The allowlisted environment of the Claude Code subprocess."""
    return {k: v for k, v in environ.items() if k in ENV_KEEP or k.startswith(ENV_PREFIXES)}


def make_non_dumpable() -> bool:
    """prctl(PR_SET_DUMPABLE, 0) on Linux: /proc/<pid>/environ and /proc/<pid>/mem of this process become
    root-owned, so a child running as the same uid cannot read the worker's credentials. False elsewhere."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(4, 0, 0, 0, 0) == 0  # PR_SET_DUMPABLE = 4
    except (OSError, AttributeError):
        return False


def write_launcher(dest_dir: Path, python: str, real_cli: str) -> Path:
    """The `cli_path` the SDK runs: a shell stub that execs `python -I -m hwplatform.agent.launch`, which
    drops the environment to the allowlist and execs the real CLI. The stub holds no secret."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = dest_dir / "claude"
    q = lambda s: "'" + s.replace("'", "'\"'\"'") + "'"  # noqa: E731
    p.write_text(
        f'#!/bin/sh\n{REAL_CLI_ENV}={q(real_cli)} exec {q(python)} -I -m hwplatform.agent.launch "$@"\n'
    )
    p.chmod(0o500)
    return p


def bundled_cli() -> str | None:
    """The Claude Code binary bundled with claude-agent-sdk (None without the SDK)."""
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    p = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    return str(p) if p.is_file() else None


_GLOB_META = re.compile(r"[*?\[{]")


def _static_prefix(pattern: str) -> str:
    """The directory part of a glob before its first wildcard ("/etc/**/x" -> "/etc")."""
    parts = []
    for seg in pattern.split("/"):
        if _GLOB_META.search(seg):
            break
        parts.append(seg)
    return "/".join(parts) or "."


class PathGuard:
    """Decides every tool call of one session against its workspace `ws`."""

    def __init__(self, ws: Path):
        self.ws = Path(os.path.realpath(ws))
        self.ref = self.ws / REF_DIR
        self.denied: list[dict] = []

    def _inside(self, raw: str, base: Path | None = None) -> Path | None:
        p = Path(raw)
        p = p if p.is_absolute() else (base or self.ws) / p
        real = Path(os.path.realpath(p))
        return real if real == self.ws or self.ws in real.parents else None

    def check(self, tool: str, inp: dict) -> str | None:
        """None when the call may run; else the reason it may not."""
        if tool.startswith(MCP_PREFIX):
            return None
        if tool not in BUILTIN_TOOLS and tool != "MultiEdit":
            return f"tool {tool} is not available in this environment"
        if tool == "WebSearch":
            return None
        paths: list[str] = []
        for k in ("file_path", "path", "notebook_path"):
            v = inp.get(k)
            if isinstance(v, str) and v:
                paths.append(v)
        # path-like globs: Glob's `pattern`, Grep's `glob` (Grep's `pattern` is a regex, not a path)
        globs = {"Glob": ("pattern",), "Grep": ("glob",)}.get(tool, ())
        for k in globs:
            v = inp.get(k)
            if isinstance(v, str) and v and (v.startswith(("/", "~")) or ".." in v):
                paths.append(_static_prefix(v))
        base = (
            self._inside(inp["path"])
            if tool in ("Glob", "Grep") and isinstance(inp.get("path"), str)
            else None
        )
        for raw in paths:
            if raw.startswith("~"):
                return f"path {raw!r} is outside the project folder"
            real = self._inside(raw, base)
            if real is None:
                return f"path {raw!r} is outside the project folder"
            if tool in WRITE_TOOLS and (real == self.ref or self.ref in real.parents):
                return f"{REF_DIR}/ is read-only"
        return None

    def _deny(self, tool: str, reason: str) -> None:
        self.denied.append({"tool": tool, "reason": reason[:300]})

    async def pre_tool_use(self, input_data: dict, tool_use_id: str | None, context) -> dict:
        """PreToolUse hook: deny (final) or no opinion (the permission rules and can_use_tool decide next)."""
        tool = str(input_data.get("tool_name") or "")
        reason = self.check(tool, dict(input_data.get("tool_input") or {}))
        if reason is None:
            return {}
        self._deny(tool, reason)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    async def can_use_tool(self, tool: str, inp: dict, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        reason = self.check(tool, inp)
        if reason is None:
            return PermissionResultAllow()
        self._deny(tool, reason)
        return PermissionResultDeny(message=reason)
