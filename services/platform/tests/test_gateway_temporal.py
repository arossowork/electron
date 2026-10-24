"""TemporalGateway against a real Temporal server (the temporalio dev server, downloaded on first use).

The workflows are stand-ins named like the ADR's (BuildWorkflow / answer_question): the real ones are issue #9.
This proves the gateway's wire behaviour: workflow ID, task queue, argument dataclass, ID conflict policy
USE_EXISTING, cancel and the answer signal. Skipped when the dev server cannot start (no network).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict

import pytest

from hwplatform.contracts import BuildInput, QuestionAnswer, Ref, RunCtx
from hwplatform.gateway import GatewayError, TemporalGateway, WorkflowNotFound
from hwplatform.settings import Settings

temporalio = pytest.importorskip("temporalio")
from temporalio import workflow  # noqa: E402
from temporalio.client import Client, WorkflowFailureError  # noqa: E402
from temporalio.common import SearchAttributeKey  # noqa: E402
from temporalio.exceptions import CancelledError  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

QUEUE = "hwflow-engine-test"


@workflow.defn(name="BuildWorkflow")
class FakeBuild:
    def __init__(self):
        self.answers: list[dict] = []

    @workflow.run
    async def run(self, arg: BuildInput) -> dict:
        await workflow.wait_condition(lambda: bool(self.answers), timeout=None)
        return {"run_id": arg.ctx.run_id, "answers": self.answers}

    @workflow.signal(name="answer_question")
    def answer(self, a: QuestionAnswer) -> None:
        self.answers.append(asdict(a))


def _input(wf_id: str) -> BuildInput:
    return BuildInput(
        ctx=RunCtx("t", "p", str(uuid.uuid4()), wf_id),
        design_version_id="dv",
        design=Ref("a" * 64, 1, "application/yaml", "design.yaml"),
        project=Ref("b" * 64, 2, "application/vnd.hwflow.tree+json", "project"),
        options={"until": "sim"},
    )


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
            gw = TemporalGateway(Settings(temporal_address=address, temporal_engine_queue=QUEUE))
            async with Worker(env.client, task_queue=QUEUE, workflows=[FakeBuild]):
                wf = f"build-{uuid.uuid4()}"
                arg = _input(wf)
                await gw.start_build(wf, arg)
                await gw.start_build(wf, arg)  # same ID while running: USE_EXISTING, not an error
                running = [w async for w in env.client.list_workflows(f"WorkflowId = '{wf}'")]
                assert len(running) == 1 and running[0].task_queue == QUEUE
                await gw.signal_answer(wf, QuestionAnswer("q1", {"a": 1}, "u1"))
                res = await env.client.get_workflow_handle(wf).result()
                assert res["answers"] == [{"question_id": "q1", "answers": {"a": 1}, "answered_by": "u1"}]
                assert res["run_id"] == arg.ctx.run_id

                wf2 = f"build-{uuid.uuid4()}"
                await gw.start_build(wf2, _input(wf2))
                await gw.cancel(wf2)
                with pytest.raises(WorkflowFailureError) as ei:
                    await env.client.get_workflow_handle(wf2).result()
                assert isinstance(ei.value.cause, CancelledError)
                with pytest.raises(WorkflowNotFound):
                    await gw.cancel("build-does-not-exist")
                with pytest.raises(WorkflowNotFound):
                    await gw.signal_answer("build-does-not-exist", QuestionAnswer("q", {}, "u"))
            bad = TemporalGateway(Settings(temporal_address="127.0.0.1:1"))
            with pytest.raises(GatewayError):
                await bad.start_build("x", _input("x"))
        finally:
            await env.shutdown()

    asyncio.run(go())


def test_client_import_is_available():
    assert Client is not None
