"""agent_stage (ADR 0001, 7.1, 7.4 and 7.6), on the hwflow-agent queue: one Claude Agent SDK session of a
stage.

1. The start point: the input's workspace and session, or, on a retry after a worker crash or a heartbeat
   timeout, the last snapshot this activity heartbeated (the workspace tree and the transcript, saved every
   AGENT_SNAPSHOT_EVERY_S and at the end), so a restarted stage resumes the same session where it was saved.
2. BYOK: the tenant's key from `get_tenant_llm_key`. It goes only into the subprocess environment of the
   session (never into a payload, a heartbeat, an event, a log line or an artifact). No usable key:
   LlmKeyInvalid.
3. The session (hwplatform.agent.runner) in `agent/<run_id>/<stage>/ws` with the MCP tools of
   hwplatform.agent.tools; usage is recorded with `record_usage` after every result.
4. The end: the workspace and transcript snapshot; then the API outcome. Invalid or exhausted key:
   `mark_invalid` and LlmKeyInvalid; rate limit: LlmRateLimited (with retry_after_s); both non-retryable here,
   the workflow waits (a `retry` signal, a backoff timer) and runs the stage again from the snapshot in the
   error details. Otherwise AgentStageOutput: "needs_answers" when the session asked the product person, else
   "done".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from hwflow.artifacts import ArtifactRef, ref_from_dict, ref_to_dict
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..agent import workspace as wsmod
from ..agent.backend import PlatformBackend
from ..agent.runner import KEY_EXHAUSTED, KEY_INVALID, RATE_LIMITED, Hooks, SessionSpec
from ..agent.tools import ToolBox
from ..contracts import (
    A_AGENT_STAGE,
    AGENT_SNAPSHOT_EVERY_S,
    HEARTBEAT_EVERY_S,
    AgentStageInput,
    AgentStageOutput,
    errors,
)
from ..llm_keys import get_tenant_llm_key, mark_invalid, record_usage
from .deps import Deps

log = logging.getLogger("hwplatform.agent")


@dataclass
class Start:
    workspace: ArtifactRef
    session: ArtifactRef | None
    session_id: str | None
    resume: str | None


def snapshot_details(
    workspace: ArtifactRef, session: ArtifactRef | None, session_id: str | None, line: str = ""
):
    """Heartbeat / error details: refs and the session id only (no secret, ADR 4.3)."""
    return {
        "workspace": ref_to_dict(workspace),
        "session": ref_to_dict(session) if session else None,
        "session_id": session_id,
        "line": line[:300],
    }


def start_point(inp: AgentStageInput, details: list) -> Start:
    """Where the session starts: the last heartbeated snapshot of an earlier attempt, else the input."""
    for d in reversed(details or []):
        if isinstance(d, dict) and d.get("workspace"):
            sess = d.get("session")
            return Start(
                ref_from_dict(d["workspace"]),
                ref_from_dict(sess) if sess else None,
                d.get("session_id"),
                "restart",
            )
    return Start(inp.workspace, inp.session, inp.session_id, inp.resume)


def prompt_for(inp: AgentStageInput, start: Start, can_resume: bool) -> str:
    from hwharness.prompts import RETRY_MESSAGE, answers_message, first_message

    if start.resume == "answers" and inp.answers is not None:
        msg = answers_message(inp.questions, inp.answers, inp.round)
        return msg if can_resume else first_message(inp.stage, inp.brief) + "\n\n" + msg
    if start.resume in ("retry", "restart") and can_resume:
        return RETRY_MESSAGE
    if start.resume in ("retry", "restart"):
        return first_message(inp.stage, inp.brief) + "\n\n" + RETRY_MESSAGE
    return first_message(inp.stage, inp.brief)


class AgentActivities:
    def __init__(self, deps: Deps):
        self.deps = deps

    def _prompts_root(self) -> Path:
        from hwharness.prompts import prompts_root

        d = self.deps.settings.agent_prompts_dir
        return Path(d) if d else prompts_root()

    async def _key(self, tenant_id: str, details: dict) -> str:
        from ..crypto import CryptoError

        try:
            key = await get_tenant_llm_key(self.deps.db, self.deps.keyring, uuid.UUID(tenant_id))
        except CryptoError:
            key = None
        if not key:
            raise ApplicationError(
                "the tenant has no usable Anthropic API key",
                {**details, "reason": "no_key"},
                type=errors.LlmKeyInvalid,
                non_retryable=True,
            )
        return key

    @activity.defn(name=A_AGENT_STAGE)
    async def agent_stage(self, inp: AgentStageInput) -> AgentStageOutput:
        from hwharness.prompts import stage_prompt

        ctx, settings = inp.ctx, self.deps.settings
        store = self.deps.store(ctx.tenant_id)
        start = start_point(inp, list(activity.info().heartbeat_details or []))
        details = snapshot_details(start.workspace, start.session, start.session_id)
        key = await self._key(ctx.tenant_id, details)

        dirs = wsmod.Dirs(self.deps.root, ctx.run_id, inp.stage)
        dirs.reset()
        root = self._prompts_root()
        await asyncio.to_thread(wsmod.materialize, store, start.workspace, dirs.ws)
        await asyncio.to_thread(wsmod.install_references, root, dirs.ws)
        if start.session is not None:
            await asyncio.to_thread(wsmod.restore_session, store, start.session, dirs.config)
        can_resume = start.session is not None and bool(start.session_id)
        sp = stage_prompt(inp.stage, root, settings.agent_model_map())

        state = {"session_id": start.session_id if can_resume else None, "line": "", "details": details}
        usage: dict[str, dict] = {}

        async def on_usage(model: str, u: dict) -> None:
            await record_usage(self.deps.db, uuid.UUID(ctx.tenant_id), model, u, run_id=uuid.UUID(ctx.run_id))
            tot = usage.setdefault(model, {})
            for k, v in u.items():
                tot[k] = tot.get(k, 0) + int(v or 0)

        def on_message(line: str) -> None:
            state["line"] = line

        def on_session(sid: str) -> None:
            state["session_id"] = sid

        backend = PlatformBackend(self.deps.db, store, self.deps.temporal, ctx, on_wait=on_message)
        box = ToolBox(dirs.ws, backend, stage=inp.stage, on_event=on_message)
        spec = SessionSpec(
            ws=dirs.ws,
            home=dirs.home,
            config_dir=dirs.config,
            bin_dir=dirs.bin,
            tmp_dir=dirs.tmp,
            system=sp.system,
            model=sp.model,
            prompt=prompt_for(inp, start, can_resume),
            api_key=key,
            effort=sp.effort,
            max_turns=sp.max_turns,
            resume=state["session_id"],
            max_budget_usd=settings.agent_max_budget_usd,
            fallback_model=settings.agent_fallback_model or None,
        )
        del key

        async def snap() -> dict:
            w = await asyncio.to_thread(wsmod.snapshot, store, dirs.ws)
            s = await asyncio.to_thread(wsmod.save_session, store, dirs.config)
            return snapshot_details(w, s, state["session_id"], state["line"])

        async def beat() -> None:
            last = time.monotonic()
            while True:
                await asyncio.sleep(HEARTBEAT_EVERY_S)
                if time.monotonic() - last >= AGENT_SNAPSHOT_EVERY_S:
                    with contextlib.suppress(Exception):  # a failed snapshot keeps the previous one
                        state["details"] = await snap()
                    last = time.monotonic()
                activity.heartbeat({**state["details"], "line": state["line"][:300]})

        hb = asyncio.create_task(beat())
        try:
            res = await self.deps.runner.run(spec, box, Hooks(on_usage, on_message, on_session))
        except asyncio.CancelledError:
            await asyncio.shield(backend.cancel_builds(list(box.builds)))
            raise
        finally:
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb
        try:
            final = await snap()
        finally:
            # the transcript and files live on in S3 only (the key never left the subprocess env)
            dirs.remove()
        activity.heartbeat(final)  # a retry of this activity resumes from here
        ws_ref = ref_from_dict(final["workspace"])
        sess_ref = ref_from_dict(final["session"]) if final["session"] else None
        if res.denied:
            log.warning(
                "run %s stage %s: %d tool calls denied by the sandbox", ctx.run_id, inp.stage, len(res.denied)
            )
        if res.error in (KEY_INVALID, KEY_EXHAUSTED):
            await mark_invalid(self.deps.db, uuid.UUID(ctx.tenant_id))
            raise ApplicationError(
                "the Anthropic API rejected the tenant's key"
                if res.error == KEY_INVALID
                else "the tenant's Anthropic account has no credit left",
                {**final, "reason": res.error},
                type=errors.LlmKeyInvalid,
                non_retryable=True,
            )
        if res.error == RATE_LIMITED:
            raise ApplicationError(
                "the Anthropic API rate-limited the session",
                {**final, "retry_after_s": res.retry_after_s},
                type=errors.LlmRateLimited,
                non_retryable=True,
            )
        if res.error:
            raise ApplicationError(f"agent session failed: {res.text[:500]}", final, type="AgentFailed")
        summary = res.text
        if res.stop and res.stop != "success":
            summary = f"[{res.stop}] {summary}"
        return AgentStageOutput(
            outcome="needs_answers" if box.questions else "done",
            workspace=ws_ref,
            session=sess_ref,
            session_id=state["session_id"],
            questions=list(box.questions),
            decisions=list(box.decisions),
            usage=usage,
            builds=list(box.builds),
            verdict=box.verdict,
            summary=summary[:2000],
        )
