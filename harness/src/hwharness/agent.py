"""`claude -p` sessions: the flags, the environment, the usage accounting and the wait for the subscription window.

Shared by evals/harness.py, ui/server.py and scripts/hw-run.sh, so every headless call uses the same lean context."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

CALL_TIMEOUT = int(os.environ.get("HWFLOW_CALL_TIMEOUT", str(6 * 3600)))  # one designer turn can run hours

# Every headless call (this harness, ui/server.py, scripts/hw-run.sh) uses the same lean context. Project
# settings only: no user plugins, skills, agents or hooks (about 9k tokens instead of 18.5k on every request).
# No MCP servers, a fixed tool set, and auto-compact at 400k tokens instead of the default window of about 1M.
# Keep these flags identical for every --resume of a session, or the prompt cache misses.
AUTOCOMPACT_WINDOW = os.environ.get("HWFLOW_AUTOCOMPACT_WINDOW", "400000")
DESIGNER_TOOLS = "Bash,Read,Write,Edit,Agent,Skill,WebSearch,WebFetch"
LEAN_ARGS = ["--setting-sources", "project,local", "--strict-mcp-config"]
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def headless_env(headless: bool = True) -> dict:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # always use the subscription login
    for k in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_CHILD_SESSION",
              "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_MESSAGING_SOCKET",
              "CLAUDE_CODE_MESSAGING_TOKEN"):
        env.pop(k, None)  # launched from a Claude Code session: its effort would override the agents' `effort:`
    env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = AUTOCOMPACT_WINDOW  # takes precedence over every settings file
    # Subagents default to a 5 min prompt cache. A hwflow build (routing, Monte Carlo) often idles longer, so the
    # next turn would write the whole subagent context again. 1h keeps it cached across a build.
    env.setdefault("CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL", "1h")
    if headless:
        env["HWFLOW_HEADLESS"] = "1"
    return env


def designer_args(model: str) -> list[str]:
    """Flags of a designer call after `claude -p <prompt>`; HWFLOW_EFFORT (low..max) sets the session effort."""
    args = ["--model", model, *LEAN_ARGS, "--tools", DESIGNER_TOOLS, "--permission-mode", "acceptEdits"]
    if os.environ.get("HWFLOW_EFFORT"):
        args += ["--effort", os.environ["HWFLOW_EFFORT"]]
    return args


from .limits import LIMIT_RE, limit_wait_seconds  # noqa: E402 - shared with the platform's credential adapter


def _limit_wait_seconds(text: str) -> int:
    """Seconds until the subscription window resets, from the CLI message; 15 min when unknown."""
    return limit_wait_seconds(text)


def claude(prompt: str, model: str, cwd: Path, resume: str | None = None, system: str | None = None,
           headless: bool = True, timeout: int | None = None, log: Path | None = None) -> dict:
    """claude -p with automatic waiting when the subscription usage window is exhausted."""
    for _ in range(48):
        out = _claude_once(prompt, model, cwd, resume, system, headless, timeout or CALL_TIMEOUT, log)
        text = str(out.get("result", ""))
        if out.get("is_error") and LIMIT_RE.search(text) and "TIMEOUT" not in text:
            wait = _limit_wait_seconds(text)
            print(f"{time.strftime('%H:%M')} usage limit: waiting {wait // 60} min for the window to refresh",
                  flush=True)
            if log:
                with open(log, "a") as fh:
                    fh.write(json.dumps({"event": "limit_wait", "seconds": wait, "msg": text[:300]}) + "\n")
            time.sleep(wait)
            resume = out.get("session_id") or resume
            continue
        return out
    return out


def _claude_once(prompt: str, model: str, cwd: Path, resume: str | None = None, system: str | None = None,
           headless: bool = True, timeout: int = CALL_TIMEOUT, log: Path | None = None) -> dict:
    if system:  # the product person: no tools, no project context
        cmd = ["claude", "-p", prompt, "--model", model, *LEAN_ARGS, "--system-prompt", system, "--tools", "",
               "--effort", os.environ.get("HWFLOW_PM_EFFORT", "low")]
    else:
        cmd = ["claude", "-p", prompt, *designer_args(model)]
    cmd += ["--output-format", "json"]
    if resume:
        cmd += ["--resume", resume]
    env = headless_env(headless)
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        try:
            out = json.loads(p.stdout)
        except json.JSONDecodeError:
            out = {"is_error": True, "result": p.stdout[-2000:] + p.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        out = {"is_error": True, "result": f"TIMEOUT after {timeout}s", "session_id": resume}
    out["_seconds"] = round(time.time() - t0, 1)
    if log:
        with open(log, "a") as fh:
            fh.write(json.dumps({"prompt": prompt[:500], "model": model, "result": str(out.get("result"))[:4000],
                                 "cost": out.get("total_cost_usd"), "turns": out.get("num_turns"),
                                 "usage": _usage(out), "seconds": out["_seconds"]}) + "\n")
    return out


def _usage(out: dict) -> dict:
    u = out.get("usage") or {}
    return {k: u.get(k) or 0 for k in USAGE_KEYS}


MODEL_KEYS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")


def model_usage_delta(prev: dict | None, cur: dict) -> dict:
    """`modelUsage` of a resumed session is cumulative (checked against the session transcripts: the second turn
    reported the running total of both turns, subagents included). Return this turn's share."""
    cur = {(u.get("canonicalModel") or m): {k: u.get(k) or 0 for k in MODEL_KEYS} for m, u in (cur or {}).items()}
    if not prev:
        return cur
    out = {m: {k: v[k] - (prev.get(m) or {}).get(k, 0) for k in MODEL_KEYS} for m, v in cur.items()}
    if any(x < 0 for v in out.values() for x in v.values()):  # not cumulative after all (new session): use as is
        return cur
    return out


def _add_usage(meta: dict, out: dict, chain: str | None = None):
    """Token totals of a trial; on a subscription these, not cost_usd, are what the usage window counts.
    `chain` names a resumed session (its modelUsage is cumulative); None for one-shot calls."""
    tok = meta.setdefault("tokens", dict.fromkeys(USAGE_KEYS, 0))
    for k, v in _usage(out).items():
        tok[k] = tok.get(k, 0) + v
    by_model = meta.setdefault("tokens_by_model", {})  # main session and subagents, per model
    prev = meta.setdefault("_model_usage_seen", {}).get(chain) if chain else None
    cur = model_usage_delta(prev, out.get("modelUsage") or {})
    if chain:
        meta["_model_usage_seen"][chain] = {m: {k: (prev or {}).get(m, {}).get(k, 0) + v[k] for k in MODEL_KEYS}
                                            for m, v in cur.items()} | {m: v for m, v in (prev or {}).items()
                                                                        if m not in cur}
    for m, v in cur.items():
        t = by_model.setdefault(m, {})
        for k in MODEL_KEYS:
            t[k] = t.get(k, 0) + v[k]
