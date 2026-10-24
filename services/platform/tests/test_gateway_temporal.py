"""TemporalGateway against a real Temporal server (the temporalio dev server, downloaded on first use), with the
real BuildWorkflow on the engine queue and fake activities (no database, S3 or engine tools).

Proves the gateway's wire behaviour: workflow ID, task queue, argument dataclass, search attributes, the ID
conflict policy USE_EXISTING (a second start while the build runs is the same run, not a second one), cancel
(the build records `cancelled` and ends cancelled), and the real DesignWorkflow (fake activities) with the answer
and retry signals. Skipped when the dev server cannot start (no network).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from hwplatform.contracts import QUEUES, DesignOutput, QuestionAnswer
from hwplatform.gateway import GatewayError, TemporalGateway, WorkflowNotFound
from hwplatform.settings import Settings

temporalio = pytest.importorskip("temporalio")
from temporalio.client import Client, WorkflowFailureError  # noqa: E402
from temporalio.common import SearchAttributeKey  # noqa: E402
from temporalio.exceptions import CancelledError  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from test_workflow_design import DesignFakes, asks, design_arg, done, key_error, wait_for, workers_for  # noqa: E402
from wf_fakes import Fakes, Workers, arg, hang  # noqa: E402

from hwplatform.workers.main import build_workers  # noqa: E402
from hwplatform.workflows import BuildWorkflow  # noqa: E402


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
            async with Workers(workers):
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

                with pytest.raises(WorkflowNotFound):
                    await gw.cancel("build-does-not-exist")
                with pytest.raises(WorkflowNotFound):
                    await gw.signal_answer("build-does-not-exist", QuestionAnswer("q", {}, "u"))
            # the real DesignWorkflow: start, a question, the answer signal, an invalid key, the retry signal
            f = DesignFakes({"requirements": [asks, done], "partition": [key_error("key_invalid"), done]})
            async with workers_for(env, f):
                a = design_arg(["requirements", "partition"])
                dw = a.ctx.workflow_id
                await gw.start_design(dw, a)
                await wait_for(lambda: "ask" in f.ops(), "the question")
                await gw.signal_answer(dw, QuestionAnswer(f.questions[0].question_id, {"q1": "AA"}, "u1"))
                await wait_for(lambda: "key_wait" in f.ops(), "the key wait")
                await gw.signal_retry(dw)
                res = await env.client.get_workflow_handle(dw, result_type=DesignOutput).result()
            assert res.status == "succeeded" and f.ops() == ["ask", "resumed", "key_wait", "key_resumed"]
            assert f.agent_calls[1].answers == {"q1": "AA"} and f.agent_calls[1].resume == "answers"
            running = [w async for w in env.client.list_workflows(f"WorkflowId = '{dw}'")]
            assert running and running[0].workflow_type == "DesignWorkflow"
            with pytest.raises(WorkflowNotFound):
                await gw.signal_retry("design-does-not-exist")
            bad = TemporalGateway(Settings(temporal_address="127.0.0.1:1"))
            with pytest.raises(GatewayError):
                await bad.start_build("x", arg({}))
        finally:
            await env.shutdown()

    asyncio.run(go())


def test_client_import_is_available():
    assert Client is not None
