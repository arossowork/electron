"""TemporalGateway against a real Temporal server (the temporalio dev server, downloaded on first use), with the
real BuildWorkflow on the engine queue and fake activities (no database, S3 or engine tools).

Proves the gateway's wire behaviour: workflow ID, task queue, argument dataclass, search attributes, the ID
conflict policy USE_EXISTING (a second start while the build runs is the same run, not a second one), cancel
(the build records `cancelled` and ends cancelled) and the answer signal. DesignWorkflow is #15, so the signal goes
to a stand-in named like it. Skipped when the dev server cannot start (no network).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict
from datetime import timedelta

import pytest

from hwplatform.contracts import QUEUES, DesignInput, QuestionAnswer, RunCtx
from hwplatform.gateway import GatewayError, TemporalGateway, WorkflowNotFound
from hwplatform.settings import Settings

temporalio = pytest.importorskip("temporalio")
from temporalio import workflow  # noqa: E402
from temporalio.client import Client, WorkflowFailureError  # noqa: E402
from temporalio.common import SearchAttributeKey  # noqa: E402
from temporalio.exceptions import CancelledError  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402
from wf_fakes import Fakes, Workers, arg, hang  # noqa: E402

from hwplatform.workers.main import build_workers  # noqa: E402
from hwplatform.workflows import BuildWorkflow  # noqa: E402


@workflow.defn(name="DesignWorkflow")
class FakeDesign:
    def __init__(self):
        self.answers: list[dict] = []

    @workflow.run
    async def run(self, arg: DesignInput) -> dict:
        await workflow.wait_condition(lambda: bool(self.answers), timeout=None)
        return {"run_id": arg.ctx.run_id, "answers": self.answers}

    @workflow.signal(name="answer_question")
    def answer(self, a: QuestionAnswer) -> None:
        self.answers.append(asdict(a))


def test_gateway_start_signal_cancel_against_temporal():
    async def go():
        try:
            env = await WorkflowEnvironment.start_local(
                search_attributes=[
                    SearchAttributeKey.for_keyword(k)
                    for k in ("HwTenantId", "HwProjectId", "HwRunId", "HwRunKind")
                ]
            )
        except Exception as e:  # no network for the one-time dev server download
            pytest.skip(f"Temporal dev server unavailable: {type(e).__name__}: {e}")
        try:
            address = env.client.service_client.config.target_host
            gw = TemporalGateway(Settings(temporal_address=address))
            fakes = Fakes({"check": hang})
            workers = build_workers(
                env.client,
                None,
                QUEUES,
                workflows=[BuildWorkflow],
                activities=fakes.acts,
                max_heartbeat_throttle_interval=timedelta(milliseconds=200),
                default_heartbeat_throttle_interval=timedelta(milliseconds=200),
            )
            design_q = f"design-{uuid.uuid4()}"
            async with Workers(workers), Worker(env.client, task_queue=design_q, workflows=[FakeDesign]):
                a = arg({"until": "sim"})
                wf = a.ctx.workflow_id
                await gw.start_build(wf, a)
                await gw.start_build(wf, a)  # same ID while running: USE_EXISTING, not an error
                for _ in range(100):  # visibility is eventually consistent
                    running = [w async for w in env.client.list_workflows(f"WorkflowId = '{wf}'")]
                    if running:
                        break
                    await asyncio.sleep(0.05)
                assert len(running) == 1 and running[0].task_queue == "hwflow-engine"
                assert running[0].workflow_type == "BuildWorkflow"
                for _ in range(200):  # the build reached its first stage
                    if fakes.calls["check"]:
                        break
                    await asyncio.sleep(0.05)
                assert fakes.calls["check"] == 1 and [r.to for r in fakes.runs] == ["running"]
                await gw.cancel(wf)
                with pytest.raises(WorkflowFailureError) as ei:
                    await env.client.get_workflow_handle(wf).result()
                assert isinstance(ei.value.cause, CancelledError)
                assert [r.to for r in fakes.runs] == ["running", "cancelled"]
                assert fakes.cancelled == ["check"]

                gw_design = TemporalGateway(
                    Settings(temporal_address=address, temporal_engine_queue=design_q)
                )
                dw = f"design-{uuid.uuid4()}"
                rid = str(uuid.uuid4())
                await gw_design.start_design(dw, DesignInput(RunCtx("t", "p", rid, dw), "dv", "a brief"))
                await gw_design.signal_answer(dw, QuestionAnswer("q1", {"a": 1}, "u1"))
                res = await env.client.get_workflow_handle(dw).result()
                assert res == {
                    "run_id": rid,
                    "answers": [{"question_id": "q1", "answers": {"a": 1}, "answered_by": "u1"}],
                }
                with pytest.raises(WorkflowNotFound):
                    await gw.cancel("build-does-not-exist")
                with pytest.raises(WorkflowNotFound):
                    await gw.signal_answer("build-does-not-exist", QuestionAnswer("q", {}, "u"))
            bad = TemporalGateway(Settings(temporal_address="127.0.0.1:1"))
            with pytest.raises(GatewayError):
                await bad.start_build("x", arg({}))
        finally:
            await env.shutdown()

    asyncio.run(go())


def test_client_import_is_available():
    assert Client is not None
