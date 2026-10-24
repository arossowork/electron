"""DesignWorkflow in the Temporal test servers, with a fake agent_stage (no API call, database or S3).

Stage order and a design version per changed stage; a question -> wait -> answer -> continue round; an invalid
key -> waiting -> `retry` signal -> the stage again from its snapshot; the rate-limit timer; a worker restart
in the middle of a stage (the new attempt resumes from the heartbeated snapshot); cancellation; and the
`build` tool starting a linked BuildWorkflow (real BuildWorkflow, fake stage activities) through the real
PlatformBackend and ToolBox.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from collections import Counter
from datetime import timedelta

import pytest
from hwflow.artifacts import ArtifactRef, ref_to_dict

from hwplatform.contracts import (
    A_AGENT_STAGE,
    A_CREATE_DESIGN_VERSION,
    A_GET_DESIGN_VERSION,
    A_RECORD_QUESTION,
    A_RECORD_RUN,
    DESIGN_STAGES,
    Q_AGENT,
    Q_ENGINE,
    QUEUES,
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
    RunCtx,
    errors,
)

temporalio = pytest.importorskip("temporalio")
from temporalio import activity  # noqa: E402
from temporalio.client import WorkflowFailureError  # noqa: E402
from temporalio.common import SearchAttributeKey  # noqa: E402
from temporalio.exceptions import ApplicationError, CancelledError  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from wf_fakes import REF, Fakes, Workers, hang  # noqa: E402

from hwplatform.activities.agent import snapshot_details, start_point  # noqa: E402
from hwplatform.agent.backend import PlatformBackend  # noqa: E402
from hwplatform.agent.tools import ToolBox  # noqa: E402
from hwplatform.workers.main import build_workers  # noqa: E402
from hwplatform.workflows import BuildWorkflow, DesignWorkflow, EngineToolWorkflow  # noqa: E402

WS0 = ArtifactRef("0" * 64, 10, "application/vnd.hwflow.tree+json", "project")


def ws(n: int) -> ArtifactRef:
    return ArtifactRef(f"{n:x}" * 64, 10 + n, "application/vnd.hwflow.tree+json", "workspace")


class DesignFakes(Fakes):
    """The build fakes plus the design activities; `agent[stage]` is a list of async fns (inp, n) -> output,
    one per call of that stage (the last one repeats)."""

    def __init__(self, agent=None, stage=None):
        super().__init__(stage)
        self.agent = agent or {}
        self.agent_calls: list[AgentStageInput] = []
        self.agent_n: Counter = Counter()
        self.questions: list[RecordQuestion] = []
        self.versions: list[DesignVersionInput] = []
        self.starts: list = []
        self.scheduled: list = []
        self.run_status: dict[str, str] = {}

    @activity.defn(name=A_RECORD_RUN)
    async def record_run(self, r: RecordRun) -> RecordedRun:
        """As Fakes.record_run, with a status per run (the design run and its builds)."""
        self.runs.append(r)
        allowed = {
            "running": {"queued"},
            "succeeded": {"running"},
            "failed": {"queued", "running"},
            "cancelled": {"queued", "running"},
        }
        cur = self.run_status.get(r.ctx.run_id, "queued")
        if cur in allowed[r.to]:
            self.run_status[r.ctx.run_id] = r.to
            return RecordedRun(r.to, True)
        return RecordedRun(cur, False)

    @activity.defn(name=A_RECORD_QUESTION)
    async def record_question(self, r: RecordQuestion) -> str:
        self.questions.append(r)
        return r.question_id or ""

    @activity.defn(name=A_GET_DESIGN_VERSION)
    async def get_design_version(self, r: DesignVersionInput) -> DesignVersionOut:
        return DesignVersionOut(r.design_version_id, "a" * 64, REF, WS0)

    @activity.defn(name=A_CREATE_DESIGN_VERSION)
    async def create_design_version(self, r: DesignVersionInput) -> DesignVersionOut:
        self.versions.append(r)
        return DesignVersionOut(f"dv{len(self.versions)}", r.workspace.sha256, REF, r.workspace, True)

    @activity.defn(name=A_AGENT_STAGE)
    async def agent_stage(self, inp: AgentStageInput) -> AgentStageOutput:
        self.agent_calls.append(inp)
        self.scheduled.append(activity.info().current_attempt_scheduled_time)
        n = self.agent_n[inp.stage]
        self.agent_n[inp.stage] += 1
        self.starts.append((inp.stage, start_point(inp, list(activity.info().heartbeat_details or []))))
        fns = self.agent.get(inp.stage) or [done]
        return await fns[min(n, len(fns) - 1)](inp, n)

    def acts(self, queue: str) -> list:
        if queue == Q_AGENT:
            return [self.agent_stage]
        acts = super().acts(queue)
        if queue == Q_ENGINE:
            acts += [self.record_question, self.get_design_version, self.create_design_version]
        return acts

    def ops(self) -> list[str]:
        return [q.op for q in self.questions]


async def done(inp: AgentStageInput, n: int, *, changed: bool = True, verdict=None) -> AgentStageOutput:
    w = ws(DESIGN_STAGES.index(inp.stage) + 1) if changed else inp.workspace
    return AgentStageOutput("done", w, None, f"sess-{inp.stage}", summary=f"{inp.stage} ok", verdict=verdict)


async def unchanged(inp, n):
    return await done(inp, n, changed=False)


async def asks(inp, n):
    return AgentStageOutput(
        "needs_answers",
        ws(50),
        ArtifactRef("e" * 64, 5, "application/x-hwflow-agent-session+tar.gz", "session"),
        "sess-q",
        questions=[{"id": "q1", "text": "Battery?", "options": ["CR2032", "AA"]}],
    )


def key_error(reason: str):
    async def fn(inp, n):
        raise ApplicationError(
            "key",
            {**snapshot_details(ws(60), None, "sess-k"), "reason": reason},
            type=errors.LlmKeyInvalid,
            non_retryable=True,
        )

    return fn


async def rate_limited(inp, n):
    raise ApplicationError(
        "rl",
        {**snapshot_details(ws(70), None, "sess-r"), "retry_after_s": 90},
        type=errors.LlmRateLimited,
        non_retryable=True,
    )


def design_arg(stages=None, **kw) -> DesignInput:
    rid = str(uuid.uuid4())
    return DesignInput(
        RunCtx(str(uuid.uuid4()), str(uuid.uuid4()), rid, f"design-{rid}"),
        "dv0",
        "A coin-cell thermometer.",
        stages=list(stages or []),
        **kw,
    )


async def _env(local: bool):
    if local:
        try:
            return await WorkflowEnvironment.start_local(
                search_attributes=[
                    SearchAttributeKey.for_keyword(k)
                    for k in ("HwTenantId", "HwProjectId", "HwRunId", "HwRunKind")
                ]
            )
        except Exception as e:  # no network for the one-time dev server download
            pytest.skip(f"Temporal dev server unavailable: {type(e).__name__}: {e}")
    return await WorkflowEnvironment.start_time_skipping()


def workers_for(env, fakes, **kw):
    return Workers(
        build_workers(
            env.client,
            None,
            QUEUES,
            slots={q: {"activities": 3, "workflows": 4} for q in QUEUES},
            workflows=[DesignWorkflow, BuildWorkflow, EngineToolWorkflow],
            activities=fakes.acts,
            max_heartbeat_throttle_interval=timedelta(milliseconds=100),
            default_heartbeat_throttle_interval=timedelta(milliseconds=100),
            **kw,
        )
    )


async def wait_for(pred, what: str, n: int = 400):
    for _ in range(n):
        if pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def run(fakes, a, drive=None, *, local=False):
    """Start the design workflow; `drive(env, handle)` acts on it while it runs; returns its result."""

    async def go():
        env = await _env(local)
        try:
            async with workers_for(env, fakes):
                h = await env.client.start_workflow(
                    DesignWorkflow.run, a, id=a.ctx.workflow_id, task_queue=Q_ENGINE
                )
                if drive:
                    await drive(env, h)
                return await h.result()
        finally:
            await env.shutdown()

    return asyncio.run(go())


def test_stage_order_and_versions():
    f = DesignFakes({"review": [unchanged]})
    res = run(f, design_arg())
    assert isinstance(res, DesignOutput) and res.status == "succeeded"
    order = list(DESIGN_STAGES)
    assert order == ["requirements", "partition", "parts", "circuit", "review", "layout", "firmware"]
    assert [i.stage for i in f.agent_calls] == order
    assert f.keys("running") == order and f.keys("succeeded") == order
    assert [s.seq for s in f.stages if s.status == "running"] == list(range(1, 8))
    assert [r.to for r in f.runs] == ["running", "succeeded"]
    # every stage but the unchanged review saved a version, each the parent of the next
    assert [v.message for v in f.versions] == [f"agent: {s}" for s in order if s != "review"]
    assert [v.parent_id for v in f.versions] == ["dv0", "dv1", "dv2", "dv3", "dv4", "dv5"]
    assert res.design_version_id == "dv6"
    # each stage starts from the workspace the stage before left
    assert f.agent_calls[1].workspace == ws(1) and f.agent_calls[5].workspace == ws(4)
    assert all(c.resume is None and c.brief == "A coin-cell thermometer." for c in f.agent_calls)
    done_rec = [s for s in f.stages if s.status == "succeeded"][0]
    assert done_rec.out.result == ws(1) and done_rec.out.summary["summary"] == "requirements ok"


def test_question_wait_answer_continue():
    f = DesignFakes({"requirements": [asks, done]})
    a = design_arg(["requirements", "partition"])

    async def drive(env, h):
        await wait_for(lambda: "ask" in f.ops(), "the question")
        await asyncio.sleep(0.5)
        st = await h.query("state")
        assert st["waiting"]["reason"] == "question" and len(f.agent_calls) == 1  # waiting, not polling
        qid = f.questions[0].question_id
        await h.signal(DesignWorkflow.answer_question, QuestionAnswer("other", {"q1": "x"}, "u"))  # ignored
        await asyncio.sleep(0.3)
        assert len(f.agent_calls) == 1
        await h.signal(DesignWorkflow.answer_question, QuestionAnswer(qid, {"q1": "AA"}, "user-1"))

    res = run(f, a, drive)
    assert res.status == "succeeded"
    assert f.ops() == ["ask", "resumed"]
    q = f.questions[0]
    assert q.stage == "requirements" and q.questions == [
        {"id": "q1", "text": "Battery?", "options": ["CR2032", "AA"]}
    ]
    second = f.agent_calls[1]
    assert second.stage == "requirements" and second.resume == "answers" and second.answers == {"q1": "AA"}
    assert second.session_id == "sess-q" and second.session.sha256 == "e" * 64 and second.round == 1
    assert second.workspace == ws(50) and second.questions == q.questions
    # the workspace was saved as a version when the question was asked
    assert f.versions[0].message == "agent: requirements, questions round 1" and f.versions[
        0
    ].workspace == ws(50)
    assert [c.stage for c in f.agent_calls] == ["requirements", "requirements", "partition"]
    assert res.stages["requirements"]["rounds"] == 1


def test_question_timeout_when_configured():
    f = DesignFakes({"requirements": [asks]})
    a = design_arg(["requirements"], question_timeout_s=3600)
    with pytest.raises(WorkflowFailureError) as ei:
        run(f, a)
    assert ei.value.cause.type == errors.QuestionExpired
    assert f.ops() == ["ask", "expired"] and [r.to for r in f.runs] == ["running", "failed"]
    assert f.keys("failed") == ["requirements"]


def test_invalid_key_waits_for_the_retry_signal():
    f = DesignFakes({"circuit": [key_error("key_invalid"), done]})
    a = design_arg(["circuit", "review"])

    async def drive(env, h):
        await wait_for(lambda: "key_wait" in f.ops(), "the key wait")
        t0 = await env.get_current_time()
        await asyncio.sleep(1.0)
        assert len(f.agent_calls) == 1 and (await h.query("state"))["waiting"]["reason"] == "llm_key_invalid"
        await h.signal(DesignWorkflow.retry)
        await wait_for(lambda: len(f.agent_calls) >= 2, "the second session")
        assert (await env.get_current_time()) - t0 < timedelta(hours=1)  # the signal, not a timer, woke it

    res = run(f, a, drive)
    assert res.status == "succeeded" and f.ops() == ["key_wait", "key_resumed"]
    again = f.agent_calls[1]
    assert again.stage == "circuit" and again.resume == "retry" and again.workspace == ws(60)
    assert again.session_id == "sess-k" and again.answers is None


def test_no_key_waits_then_starts_fresh():
    f = DesignFakes({"requirements": [key_error("no_key"), done]})

    async def drive(env, h):
        await wait_for(lambda: "key_wait" in f.ops(), "the key wait")
        await h.signal(DesignWorkflow.retry)

    res = run(f, design_arg(["requirements"]), drive)
    assert res.status == "succeeded" and f.agent_calls[1].resume is None  # no session ran: a fresh start


def test_key_wait_gives_up_after_the_limit():
    f = DesignFakes({"requirements": [key_error("key_exhausted")]})
    with pytest.raises(WorkflowFailureError) as ei:
        run(f, design_arg(["requirements"]))  # the time-skipping server jumps the 72 h timer
    assert ei.value.cause.type == errors.LlmKeyInvalid and [r.to for r in f.runs] == ["running", "failed"]


def test_rate_limit_waits_on_a_timer():
    f = DesignFakes({"parts": [rate_limited, rate_limited, done]})
    res = run(f, design_arg(["parts"]))
    assert res.status == "succeeded" and len(f.agent_calls) == 3
    gaps = [(b - a).total_seconds() for a, b in zip(f.scheduled, f.scheduled[1:], strict=False)]
    assert all(90 <= g < 120 for g in gaps), gaps  # retry_after_s = 90, on a workflow timer
    assert all(c.resume == "retry" and c.workspace == ws(70) for c in f.agent_calls[1:])
    assert f.ops() == []  # a rate limit does not set the run waiting for a person


def test_worker_restart_resumes_from_the_heartbeated_snapshot():
    """The first attempt heartbeats a snapshot and is cut by a worker shutdown; the attempt on the new
    worker starts from that snapshot (resume "restart"), and the finished stage before it is not run again."""
    beat = asyncio.Event()

    async def long_stage(inp, n):
        if n == 0:
            activity.heartbeat(snapshot_details(ws(80), None, "sess-crash", "working"))
            beat.set()
            while True:
                await asyncio.sleep(0.05)
                activity.heartbeat(snapshot_details(ws(80), None, "sess-crash", "working"))
        return await done(inp, n)

    f = DesignFakes({"partition": [long_stage]})
    a = design_arg(["requirements", "partition"])

    async def go():
        env = await WorkflowEnvironment.start_time_skipping()
        try:
            # no sticky cache: the new worker replays the history (the test server does not move a sticky
            # workflow task off a dead worker the way the real server does after its schedule-to-start timeout)
            async with workers_for(
                env, f, graceful_shutdown_timeout=timedelta(seconds=0), max_cached_workflows=0
            ):
                h = await env.client.start_workflow(
                    DesignWorkflow.run, a, id=a.ctx.workflow_id, task_queue=Q_ENGINE
                )
                await asyncio.wait_for(beat.wait(), 20)
                await asyncio.sleep(0.5)  # the heartbeat reaches the server
            async with workers_for(env, f, max_cached_workflows=0):  # a new worker process
                return await h.result()
        finally:
            await env.shutdown()

    res = asyncio.run(go())
    assert res.status == "succeeded"
    assert [c.stage for c in f.agent_calls] == ["requirements", "partition", "partition"]
    stage, start = f.starts[-1]
    assert stage == "partition" and start.resume == "restart" and start.workspace == ws(80)
    assert start.session_id == "sess-crash"


def test_cancel_marks_the_open_question_and_the_run():
    f = DesignFakes({"requirements": [asks]})
    a = design_arg(["requirements"])

    async def drive(env, h):
        await wait_for(lambda: "ask" in f.ops(), "the question")
        await h.cancel()

    with pytest.raises(WorkflowFailureError) as ei:
        run(f, a, drive)
    assert isinstance(ei.value.cause, CancelledError)
    assert f.ops() == ["ask", "cancelled"] and [r.to for r in f.runs] == ["running", "cancelled"]
    assert f.keys("cancelled") == ["requirements"]


# -- the build tool: a linked BuildWorkflow through the real backend ----------


class _Store:
    """Enough of S3ArtifactStore for the backend: the report and status.json of a fake build."""

    def open(self, sha256):
        return io.BytesIO(b"# fake REPORT\nverdict PASS\n")


class LinkedBackend(PlatformBackend):
    """The real PlatformBackend with the database and S3 parts stubbed (design version, run row)."""

    def __init__(self, client, ctx):
        super().__init__(None, _Store(), client, ctx)
        self.rows: list[str] = []

    async def save_version(self, ws_path, message):
        from hwflow.runs import DesignVersionRef

        return DesignVersionRef("dv-b", "b" * 64, REF, None, REF)

    async def _run_row(self, dv, options, ohash, wf_id):
        rid = str(uuid.uuid4())
        self.rows.append(rid)
        return rid


def test_build_tool_starts_a_linked_build_and_cancel_reaches_it(tmp_path):
    """`build` starts BuildWorkflow under the ADR 4.4 ID (search attributes, parent run), waits for it and
    returns its report; a second call with the same files joins it. Cancelling the design run while a build
    runs cancels the agent session, which cancels the build."""
    holder: dict = {}

    def agent_with_build(cancel_test: bool):
        async def fn(inp: AgentStageInput, n):
            backend = LinkedBackend(holder["client"], inp.ctx)
            box = ToolBox(tmp_path / inp.stage, backend, stage=inp.stage)
            box.ws.mkdir(parents=True, exist_ok=True)
            task = asyncio.ensure_future(box.call("build", {"until": "sim"}))
            try:
                while not task.done():
                    activity.heartbeat("building")
                    await asyncio.sleep(0.05)
                r = task.result()
                holder["text"] = r["content"][0]["text"]
                holder["second"] = (await box.call("build", {"until": "sim"}))["content"][0]["text"]
            except asyncio.CancelledError:
                task.cancel()
                await asyncio.shield(backend.cancel_builds(list(box.builds) or [holder.get("wf", "")]))
                raise
            out = await done(inp, n, verdict=box.verdict)
            return AgentStageOutput(
                out.outcome, out.workspace, None, None, builds=list(box.builds), verdict=box.verdict
            )

        return fn

    async def go():
        env = await _env(local=True)
        holder["client"] = env.client
        try:
            # 1. the linked build runs to its end
            f = DesignFakes({"circuit": [agent_with_build(False)]})
            async with workers_for(env, f):
                a = design_arg(["circuit"])
                res = await env.client.execute_workflow(
                    DesignWorkflow.run, a, id=a.ctx.workflow_id, task_queue=Q_ENGINE
                )
            assert res.status == "succeeded" and res.verdict == "PASS"
            assert "verdict PASS" in holder["text"] and "fake REPORT" in holder["text"]
            builds = [
                w
                async for w in env.client.list_workflows(
                    f"WorkflowType = 'BuildWorkflow' AND HwTenantId = '{a.ctx.tenant_id}'"
                )
            ]
            # the second `build` of the same files after the first ended: the same workflow ID, a new run
            # (ALLOW_DUPLICATE); while one runs, USE_EXISTING joins it (test_gateway_temporal)
            assert len(builds) == 2 and len({b.id for b in builds}) == 1
            assert builds[0].id.startswith(f"build-{a.ctx.tenant_id}-{a.ctx.project_id}-bbbb")
            assert "verdict PASS" in holder["second"]
            desc = await env.client.get_workflow_handle(builds[0].id).describe()
            assert desc.typed_search_attributes.get(SearchAttributeKey.for_keyword("HwRunKind")) == "build"
            assert [r.to for r in f.runs if r.ctx.workflow_id == builds[0].id] == ["running", "succeeded"] * 2
            build_in = next(r for r in f.runs if r.ctx.workflow_id == builds[0].id).ctx
            assert build_in.run_id in {s.ctx.run_id for s in f.stages}

            # 2. cancel the design run while its build runs: the build is cancelled too
            f2 = DesignFakes({"circuit": [agent_with_build(True)]}, stage={"sim": hang})
            async with workers_for(env, f2):
                a2 = design_arg(["circuit"])
                h = await env.client.start_workflow(
                    DesignWorkflow.run, a2, id=a2.ctx.workflow_id, task_queue=Q_ENGINE
                )
                await wait_for(lambda: f2.calls["sim"] > 0, "the build's sim stage", 600)
                bwf = next(r.ctx.workflow_id for r in f2.runs if r.ctx.workflow_id.startswith("build-"))
                await h.cancel()
                with pytest.raises(WorkflowFailureError) as di:
                    await h.result()
                assert isinstance(di.value.cause, CancelledError)
                with pytest.raises(WorkflowFailureError) as ei:
                    await env.client.get_workflow_handle(bwf).result()
                assert isinstance(ei.value.cause, CancelledError)
            assert f2.cancelled == ["sim"] and [
                s.key for s in f2.stages if s.status == "cancelled" and s.ctx.run_id == a2.ctx.run_id
            ] == ["circuit"]
            assert [r.to for r in f2.runs if r.ctx.workflow_id == bwf] == ["running", "cancelled"]
            assert [r.to for r in f2.runs if r.ctx.workflow_id == a2.ctx.workflow_id] == [
                "running",
                "cancelled",
            ]
        finally:
            await env.shutdown()

    asyncio.run(go())


def test_ids_and_refs_are_plain_data():
    assert ref_to_dict(ws(1))["sha256"] == "1" * 64
