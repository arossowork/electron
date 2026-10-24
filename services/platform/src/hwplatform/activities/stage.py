"""run_stage_activity (ADR 0001, 4.2 and 4.5): one stage of a run in a child process, on the queue of its
tools.

The child is `python -m hwplatform.activities.child in.json out.json` in its own process group (the engine's
`execute_stage` with the tenant's S3 store and the Postgres stage cache). The activity heartbeats every
HEARTBEAT_EVERY_S with the child's last output line. On cancel (or a heartbeat the server refuses) it sends
SIGTERM to the group, SIGKILL after KILL_GRACE_S, removes the scratch dir and re-raises the cancel.

Outcomes: the stage ran (any verdict, including `error` for an engine exception) -> StageOut, the planner
decides. DesignInvalid / ToolMissing -> non-retryable ApplicationError. A store error -> StoreUnavailable; a
child killed by SIGKILL (OOM) -> WorkerLost; any other crash -> ToolCrashed; all three are retried per the
stage's policy.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path

from hwflow.artifacts import ref_to_dict
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..contracts import A_STAGE, HEARTBEAT_EVERY_S, KILL_GRACE_S, StageActivityInput, StageOut, errors
from .deps import Deps
from .wire import out_from_json, out_to_json

# error type names (of the child's exception) that mean the artifact store or the database did not answer
STORE_ERRORS = {
    "EndpointConnectionError",
    "ConnectionClosedError",
    "ConnectTimeoutError",
    "ReadTimeoutError",
    "OperationalError",
    "InterfaceError",
    "ConnectionRefusedError",
    "ConnectionError",
    "TimeoutError",
    "CannotConnectNowError",
    "ClientError",
    "ArtifactHashMismatch",
}
NON_RETRYABLE = {errors.DesignInvalid, errors.ToolMissing}
REUSE_VERDICTS = ("pass", "fail", "blocked")  # the platform reuses every cacheable verdict (ADR 2.4)


def job_doc(inp: StageActivityInput, scratch: Path, tool_cache: Path) -> dict:
    """The child's in.json: the engine's StageInput JSON plus tenant, scratch and cache dirs."""
    from hwflow.pipeline.spec import STAGE_SPECS

    spec = STAGE_SPECS[inp.stage]
    state = {s: (ref_to_dict(inp.state[s]) if inp.state.get(s) else None) for s in spec.state_in}
    return {
        "spec": inp.stage,
        "tenant_id": inp.ctx.tenant_id,
        "input": {
            "run_id": inp.ctx.run_id,
            "stage": inp.stage,
            "design": ref_to_dict(inp.design),
            "project": ref_to_dict(inp.project),
            "upstream": {k: out_to_json(v) for k, v in inp.upstream.items()},
            "state": state,
            "settings": dict(inp.settings),
            "params": dict(inp.params),
        },
        "scratch_root": str(scratch),
        "tool_cache_dir": str(tool_cache),
        "rebuild": inp.rebuild,
        "reuse_verdicts": list(REUSE_VERDICTS),
    }


def classify(rc: int, doc: dict | None, tail: str) -> ApplicationError:
    """The ApplicationError for a child that did not produce a StageResult."""
    err = (doc or {}).get("error") or {}
    t, msg = err.get("type"), err.get("message") or tail
    if t in NON_RETRYABLE:
        return ApplicationError(msg, type=t, non_retryable=True)
    if t in STORE_ERRORS:
        return ApplicationError(f"{t}: {msg}", type=errors.StoreUnavailable)
    if rc == -signal.SIGKILL:
        return ApplicationError(
            f"the stage process was killed (SIGKILL, out of memory?): {tail}", type=errors.WorkerLost
        )
    what = f"{t}: {msg}" if t else f"the stage process exited with code {rc}: {tail}"
    return ApplicationError(what, type=errors.ToolCrashed)


async def kill_group(proc: asyncio.subprocess.Process, grace: float = KILL_GRACE_S) -> None:
    """SIGTERM to the child's process group, SIGKILL after `grace` seconds."""
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, grace)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), wait)
            # the leader is gone; tools it started may still run in the group
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            return
        except TimeoutError:
            continue


class StageActivity:
    def __init__(self, deps: Deps, *, heartbeat_every: float = HEARTBEAT_EVERY_S):
        self.deps = deps
        self.heartbeat_every = heartbeat_every

    @activity.defn(name=A_STAGE)
    async def run_stage_activity(self, inp: StageActivityInput) -> StageOut:
        info = activity.info()
        job = Path(tempfile.mkdtemp(prefix=f"{inp.key}-", dir=self.deps.scratch()))
        last = {"line": f"{inp.key}: starting"}
        try:
            (job / "in.json").write_text(json.dumps(job_doc(inp, job / "scratch", self.deps.tool_cache())))
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "hwplatform.activities.child",
                str(job / "in.json"),
                str(job / "out.json"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            lines: list[str] = []

            async def read():
                if proc.stdout is None:
                    return
                async for raw in proc.stdout:
                    ln = raw.decode(errors="replace").rstrip()
                    if ln:
                        last["line"] = ln[:500]
                        lines.append(ln)
                        del lines[:-40]

            reader = asyncio.create_task(read())
            try:
                while True:
                    activity.heartbeat(last["line"])
                    try:
                        await asyncio.wait_for(asyncio.shield(proc.wait()), self.heartbeat_every)
                        break
                    except TimeoutError:
                        continue
                await reader
            except BaseException:  # cancelled (or the worker shuts down): the child and its tools go first
                reader.cancel()
                await asyncio.shield(kill_group(proc))
                raise
            rc = proc.returncode
            doc = None
            try:
                doc = json.loads((job / "out.json").read_text())
            except (OSError, ValueError):
                pass
            if rc == 0 and doc is not None and "stage" in doc:  # a StageResult (its "error" is None)
                return out_from_json(doc, attempt=info.attempt)
            raise classify(rc, doc, " | ".join(lines[-5:]))
        finally:
            shutil.rmtree(job, ignore_errors=True)
