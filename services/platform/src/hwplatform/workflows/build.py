"""BuildWorkflow (ADR 0001, 4.4 to 4.7): one build run, driven by the same planner as the inline CLI.

record_run(running) -> resolve_run -> loop {plan -> record_stage(running) + run_stage_activity on
QUEUE_OF[stage] -> wait FIRST_COMPLETED -> record_stage(finished)} -> render_run -> record_run(succeeded).
Layout attempts run as parallel activities on the layout queue; an attempt the planner drops (cutoff, budget)
is cancelled and recorded as skipped. A stage activity that fails for good (non-retryable, or retries
exhausted) fails the run after a best-effort render; a cancelled workflow cancels its activities (the stage
child processes are dead first) and records `cancelled` in a shielded local activity.

Determinism: only temporalio, hwplatform.contracts and hwflow.buildplan (stdlib only) are imported; time is
workflow.now(); decisions depend on result keys only (the planner's rule), and results are recorded in
completion order.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError

with workflow.unsafe.imports_passed_through():
    from hwflow import buildplan

    from ..contracts import (
        A_RECORD_RUN,
        A_RECORD_STAGE,
        A_RENDER_RUN,
        A_RESOLVE_RUN,
        A_STAGE,
        BUILD_WORKFLOW,
        ENGINE_POLICY,
        Q_ENGINE,
        Q_LAYOUT,
        QUEUE_OF,
        RECORD_POLICY,
        SLOTS,
        STAGE_POLICY,
        BuildInput,
        BuildOutput,
        Policy,
        RecordedRun,
        RecordRun,
        RecordStage,
        RenderInput,
        RenderOutput,
        ResolvedRun,
        ResolveInput,
        StageActivityInput,
        StageOut,
        errors,
    )


def retry(p: Policy) -> RetryPolicy:
    return RetryPolicy(
        initial_interval=timedelta(seconds=p.initial_s),
        backoff_coefficient=p.backoff,
        maximum_interval=timedelta(seconds=p.max_interval_s),
        maximum_attempts=p.attempts,
        non_retryable_error_types=list(errors.NON_RETRYABLE),
    )


def stage_policy(stage: str, settings: dict) -> Policy:
    p = STAGE_POLICY[stage]
    if stage == buildplan.ATTEMPT:  # layout.budget_s + 10 min
        budget = float((settings.get("layout") or {}).get("budget_s", 3000))
        p = Policy(budget + 600, p.heartbeat_s, p.initial_s, p.backoff, p.max_interval_s, p.attempts)
    return p


def _error(e: BaseException) -> dict:
    """{"type", "message", "retryable"} of an activity failure (ApplicationError.type when it has one)."""
    cause = e.cause if isinstance(e, ActivityError) and e.cause is not None else e
    if isinstance(cause, ApplicationError):
        return {
            "type": cause.type or "ApplicationError",
            "message": str(cause.message)[:2000],
            "retryable": not cause.non_retryable and cause.type not in errors.NON_RETRYABLE,
        }
    return {"type": type(cause).__name__, "message": str(cause)[:2000], "retryable": False}


class _StageFailed(Exception):
    def __init__(self, key: str, error: dict):
        super().__init__(f"{key}: {error.get('type')}: {error.get('message')}")
        self.key, self.error = key, error


@workflow.defn(name=BUILD_WORKFLOW)
class BuildWorkflow:
    def __init__(self) -> None:
        self.arg: BuildInput | None = None
        self.seq = 0
        self.seqs: dict[str, int] = {}
        self.started: dict[str, str] = {}
        self.outs: dict[str, StageOut] = {}
        self.running: dict[str, asyncio.Task] = {}

    # -- local activities (one transaction each: row + outbox event) ----------
    async def _record_run(self, to: str, **kw) -> RecordedRun:
        return await workflow.execute_local_activity(
            A_RECORD_RUN,
            RecordRun(ctx=self.arg.ctx, to=to, **kw),
            result_type=RecordedRun,
            start_to_close_timeout=timedelta(seconds=RECORD_POLICY.start_to_close_s),
            retry_policy=retry(RECORD_POLICY),
        )

    async def _record_stage(self, key: str, stage: str, status: str, **kw) -> None:
        await workflow.execute_local_activity(
            A_RECORD_STAGE,
            RecordStage(
                ctx=self.arg.ctx,
                key=key,
                stage=stage,
                seq=self.seqs[key],
                status=status,
                started_at=self.started.get(key),
                **kw,
            ),
            start_to_close_timeout=timedelta(seconds=RECORD_POLICY.start_to_close_s),
            retry_policy=retry(RECORD_POLICY),
        )

    def _now(self) -> str:
        return workflow.now().isoformat()

    async def _engine(self, name: str, arg, result_type):
        return await workflow.execute_activity(
            name,
            arg,
            result_type=result_type,
            task_queue=Q_ENGINE,
            start_to_close_timeout=timedelta(seconds=ENGINE_POLICY.start_to_close_s),
            retry_policy=retry(ENGINE_POLICY),
        )

    # -- stages ----------
    async def _begin(self, a: buildplan.RunStage) -> None:
        self.seq += 1
        self.seqs[a.key] = self.seq
        self.started[a.key] = self._now()
        await self._record_stage(a.key, a.stage, "running")

    def _start(
        self, a: buildplan.RunStage, resolved: ResolvedRun, state: dict, rebuild: bool
    ) -> asyncio.Task:
        p = stage_policy(a.stage, resolved.settings)
        inp = StageActivityInput(
            ctx=self.arg.ctx,
            key=a.key,
            stage=a.stage,
            design=self.arg.design,
            project=self.arg.project,
            upstream={u: self.outs[u] for u in a.upstream if u in self.outs},
            state=dict(state),
            settings=resolved.settings,
            params=dict(a.params),
            rebuild=rebuild,
        )
        return workflow.start_activity(
            A_STAGE,
            inp,
            result_type=StageOut,
            task_queue=QUEUE_OF[a.stage],
            start_to_close_timeout=timedelta(seconds=p.start_to_close_s),
            heartbeat_timeout=timedelta(seconds=p.heartbeat_s) if p.heartbeat_s else None,
            retry_policy=retry(p),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            summary=a.key,
        )

    async def _finish(self, key: str, out: StageOut) -> None:
        self.outs[key] = out
        await self._record_stage(
            key,
            out.stage,
            "failed" if out.verdict == "error" else "succeeded",
            ended_at=self._now(),
            out=out,
            error=out.error,
            attempt=out.attempt,
        )

    async def _drop(self, key: str, reason: str, status: str = "skipped") -> None:
        """Cancel a running stage activity and wait until it is gone (its child process is dead)."""
        t = self.running.pop(key)
        t.cancel()
        try:
            await t
        except (ActivityError, CancelledError, asyncio.CancelledError):
            pass
        else:
            pass  # it finished before the cancel arrived: still not wanted, so not used
        await self._record_stage(
            key,
            buildplan.ATTEMPT if key.startswith(buildplan.ATTEMPT) else key,
            status,
            ended_at=self._now(),
            reason=reason,
        )

    # -- the run ----------
    @workflow.run
    async def run(self, arg: BuildInput) -> BuildOutput:
        self.arg = arg
        rec = await self._record_run("running")
        if rec.status != "running":  # cancelled (or finished) before it started: nothing to do
            return BuildOutput(arg.ctx.run_id, rec.status, None, None, None)
        try:
            return await self._build(arg)
        except (asyncio.CancelledError, CancelledError):
            for key in sorted(self.running):
                await asyncio.shield(self._drop(key, "cancelled", status="cancelled"))
            await asyncio.shield(self._record_run("cancelled", by="user"))
            raise
        except _StageFailed as e:
            for key in sorted(self.running):
                await self._drop(key, "run failed", status="cancelled")
            await self._render_best_effort()
            await self._record_run("failed", error=e.error)
            raise ApplicationError(str(e), e.error, type=e.error.get("type"), non_retryable=True) from None
        except ActivityError as e:  # resolve_run / render_run failed for good
            err = _error(e)
            await self._render_best_effort()
            await self._record_run("failed", error=err)
            raise ApplicationError(
                f"{err['type']}: {err['message']}", err, type=err["type"], non_retryable=True
            ) from None

    async def _render_best_effort(self) -> RenderOutput | None:
        try:
            return await self._engine(A_RENDER_RUN, RenderInput(self.arg.ctx), RenderOutput)
        except ActivityError:
            return None

    async def _build(self, arg: BuildInput) -> BuildOutput:
        resolved: ResolvedRun = await self._engine(
            A_RESOLVE_RUN,
            ResolveInput(
                ctx=arg.ctx,
                design=arg.design,
                project=arg.project,
                options=arg.options,
                settings_overrides=arg.settings_overrides,
            ),
            ResolvedRun,
        )
        opts = {
            **arg.options,
            "layout_blocked": resolved.layout_blocked is not None,
            "workers": SLOTS[Q_LAYOUT]["activities"],
        }
        lay = dict(resolved.settings.get("layout") or {})
        rebuild = bool(arg.options.get("rebuild"))
        state = dict(resolved.state)
        layout_t0: float | None = None
        stopped: str | None = None
        while True:
            now = workflow.now().timestamp()
            view = {
                k: {"verdict": o.verdict, "summary": dict(o.summary)} for k, o in sorted(self.outs.items())
            }
            p = buildplan.plan(opts, view, lay, now, layout_t0 if layout_t0 is not None else now)
            if isinstance(p, buildplan.Done):
                stopped = None if p.reason == "complete" else p.reason
                break
            if layout_t0 is None and any(a.stage == buildplan.ATTEMPT for a in p):
                layout_t0 = now  # the search starts now: its deadline is now + budget_s
            wanted = {a.key for a in p}
            if self.running:
                search = buildplan.layout_search(
                    opts, buildplan.attempts_of(view), lay, now, layout_t0 or now
                )
                for key in sorted(k for k in self.running if k not in wanted):
                    await self._drop(key, buildplan.drop_reason(search, buildplan.attempt_index(key) or 0))
            for a in p:
                if a.key in self.running or a.key in self.outs:
                    continue
                await self._begin(a)
                if a.params.get("blocked"):  # resolve_run found the structure sent back by the layout history
                    await self._finish(a.key, resolved.layout_blocked)
                    continue
                self.running[a.key] = self._start(a, resolved, state, rebuild)
            if not self.running:
                continue  # a blocked layout was recorded: plan again
            timeout = None
            if (
                opts.get("auto")
                and layout_t0 is not None
                and any(k.startswith(buildplan.ATTEMPT) for k in self.running)
            ):
                timeout = max(1.0, layout_t0 + float(lay.get("budget_s", 3000)) - now)
            done, _ = await workflow.wait(
                list(self.running.values()), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            for key in sorted(k for k, t in self.running.items() if t in done):
                t = self.running.pop(key)
                try:
                    out: StageOut = t.result()
                except ActivityError as e:
                    err = _error(e)
                    self.outs.pop(key, None)
                    await self._record_stage(
                        key, key.partition(".")[0], "failed", ended_at=self._now(), error=err
                    )
                    raise _StageFailed(key, err) from None
                await self._finish(key, out)
                state.update(out.state)
        for key in buildplan.remaining(opts, {k: {} for k in self.outs}) if stopped else ():
            self.seq += 1
            self.seqs[key] = self.seq
            await self._record_stage(key, key, "skipped", reason=stopped)
        rendered: RenderOutput = await self._engine(A_RENDER_RUN, RenderInput(arg.ctx), RenderOutput)
        rec = await self._record_run("succeeded", verdict=rendered.verdict)
        return BuildOutput(
            run_id=arg.ctx.run_id,
            status=rec.status,
            verdict=rendered.verdict,
            report=rendered.report,
            status_json=rendered.status_json,
            stages={
                k: {"verdict": o.verdict, "cached": o.cached, "summary": dict(o.summary)}
                for k, o in sorted(self.outs.items())
            },
        )
