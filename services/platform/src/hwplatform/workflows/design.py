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

Questions (#54, ADR 7.7): `question_timeout_s` (the setting, default 1 h) is a durable timer on every
question. A question whose items are all `critical: false` is then answered with the agent's `recommended`
options (`record_question(auto_answered)`: `question.auto_answered`) and the stage goes on, the defaulted
answers marked as such in the agent's message and in `notes/decisions.md`. A question with any critical item
keeps waiting without a limit; `question.waiting_critical` tells the UI once. A person's late answer to a
defaulted question (signal `answer_question` with `overrides_default`) is applied at the next stage boundary:
the stage that asked and every later stage that already ran run again (each resumes its own session and is
told what changed), then the run goes on. Chat (`chat` signal): the message is stored by the API and the agent
activity takes it at its next turn boundary, so the workflow only counts the signals (query `state`).

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
        RATE_LIMIT_RESET_MAX_S,
        RECORD_POLICY,
        SIGNAL_ANSWER,
        SIGNAL_CHAT,
        SIGNAL_CHILD_RATE_LIMITED,
        SIGNAL_RETRY,
        AgentStageInput,
        AgentStageOutput,
        ChatSignal,
        DesignInput,
        DesignOutput,
        DesignVersionInput,
        DesignVersionOut,
        QuestionAnswer,
        RecordedRun,
        RecordQuestion,
        RecordRun,
        RecordStage,
        SimulateInput,
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


def _recommended(q: dict):
    r = q.get("recommended")
    return r if r is not None else q.get("default")


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
        self.attempts: dict[str, int] = {}  # stage -> times it ran (a rerun after an override is another)
        self.chat: list[str] = []  # message ids of `chat` signals
        self.overrides: list[QuestionAnswer] = []  # late answers to defaulted questions, in arrival order
        self.overrides_seen = 0
        self.asked: dict[str, dict] = {}  # question id -> {stage, questions, defaults}

    # -- signals and the state query ----------
    @workflow.signal(name=SIGNAL_ANSWER)
    def answer_question(self, a: QuestionAnswer) -> None:
        if a.overrides_default:
            self.overrides.append(a)
        else:
            self.answers[a.question_id] = a

    @workflow.signal(name=SIGNAL_CHAT)
    def chat_message(self, m: ChatSignal) -> None:
        self.chat.append(m.message_id)

    @workflow.signal(name=SIGNAL_RETRY)
    def retry(self) -> None:
        self.retries += 1

    @workflow.query(name="state")
    def state(self) -> dict:
        return {
            "stage": self.stage,
            "waiting": self.waiting,
            "builds": list(self.builds),
            "chat_messages": len(self.chat),
            "overrides": len(self.overrides),
        }

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
                attempt=self.attempts.get(stage, 1),
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
        stage_list = list(arg.stages) or list(DESIGN_STAGES)
        sessions: dict[str, tuple[ArtifactRef | None, str | None]] = {}  # stage -> its last session
        changes: list[dict] = []  # defaulted answers a person replaced (#54)
        redo_from = len(stage_list)  # stages from here on run again, told about `changes`
        i = 0
        while i < len(stage_list):
            stage = stage_list[i]
            redo = changes if i >= redo_from and stage in self.attempts else []  # ran before: runs again
            self.stage = stage
            self.seq += 1
            self.attempts[stage] = self.attempts.get(stage, 0) + 1
            self.started_seq[stage] = self.seq
            self.started[stage] = self._now()
            await self._record_stage(stage, "running")
            out, rounds = await self._stage(stage, workspace, version_id, rounds, sessions.get(stage), redo)
            if out.workspace.sha256 != workspace.sha256:
                version_id = await self._save(out.workspace, version_id, f"agent: {stage}")
            workspace = out.workspace
            sessions[stage] = (out.session, out.session_id)
            verdict = out.verdict or verdict
            summary = {
                "summary": out.summary[:1500],
                "decisions": out.decisions[-20:],
                "rounds": rounds,
                "design_version_id": version_id,
                "builds": len(out.builds),
                "usage": out.usage,
            }
            if redo:
                summary["rerun"] = {"attempt": self.attempts[stage], "changes": redo}
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
            new = self._take_overrides()
            if new:  # a person replaced a default: the stage that asked and all later ones run again
                changes = [*changes, *new]
                redo_from = min(stage_list.index(c["stage"]) for c in new if c["stage"] in stage_list)
                i = redo_from if redo_from <= i else i + 1
                continue
            i += 1
        self.stage = None
        rec = await self._record_run("succeeded", verdict=verdict)
        return DesignOutput(arg.ctx.run_id, rec.status, verdict, version_id, stages)

    def _take_overrides(self) -> list[dict]:
        """The overrides that arrived since the last call, as changes {question_id, stage, text, was, now}; an
        answer equal to the default changes nothing."""
        new, self.overrides_seen = self.overrides[self.overrides_seen :], len(self.overrides)
        out = []
        for a in new:
            q = self.asked.get(a.question_id)
            if q is None:
                continue
            texts = {str(x.get("id")): x.get("text", "") for x in q["questions"]}
            for qid, now in a.answers.items():
                was = q["defaults"].get(qid)
                if qid in texts and str(now) != str(was):
                    out.append(
                        {"question_id": qid, "stage": q["stage"], "text": texts[qid], "was": was, "now": now}
                    )
        return out

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
        self,
        stage: str,
        workspace: ArtifactRef,
        version_id: str,
        rounds: int,
        last_session: tuple[ArtifactRef | None, str | None] | None = None,
        redo: list[dict] | None = None,
    ) -> tuple[AgentStageOutput, int]:
        """One stage to its end: question rounds, key waits and rate-limit timers included. `redo`: this run
        of the stage follows a person's override of defaulted answers; it resumes the stage's last session."""
        session, session_id = last_session if redo and last_session else (None, None)
        answers: dict | None = None
        questions: list[dict] = []
        defaulted: list[str] = []
        resume: str | None = "override" if redo else None
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
                        defaulted=defaulted,
                        overrides=list(redo or []) if resume == "override" else [],
                        model=self.arg.model,
                        budget_usd=self.arg.budget_usd,
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
                    backoff = await self._rate_wait(det, backoff)
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
            answers, defaulted = await self._ask(stage, questions)
            resume = "answers"

    async def _rate_wait(self, det: dict, backoff: float) -> float:
        """A durable timer for a rate limit: the reset time when known (else the doubling backoff). A `retry`
        signal ends it early. Tells the parent EvalWorkflow, which starts no new trial meanwhile. Returns the
        next backoff."""
        reset = det.get("retry_after_s")
        if reset:  # a known reset time: wait for it (a durable timer, no polling)
            delay = min(float(reset), RATE_LIMIT_RESET_MAX_S)
        else:
            delay = backoff
            backoff = min(backoff * 2, RATE_LIMIT_BACKOFF_S[1])
        self.waiting = {"reason": "rate_limited", "seconds": delay}
        if self.arg.parent_workflow_id:
            try:
                await workflow.get_external_workflow_handle(self.arg.parent_workflow_id).signal(
                    SIGNAL_CHILD_RATE_LIMITED, delay
                )
            except Exception:  # noqa: BLE001, S110 - the parent is gone: nobody to tell
                pass
        seen = self.retries  # a `retry` signal (say, a new key) ends the wait early
        try:
            await workflow.wait_condition(lambda: self.retries > seen, timeout=timedelta(seconds=delay))
        except TimeoutError:
            pass
        self.waiting = None
        return backoff

    async def _simulate(self, stage: str, qid: str, questions: list[dict]) -> dict:
        """The simulated product person's answers, with the same LLM waits as an agent stage (a rate limit
        is a timer, an invalid key waits for `retry`)."""
        backoff = RATE_LIMIT_BACKOFF_S[0]
        while True:
            try:
                return await workflow.execute_activity(
                    A_SIMULATE_ANSWER,
                    SimulateInput(
                        ctx=self.arg.ctx,
                        question_id=qid,
                        stage=stage,
                        questions=questions,
                        persona=self.arg.persona,
                    ),
                    result_type=dict,
                    task_queue=Q_AGENT,
                    start_to_close_timeout=timedelta(minutes=10),
                    heartbeat_timeout=timedelta(seconds=60),
                    retry_policy=_agent_retry(),
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                    summary=f"simulate {stage}",
                )
            except ActivityError as e:
                if isinstance(e.cause, CancelledError):
                    raise
                typ, det = _failure(e)
                if typ == errors.LlmKeyInvalid:
                    await self._wait_for_key(stage, det.get("reason") or "key_invalid")
                elif typ == errors.LlmRateLimited:
                    backoff = await self._rate_wait(det, backoff)
                else:
                    raise _Failed(_error(e)) from None

    async def _ask(self, stage: str, questions: list[dict]) -> tuple[dict, list[str]]:
        """(answers, ids defaulted after the timeout). Policy: ADR 7.7 and the module docstring."""
        qid = str(workflow.uuid4())
        await self._question("ask", question_id=qid, stage=stage, questions=questions)
        self.open_question = qid
        self.waiting = {"reason": "question", "question_id": qid}
        self.asked[qid] = {"stage": stage, "questions": questions, "defaults": {}}
        if self.arg.answerer == "simulator":  # #19: the evals product-person simulator answers
            got = await self._simulate(stage, qid, questions)
            self.answers[qid] = QuestionAnswer(qid, got, "simulator")
        # all items explicitly non-critical and with a recommended option: the question may be defaulted
        defaultable = all(q.get("critical") is False and _recommended(q) is not None for q in questions)
        t = self.arg.question_timeout_s
        defaulted: list[str] = []
        try:
            await workflow.wait_condition(
                lambda: qid in self.answers, timeout=timedelta(seconds=t) if t else None
            )
        except TimeoutError:
            if defaultable:
                defaults = {str(q["id"]): _recommended(q) for q in questions}
                who = await self._question("auto_answered", question_id=qid, answers=defaults)
                if who == "auto":
                    self.asked[qid]["defaults"] = defaults
                    self.open_question = None
                    self.waiting = None
                    await self._question("resumed", question_id=qid)
                    return defaults, list(defaults)
                # a person answered first: their signal is on its way
            else:
                self.waiting = {"reason": "question", "question_id": qid, "critical": True}
                await self._question("critical_wait", question_id=qid)
            await workflow.wait_condition(lambda: qid in self.answers)
        self.open_question = None
        self.waiting = None
        await self._question("resumed", question_id=qid)
        return dict(self.answers[qid].answers), defaulted

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
