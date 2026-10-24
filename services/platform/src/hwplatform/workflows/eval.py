"""EvalWorkflow (ADR 0001, 4.4 and 7.7, issue #19): fixtures run as design workflows and are scored.

record_run(running) -> for each trial (fixture x model x repetition), at most `max_parallel` at once:
prepare_eval_trial (project, design version, queued design run, `parent_run_id` = this run) -> child
DesignWorkflow with `answerer="simulator"` -> prepare_score_build -> child BuildWorkflow on the final design
version (cached stages make it cheap when the agent built it) -> score_trial (the score card from the run
manifest and events) -> finish_eval (aggregate, baseline comparison, eval.json and REPORT.md as artifacts)
-> record_run(succeeded, verdict PASS or FAIL).

A trial that fails does not fail the evaluation: it is a stage `failed` and an entry in `errors`, and the
others
go on. A design run that failed or was cancelled is still scored with what it left. Rate limits: a child
design
run waits out a limit on its own timer and tells this workflow (`child_rate_limited`), which starts no new
trial
until that time has passed. Cancellation: the child design runs are cancelled (and waited for) before the
run is
marked `cancelled`.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import SearchAttributeKey, SearchAttributePair, TypedSearchAttributes
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from ..contracts import (
        A_FINISH_EVAL,
        A_PREPARE_EVAL_TRIAL,
        A_PREPARE_SCORE_BUILD,
        A_RECORD_RUN,
        A_RECORD_STAGE,
        A_SCORE_TRIAL,
        BUILD_WORKFLOW,
        DESIGN_WORKFLOW,
        ENGINE_POLICY,
        EVAL_WORKFLOW,
        Q_ENGINE,
        RECORD_POLICY,
        SIGNAL_CHILD_RATE_LIMITED,
        BuildOutput,
        DesignOutput,
        EvalInput,
        EvalOutput,
        FinishEvalInput,
        FinishEvalOut,
        PreparedTrial,
        PrepareTrial,
        RecordedRun,
        RecordRun,
        RecordStage,
        RunCtx,
        ScoreBuildPlan,
        ScoreTrialInput,
        StageOut,
        TrialResult,
    )
    from .build import _error
    from .build import retry as retry_policy


def _child_error(e: ChildWorkflowError) -> dict:
    cause = e.cause
    if isinstance(cause, ApplicationError):
        return {
            "type": cause.type or "ApplicationError",
            "message": str(cause.message)[:2000],
            "retryable": False,
        }
    return {"type": type(cause).__name__, "message": str(cause)[:2000], "retryable": False}


def _attrs(ctx: RunCtx, kind: str) -> TypedSearchAttributes:
    return TypedSearchAttributes(
        [
            SearchAttributePair(SearchAttributeKey.for_keyword(k), v)
            for k, v in (
                ("HwTenantId", ctx.tenant_id),
                ("HwProjectId", ctx.project_id),
                ("HwRunId", ctx.run_id),
                ("HwRunKind", kind),
            )
        ]
    )


def trial_key(fixture_id: str, model: str, n: int) -> str:
    return f"trial.{fixture_id}.{model or 'default'}.t{n}"


@workflow.defn(name=EVAL_WORKFLOW)
class EvalWorkflow:
    def __init__(self) -> None:
        self.arg: EvalInput | None = None
        self.pause_until = None  # datetime: no new trial before it (a child waits out a rate limit)
        self.live: dict[str, int] = {}  # trial key -> seq, while its stage is running
        self.done = 0
        self.total = 0
        # Set when this evaluation is cancelled. A child design run that ends cancelled then is
        # not "a design run
        # that was cancelled on its own": the trial stops and is not scored.
        self.cancelling = False

    @workflow.signal(name=SIGNAL_CHILD_RATE_LIMITED)
    def child_rate_limited(self, seconds: float) -> None:
        until = workflow.now() + timedelta(seconds=max(0.0, float(seconds)))
        if self.pause_until is None or until > self.pause_until:
            self.pause_until = until

    @workflow.query(name="state")
    def state(self) -> dict:
        return {
            "done": self.done,
            "total": self.total,
            "running": sorted(self.live),
            "paused_until": self.pause_until.isoformat() if self.pause_until else None,
        }

    # -- activities ----------
    async def _local(self, name: str, arg, result_type=None):
        return await workflow.execute_local_activity(
            name,
            arg,
            result_type=result_type,
            start_to_close_timeout=timedelta(seconds=RECORD_POLICY.start_to_close_s),
            retry_policy=retry_policy(RECORD_POLICY),
        )

    async def _record_run(self, to: str, **kw) -> RecordedRun:
        return await self._local(A_RECORD_RUN, RecordRun(ctx=self.arg.ctx, to=to, **kw), RecordedRun)

    async def _record_stage(self, key: str, seq: int, status: str, **kw) -> None:
        await self._local(
            A_RECORD_STAGE,
            RecordStage(ctx=self.arg.ctx, key=key, stage="trial", seq=seq, status=status, **kw),
        )

    async def _engine(self, name: str, arg, result_type):
        return await workflow.execute_activity(
            name,
            arg,
            result_type=result_type,
            task_queue=Q_ENGINE,
            start_to_close_timeout=timedelta(seconds=ENGINE_POLICY.start_to_close_s),
            retry_policy=retry_policy(ENGINE_POLICY),
        )

    def _now(self) -> str:
        return workflow.now().isoformat()

    # -- the run ----------
    @workflow.run
    async def run(self, arg: EvalInput) -> EvalOutput:
        self.arg = arg
        rec = await self._record_run("running")
        if rec.status != "running":
            return EvalOutput(arg.ctx.run_id, rec.status, None)
        try:
            return await self._eval(arg)
        except (asyncio.CancelledError, CancelledError):
            await asyncio.shield(self._on_cancel())
            raise
        except ActivityError as e:  # finish_eval failed for good
            err = _error(e)
            await self._record_run("failed", error=err)
            raise ApplicationError(
                f"{err['type']}: {err['message']}", err, type=err["type"], non_retryable=True
            ) from None

    async def _on_cancel(self) -> None:
        for key, seq in sorted(self.live.items(), key=lambda kv: kv[1]):
            await self._record_stage(key, seq, "cancelled", ended_at=self._now(), reason="cancelled")
        await self._record_run("cancelled", by="user")

    async def _eval(self, arg: EvalInput) -> EvalOutput:
        jobs = [
            (trial_key(fx["id"], model, n), fx, model, n)
            for fx in arg.fixtures
            for model in (arg.models or [""])
            for n in range(1, max(1, arg.repetitions) + 1)
        ]
        self.total = len(jobs)
        sem = asyncio.Semaphore(max(1, arg.max_parallel))

        async def one(i: int, job: tuple) -> dict:
            async with sem:
                await self._gate()
                return await self._trial(i + 1, *job)

        tasks = [asyncio.ensure_future(one(i, j)) for i, j in enumerate(jobs)]
        try:
            # workflow.wait, not gather: gather would cancel the trials and wait for them
            # before this task sees
            # the cancel, and a cancelled child run would then look like a trial to score.
            await workflow.wait(tasks)
            results = [t.result() for t in tasks]
        except BaseException:  # a cancel: stop the trials, wait for their child runs to stop
            self.cancelling = True
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        cards = [r["card"] for r in results if "card" in r]
        errors = [r["error"] for r in results if "error" in r]
        fin: FinishEvalOut = await self._engine(
            A_FINISH_EVAL,
            FinishEvalInput(
                ctx=arg.ctx,
                suite=arg.suite,
                cards=cards,
                errors=errors,
                baseline=arg.baseline,
                baseline_name=arg.baseline_name,
                options=arg.options,
            ),
            FinishEvalOut,
        )
        rec = await self._record_run("succeeded", verdict=fin.verdict)
        return EvalOutput(
            arg.ctx.run_id, rec.status, fin.verdict, len(cards), len(errors), fin.report, fin.eval_json
        )

    async def _gate(self) -> None:
        """No new trial while a child design run waits out a rate limit."""
        while self.pause_until is not None and workflow.now() < self.pause_until:
            await workflow.sleep((self.pause_until - workflow.now()).total_seconds())

    async def _trial(self, seq: int, key: str, fx: dict, model: str, n: int) -> dict:
        arg = self.arg
        started = self._now()
        meta = {"fixture": fx["id"], "model": model or "default", "trial": n}
        prep: PreparedTrial | None = None
        try:
            prep = await self._engine(
                A_PREPARE_EVAL_TRIAL,
                PrepareTrial(
                    ctx=arg.ctx,
                    key=key,
                    fixture=fx,
                    model=model,
                    trial=n,
                    budget_usd=arg.budget_usd,
                    question_timeout_s=arg.question_timeout_s,
                ),
                PreparedTrial,
            )
            self.live[key] = seq
            await self._record_stage(key, seq, "running", started_at=started)
            d = prep.design
            status, design_error = "succeeded", None
            try:
                await workflow.execute_child_workflow(
                    DESIGN_WORKFLOW,
                    d,
                    id=d.ctx.workflow_id,
                    task_queue=Q_ENGINE,
                    result_type=DesignOutput,
                    cancellation_type=workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED,
                    parent_close_policy=workflow.ParentClosePolicy.REQUEST_CANCEL,
                    search_attributes=_attrs(d.ctx, "design")
                    if arg.options.get("visibility", True)
                    else None,
                )
            except (
                ChildWorkflowError
            ) as e:  # the design run failed or was cancelled on its own: score what it left
                if self.cancelling:
                    raise
                design_error = _child_error(e)
                status = "cancelled" if isinstance(e.cause, CancelledError) else "failed"
            sin = ScoreTrialInput(
                ctx=arg.ctx, trial=prep, fixture=fx, design_status=status, design_error=design_error
            )
            plan: ScoreBuildPlan = await self._engine(A_PREPARE_SCORE_BUILD, sin, ScoreBuildPlan)
            build_run = ""
            if plan.build is not None:
                b = plan.build
                build_run = b.ctx.run_id
                try:
                    await workflow.execute_child_workflow(
                        BUILD_WORKFLOW,
                        b,
                        id=b.ctx.workflow_id,
                        task_queue=Q_ENGINE,
                        result_type=BuildOutput,
                        cancellation_type=workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED,
                        parent_close_policy=workflow.ParentClosePolicy.REQUEST_CANCEL,
                        search_attributes=_attrs(b.ctx, "build")
                        if arg.options.get("visibility", True)
                        else None,
                    )
                except ChildWorkflowError:  # a failed build still left its partial score card
                    if self.cancelling:
                        raise
            tr: TrialResult = await self._engine(
                A_SCORE_TRIAL,
                ScoreTrialInput(
                    ctx=arg.ctx,
                    trial=prep,
                    fixture=fx,
                    build_run_id=build_run,
                    design_status=status,
                    design_error=design_error,
                ),
                TrialResult,
            )
            done = StageOut(
                stage="trial",
                verdict="pass" if status == "succeeded" else "fail",
                cache_key="",
                cached=False,
                result=tr.card_ref,
                summary={
                    **meta,
                    "score": tr.card.get("score"),
                    "design_run_id": d.ctx.run_id,
                    "design_status": status,
                    "questions": tr.card.get("questions"),
                },
            )
            await self._record_stage(
                key, seq, "succeeded", started_at=started, ended_at=self._now(), out=done
            )
            self.live.pop(key, None)
            self.done += 1
            return {"card": tr.card}
        except (ActivityError, ChildWorkflowError) as e:  # this trial only: the evaluation goes on
            if self.cancelling or (isinstance(e, ActivityError) and isinstance(e.cause, CancelledError)):
                raise  # the evaluation is being cancelled
            err = _child_error(e) if isinstance(e, ChildWorkflowError) else _error(e)
            await self._record_stage(key, seq, "failed", started_at=started, ended_at=self._now(), error=err)
            self.live.pop(key, None)
            self.done += 1
            return {"error": {**meta, "key": key, "error": f"{err['type']}: {err['message']}"}}
