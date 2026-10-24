"""BuildWorkflow in the Temporal time-skipping test server, with fake activities (ADR 0001, 4.4 to 4.6).

Stage order, the hard-failure stop, parallel layout attempts with the cutoff and the select, retry on an infra
error, no retry on a design error, cancellation (the stage activity has finished its cancel before the run is
marked), and the workflow sandbox (the real worker's passthrough list). No database, S3 or engine tools: the
activities are fakes registered under the real names.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from hwplatform.contracts import (
    Q_ENGINE,
    QUEUES,
    BuildInput,
    BuildOutput,
    StageOut,
    errors,
)

temporalio = pytest.importorskip("temporalio")
from temporalio import activity  # noqa: E402
from temporalio.client import WorkflowFailureError  # noqa: E402
from temporalio.exceptions import ApplicationError, CancelledError  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from wf_fakes import REF, Fakes, Workers, arg, hang, out  # noqa: E402

from hwplatform.workers.main import build_workers  # noqa: E402
from hwplatform.workflows import BuildWorkflow  # noqa: E402


def run_wf(fakes: Fakes, a: BuildInput, *, cancel_after: str | None = None):
    """Run the workflow on workers for every queue (layout with 3 slots, so attempts run in parallel)."""

    async def go():
        env = await WorkflowEnvironment.start_time_skipping()
        try:
            slots = {q: {"activities": 3, "workflows": 4} for q in QUEUES}
            workers = build_workers(
                env.client,
                None,
                QUEUES,
                slots=slots,
                workflows=[BuildWorkflow],
                activities=fakes.acts,
                max_heartbeat_throttle_interval=timedelta(milliseconds=100),
                default_heartbeat_throttle_interval=timedelta(milliseconds=100),
            )
            async with Workers(workers):
                h = await env.client.start_workflow(
                    BuildWorkflow.run, a, id=a.ctx.workflow_id, task_queue=Q_ENGINE
                )
                if cancel_after:
                    for _ in range(400):
                        if fakes.calls[cancel_after]:
                            break
                        await asyncio.sleep(0.05)
                    await asyncio.sleep(0.3)
                    await h.cancel()
                return await h.result()
        finally:
            await env.shutdown()

    return asyncio.run(go())


def test_stage_order_and_success():
    f = Fakes()
    res = run_wf(f, arg({"until": "fab"}))
    assert isinstance(res, BuildOutput) and res.status == "succeeded" and res.verdict == "PASS"
    order = ["check", "sch", "sim", "layout_attempt.1", "layout_select", "post", "fab"]
    assert f.keys("running") == order and f.keys("succeeded") == order
    assert [s.seq for s in f.stages if s.status == "running"] == list(range(1, 8))
    assert [r.to for r in f.runs] == ["running", "succeeded"] and f.runs[-1].verdict == "PASS"
    assert set(res.stages) == set(order) and f.renders == 1


def test_hard_failure_stops_and_records_the_rest_skipped():
    async def bad(inp):
        return out("check", "fail", errors=2)

    f = Fakes({"check": bad})
    res = run_wf(f, arg({"until": "fab"}))
    assert res.status == "succeeded" and res.verdict == "FAIL"
    assert f.keys("running") == ["check"]
    assert f.keys("skipped") == ["sch", "sim", "layout_select", "post", "fab"]
    assert {s.reason for s in f.stages if s.status == "skipped"} == {"hard_failure:check"}
    assert f.calls["check"] == 1  # a fail verdict is a result, not an error: never retried


def test_parallel_attempts_cutoff_and_select():
    async def a1(inp):
        return out("layout_attempt", "fail", ok=False, drc_errors=3, unrouted=0)

    async def a2(inp):
        await asyncio.sleep(0.2)
        return out("layout_attempt", "fail", ok=False, drc_errors=2, unrouted=0)

    async def a3(inp):
        await asyncio.sleep(0.4)
        return out("layout_attempt", ok=True)

    seen = {}

    async def select(inp):
        seen.update(upstream=sorted(inp.upstream), params=dict(inp.params))
        return out("layout_select", ok=True)

    f = Fakes(
        {
            "layout_attempt.1": a1,
            "layout_attempt.2": a2,
            "layout_attempt.3": a3,
            "layout_attempt.4": hang,
            "layout_select": select,
        }
    )
    res = run_wf(f, arg({"until": "layout", "auto": True, "attempts": 4}))
    assert res.status == "succeeded"
    started = f.keys("running")
    assert started[:3] == ["check", "sch", "sim"] and started[3] == "layout_attempt.1"
    assert set(started[4:7]) == {"layout_attempt.2", "layout_attempt.3", "layout_attempt.4"}
    assert started[-1] == "layout_select"
    skipped = [s for s in f.stages if s.status == "skipped"]
    assert [(s.key, s.reason) for s in skipped] == [("layout_attempt.4", "cutoff")]
    assert f.cancelled == ["layout_attempt.4"]
    # the select ranks attempts 1..3 (the cutoff), with their results as upstream
    assert seen["upstream"] == ["layout_attempt.1", "layout_attempt.2", "layout_attempt.3", "sch", "sim"]
    assert seen["params"]["used"] == [1, 2, 3] and seen["params"]["cutoff"] == 3


def test_retry_on_an_infra_error():
    async def flaky(inp):
        if activity.info().attempt == 1:
            raise ApplicationError("ngspice died", type=errors.ToolCrashed)
        return StageOut(
            stage="sim",
            verdict="pass",
            cache_key="k",
            cached=False,
            result=REF,
            attempt=activity.info().attempt,
        )

    f = Fakes({"sim": flaky})
    res = run_wf(f, arg({"until": "sim"}))
    assert res.status == "succeeded" and f.calls["sim"] == 2
    done = [s for s in f.stages if s.key == "sim" and s.status == "succeeded"]
    assert done and done[0].attempt == 2


def test_no_retry_on_a_design_error_and_the_run_fails():
    async def invalid(inp):
        raise ApplicationError("design.yaml: unknown section", type=errors.DesignInvalid, non_retryable=True)

    f = Fakes({"check": invalid})
    with pytest.raises(WorkflowFailureError):
        run_wf(f, arg({"until": "fab"}))
    assert f.calls["check"] == 1
    assert [r.to for r in f.runs] == ["running", "failed"]
    assert f.runs[-1].error["type"] == errors.DesignInvalid and f.runs[-1].error["retryable"] is False
    assert [(s.key, s.status) for s in f.stages] == [("check", "running"), ("check", "failed")]
    assert f.renders == 1  # best-effort render before the run is marked failed


def test_cancel_stops_the_stage_then_marks_the_run():
    f = Fakes({"sim": hang})
    with pytest.raises(WorkflowFailureError) as ei:
        run_wf(f, arg({"until": "fab"}), cancel_after="sim")
    assert isinstance(ei.value.cause, CancelledError)
    assert f.cancelled == ["sim"]
    assert [r.to for r in f.runs] == ["running", "cancelled"]
    assert ("sim", "cancelled") in [(s.key, s.status) for s in f.stages]
    assert "post" not in f.keys()


def test_a_run_cancelled_before_it_started_does_nothing():
    f = Fakes()
    f.status = "cancelled"
    res = run_wf(f, arg({"until": "fab"}))
    assert res.status == "cancelled" and not f.stages and not f.calls
