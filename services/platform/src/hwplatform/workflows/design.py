"""DesignWorkflow (ADR 0001, 4.4, 7.3, 7.4, 7.6): the design conversation as a workflow.

record_run(running) -> get_design_version -> for each stage: record_stage(running) -> agent_stage on
hwflow-agent -> (needs_answers: create_design_version, record_question(ask), wait for `answer_question`,
record_question(resumed), agent_stage again with the answers and the saved session) -> create_design_version
when the workspace changed -> record_stage(succeeded) -> record_run(succeeded, verdict of the last linked
build).

LLM conditions are handled here, not by activity retries: an invalid or exhausted key (LlmKeyInvalid) sets the
run `waiting` (reason llm_key_invalid) and waits for the `retry` signal that `PUT /tenant/llm-keys/anthropic`
sends (up to KEY_RETRY_WAIT_S); a rate limit (LlmRateLimited) waits on a timer (retry-after, else doubling
backoff). Either way the stage goes on from the workspace and transcript snapshot in the error details. No
polling: signals and timers only.

A worker restart: finished stages are in the history and do not run again; a stage cut by a crash is retried
by Temporal (heartbeat timeout) and resumes its session from the last snapshot the activity heartbeated.

Cancellation: the running agent_stage is cancelled and waited for (it cancels the builds it started), the
linked builds this run knows are cancelled by ID, an open question is marked cancelled, then
record_run(cancelled).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError

with workflow.unsafe.imports_passed_through():
    from hwflow.artifacts import ArtifactRef

    from ..contracts import (
        A_AGENT_STAGE,
        A_CREATE_DESIGN_VERSION,
        A_GET_DESIGN_VERSION,
        A_RECORD_QUESTION,
        A_RECORD_RUN,
        A_RECORD_STAGE,
        A_SIMULATE_ANSWER,
        AGENT_NON_RETRYABLE,
        AGENT_POLICY,
        DESIGN_STAGES,
        DESIGN_WORKFLOW,
        ENGINE_POLICY,
        KEY_RETRY_WAIT_S,
        Q_AGENT,
        Q_ENGINE,
        RATE_LIMIT_BACKOFF_S,
        RECORD_POLICY,
        SIGNAL_ANSWER,
        SIGNAL_RETRY,
        AgentStageInput,
        AgentStageOutput,
        DesignInput,
        DesignOutput,
        DesignVersionInput,
        DesignVersionOut,
        QuestionAnswer,
        RecordedRun,
        RecordQuestion,
        RecordRun,
        RecordStage,
        StageOut,
        errors,
    )
    from .build import _error
    from .build import retry as retry_policy


def _agent_retry() -> RetryPolicy:
    p = AGENT_POLICY
    return RetryPolicy(
        initial_interval=timedelta(seconds=p.initial_s),
        backoff_coefficient=p.backoff,
        maximum_interval=timedelta(seconds=p.max_interval_s),
        maximum_attempts=p.attempts,
        non_retryable_error_types=list(AGENT_NON_RETRYABLE),
    )


def _ref(d) -> ArtifactRef | None:
    if not d:
        return None
    return ArtifactRef(
        d["sha256"], int(d["size"]), d.get("media_type") or "application/octet-stream", d.get("name", "")
    )


def _failure(e: ActivityError) -> tuple[str, dict]:
    """(ApplicationError.type, first detail dict) of an activity failure."""
    cause = e.cause
    if isinstance(cause, ApplicationError):
        det = cause.details[0] if cause.details and isinstance(cause.details[0], dict) else {}
        return cause.type or "ApplicationError", det
    return type(cause).__name__ if cause else "ActivityError", {}


class _Failed(Exception):
    def __init__(self, error: dict):
        super().__init__(f"{error.get('type')}: {error.get('message')}")
        self.error = error


@workflow.defn(name=DESIGN_WORKFLOW)
class DesignWorkflow:
    def __init__(self) -> None:
        self.arg: DesignInput | None = None
        self.answers: dict[str, QuestionAnswer] = {}
        self.retries = 0  # `retry` signals received
        self.seq = 0
        self.builds: list[str] = []
        self.open_question: str | None = None
        self.waiting: dict | None = None
        self.stage: str | None = None
        self.started: dict[str, str] = {}
        self.started_seq: dict[str, int] = {}

    # -- signals and the state query ----------
    @workflow.signal(name=SIGNAL_ANSWER)
    def answer_question(self, a: QuestionAnswer) -> None:
        self.answers[a.question_id] = a

    @workflow.signal(name=SIGNAL_RETRY)
    def retry(self) -> None:
        self.retries += 1

    @workflow.query(name="state")
    def state(self) -> dict:
        return {"stage": self.stage, "waiting": self.waiting, "builds": list(self.builds)}

    # -- local and engine activities ----------
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

    async def _record_stage(self, stage: str, status: str, **kw) -> None:
        await self._local(
            A_RECORD_STAGE,
            RecordStage(
                ctx=self.arg.ctx,
                key=stage,
                stage=stage,
                seq=self.started_seq[stage],
                status=status,
                started_at=self.started.get(stage),
                **kw,
            ),
        )

    async def _question(self, op: str, **kw) -> str:
        return await self._local(A_RECORD_QUESTION, RecordQuestion(ctx=self.arg.ctx, op=op, **kw), str)

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
    async def run(self, arg: DesignInput) -> DesignOutput:
        self.arg = arg
        rec = await self._record_run("running")
        if rec.status != "running":
            return DesignOutput(arg.ctx.run_id, rec.status, None, arg.design_version_id)
        try:
            return await self._design(arg)
        except (asyncio.CancelledError, CancelledError):
            await asyncio.shield(self._on_cancel())
            raise
        except _Failed as e:
            if self.stage in self.started_seq:
                await self._record_stage(self.stage, "failed", ended_at=self._now(), error=e.error)
            await self._record_run("failed", error=e.error)
            raise ApplicationError(str(e), e.error, type=e.error.get("type"), non_retryable=True) from None
        except ActivityError as e:
            if isinstance(e.cause, CancelledError):  # the agent session finished its cancel (WAIT_...)
                await asyncio.shield(self._on_cancel())
                raise e.cause from None
            err = _error(e)  # get/create_design_version failed for good
            await self._record_run("failed", error=err)
            raise ApplicationError(
                f"{err['type']}: {err['message']}", err, type=err["type"], non_retryable=True
            ) from None

    async def _on_cancel(self) -> None:
        for wf in list(self.builds):  # linked builds (ADR 4.6), by ID; finished ones ignore it
            try:
                await workflow.get_external_workflow_handle(wf).cancel()
            except Exception:  # noqa: BLE001, S110 - a build that already ended cannot be cancelled
                pass
        if self.open_question:
            await self._question("cancelled", question_id=self.open_question)
        if self.stage in self.started_seq:
            await self._record_stage(self.stage, "cancelled", ended_at=self._now(), reason="cancelled")
        await self._record_run("cancelled", by="user")

    async def _design(self, arg: DesignInput) -> DesignOutput:
        dv: DesignVersionOut = await self._engine(
            A_GET_DESIGN_VERSION,
            DesignVersionInput(ctx=arg.ctx, design_version_id=arg.design_version_id),
            DesignVersionOut,
        )
        workspace, version_id = dv.project, dv.id
        verdict: str | None = None
        rounds = 0
        stages: dict[str, dict] = {}
        for stage in list(arg.stages) or list(DESIGN_STAGES):
            self.stage = stage
            self.seq += 1
            self.started_seq[stage] = self.seq
            self.started[stage] = self._now()
            await self._record_stage(stage, "running")
            out, rounds = await self._stage(stage, workspace, version_id, rounds)
            if out.workspace.sha256 != workspace.sha256:
                version_id = await self._save(out.workspace, version_id, f"agent: {stage}")
            workspace = out.workspace
            verdict = out.verdict or verdict
            summary = {
                "summary": out.summary[:1500],
                "decisions": out.decisions[-20:],
                "rounds": rounds,
                "design_version_id": version_id,
                "builds": len(out.builds),
                "usage": out.usage,
            }
            stages[stage] = {"summary": out.summary[:300], "rounds": rounds, "verdict": out.verdict}
            done = StageOut(
                stage=stage,
                verdict="pass",
                cache_key="",
                cached=False,
                result=workspace,
                outputs={"session": out.session} if out.session else {},
                summary=summary,
            )
            await self._record_stage(stage, "succeeded", ended_at=self._now(), out=done)
        self.stage = None
        rec = await self._record_run("succeeded", verdict=verdict)
        return DesignOutput(arg.ctx.run_id, rec.status, verdict, version_id, stages)

    async def _save(self, workspace: ArtifactRef, parent: str, message: str) -> str:
        """A new design version from the workspace; one without a valid design.yaml keeps the old version."""
        try:
            dv: DesignVersionOut = await self._engine(
                A_CREATE_DESIGN_VERSION,
                DesignVersionInput(ctx=self.arg.ctx, workspace=workspace, parent_id=parent, message=message),
                DesignVersionOut,
            )
        except ActivityError as e:
            if _failure(e)[0] == errors.DesignInvalid:
                workflow.logger.warning("no design version after %s: %s", message, _error(e)["message"])
                return parent
            raise
        return dv.id

    async def _stage(
        self, stage: str, workspace: ArtifactRef, version_id: str, rounds: int
    ) -> tuple[AgentStageOutput, int]:
        """One stage to its end: question rounds, key waits and rate-limit timers included."""
        session: ArtifactRef | None = None
        session_id: str | None = None
        answers: dict | None = None
        questions: list[dict] = []
        resume: str | None = None
        backoff = RATE_LIMIT_BACKOFF_S[0]
        while True:
            try:
                out: AgentStageOutput = await workflow.execute_activity(
                    A_AGENT_STAGE,
                    AgentStageInput(
                        ctx=self.arg.ctx,
                        stage=stage,
                        workspace=workspace,
                        brief=self.arg.brief,
                        session=session,
                        session_id=session_id,
                        answers=answers,
                        questions=questions,
                        resume=resume,
                        round=rounds,
                    ),
                    result_type=AgentStageOutput,
                    task_queue=Q_AGENT,
                    start_to_close_timeout=timedelta(seconds=AGENT_POLICY.start_to_close_s),
                    heartbeat_timeout=timedelta(seconds=AGENT_POLICY.heartbeat_s),
                    retry_policy=_agent_retry(),
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                    summary=stage,
                )
            except ActivityError as e:
                if isinstance(e.cause, CancelledError):
                    raise
                typ, det = _failure(e)
                if det.get("workspace"):  # go on from the snapshot the activity saved
                    workspace = _ref(det["workspace"])
                    session = _ref(det.get("session"))
                    session_id = det.get("session_id")
                if typ == errors.LlmKeyInvalid:
                    await self._wait_for_key(stage, det.get("reason") or "key_invalid")
                elif typ == errors.LlmRateLimited:
                    delay = float(det.get("retry_after_s") or backoff)
                    backoff = min(backoff * 2, RATE_LIMIT_BACKOFF_S[1])
                    self.waiting = {"reason": "rate_limited", "seconds": delay}
                    await workflow.sleep(timedelta(seconds=min(delay, RATE_LIMIT_BACKOFF_S[1])))
                    self.waiting = None
                else:
                    raise _Failed(_error(e)) from None
                if det.get("reason") != "no_key":  # the session ran: its transcript has the last prompt
                    answers, resume = None, "retry"
                continue
            for b in out.builds:
                if b not in self.builds:
                    self.builds.append(b)
            workspace, session, session_id = out.workspace, out.session, out.session_id
            if out.outcome != "needs_answers":
                return out, rounds
            rounds += 1
            version_id = await self._save(workspace, version_id, f"agent: {stage}, questions round {rounds}")
            questions = list(out.questions)
            answers = await self._ask(stage, questions)
            resume = "answers"

    async def _ask(self, stage: str, questions: list[dict]) -> dict:
        qid = str(workflow.uuid4())
        await self._question("ask", question_id=qid, stage=stage, questions=questions)
        self.open_question = qid
        self.waiting = {"reason": "question", "question_id": qid}
        if self.arg.answerer == "simulator":  # #19: the evals product-person simulator answers
            got = await workflow.execute_activity(
                A_SIMULATE_ANSWER,
                RecordQuestion(
                    ctx=self.arg.ctx, op="simulate", question_id=qid, stage=stage, questions=questions
                ),
                result_type=dict,
                task_queue=Q_AGENT,
                start_to_close_timeout=timedelta(minutes=10),
                heartbeat_timeout=timedelta(seconds=60),
            )
            self.answers[qid] = QuestionAnswer(qid, got, "simulator")
        t = self.arg.question_timeout_s
        try:
            await workflow.wait_condition(
                lambda: qid in self.answers, timeout=timedelta(seconds=t) if t else None
            )
        except TimeoutError:
            await self._question("expired", question_id=qid)
            self.open_question = None
            raise _Failed(
                {"type": errors.QuestionExpired, "message": f"no answer in {t:.0f} s", "retryable": False}
            ) from None
        self.open_question = None
        self.waiting = None
        await self._question("resumed", question_id=qid)
        return dict(self.answers[qid].answers)

    async def _wait_for_key(self, stage: str, reason: str) -> None:
        seen = self.retries
        self.waiting = {"reason": "llm_key_invalid", "detail": reason}
        await self._question("key_wait", stage=stage)
        try:
            await workflow.wait_condition(
                lambda: self.retries > seen, timeout=timedelta(seconds=KEY_RETRY_WAIT_S)
            )
        except TimeoutError:
            raise _Failed(
                {
                    "type": errors.LlmKeyInvalid,
                    "message": f"no valid Anthropic API key within {KEY_RETRY_WAIT_S // 3600} h ({reason})",
                    "retryable": False,
                }
            ) from None
        self.waiting = None
        await self._question("key_resumed", stage=stage)
