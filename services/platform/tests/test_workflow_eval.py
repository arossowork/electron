"""EvalWorkflow in the Temporal test servers (issue #19): real EvalWorkflow, DesignWorkflow and BuildWorkflow with
fake activities (no API call, database or S3) and a deterministic simulated product person.

The fan-out bound, trials scored with the real score card, a failing trial that does not fail the evaluation, the
comparison with a baseline, questions answered by the simulator (and the hidden specification kept away from the
agent), a rate limit that pauses new trials, and cancellation that reaches the child design runs.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from datetime import timedelta

import pytest
from hwharness.evals.cards import Evidence, score_card
from hwharness.evals.report import eval_document, render_markdown
from hwharness.evals.simulator import deterministic_answers, parse_answers

from hwplatform.contracts import (
    A_AGENT_STAGE,
    A_FINISH_EVAL,
    A_PREPARE_EVAL_TRIAL,
    A_PREPARE_SCORE_BUILD,
    A_SCORE_TRIAL,
    A_SIMULATE_ANSWER,
    Q_AGENT,
    Q_ENGINE,
    QUEUES,
    AgentStageInput,
    AgentStageOutput,
    BuildInput,
    DesignInput,
    EvalInput,
    EvalOutput,
    FinishEvalInput,
    FinishEvalOut,
    PreparedTrial,
    PrepareTrial,
    RunCtx,
    ScoreBuildPlan,
    ScoreTrialInput,
    SimulateInput,
    TrialResult,
    errors,
)

temporalio = pytest.importorskip("temporalio")
from temporalio import activity  # noqa: E402
from temporalio.client import WorkflowFailureError  # noqa: E402
from temporalio.exceptions import ApplicationError, CancelledError  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from test_workflow_design import DesignFakes, done, ws  # noqa: E402
from wf_fakes import REF, Workers  # noqa: E402

from hwplatform.activities.agent import snapshot_details  # noqa: E402
from hwplatform.workers.main import build_workers  # noqa: E402
from hwplatform.workflows import BuildWorkflow, DesignWorkflow, EngineToolWorkflow, EvalWorkflow  # noqa: E402

TWO_STAGES = ["requirements", "partition"]


def fixture(fid: str, **kw) -> dict:
    return {
        "id": fid,
        "mode": "full",
        "brief": f"Brief of {fid}.",
        "hidden_spec": f"- the battery is a CR2032\n- cost below 5 USD for {fid}",
        "planted_conflicts": [{"id": "c1", "keywords": [["battery"], ["cost"]]}],
        "expected_requirements": [["battery"], ["cost"]],
        "limits": {"max_area_mm2": 600, "max_cost_usd": 5},
        **kw,
    }


GOOD = {
    "erc_errors": 0,
    "drc_errors": 0,
    "unrouted": 0,
    "sim_pass_ratio": 1.0,
    "post_pass_ratio": 1.0,
    "req_total": 4,
    "req_unverified": 0,
    "area_mm2": 400,
    "cost_usd": 3.0,
}


class EvalFakes(DesignFakes):
    """The design fakes plus the evaluation activities. Trials are told apart by the brief."""

    def __init__(
        self,
        *,
        metrics=None,
        plan_build=False,
        bad_prepare=(),
        fail_design=(),
        baseline=None,
        agent_script=None,
        hold_s=0.15,
    ):
        super().__init__()
        self.metrics = metrics or {}
        self.plan_build = plan_build
        self.bad_prepare = set(bad_prepare)
        self.fail_design = set(fail_design)
        self.baseline_doc = baseline
        self.agent_script = agent_script or {}
        self.hold_s = hold_s
        self.prepared: list[PrepareTrial] = []
        self.prep_times: list = []
        self.designs: dict[str, DesignInput] = {}
        self.sim: list[SimulateInput] = []
        self.scored: list[ScoreTrialInput] = []
        self.finished: list[FinishEvalInput] = []
        self.docs: list[dict] = []
        self.cur = 0
        self.peak = 0
        self.stage_calls: Counter = Counter()

    # -- the trial plumbing
    @activity.defn(name=A_PREPARE_EVAL_TRIAL)
    async def prepare_eval_trial(self, inp: PrepareTrial) -> PreparedTrial:
        self.prepared.append(inp)
        self.prep_times.append(activity.info().scheduled_time)
        if inp.fixture["id"] in self.bad_prepare:
            raise ApplicationError("cannot create the project", type="StoreUnavailable", non_retryable=True)
        rid = str(uuid.uuid4())
        d = DesignInput(
            RunCtx(inp.ctx.tenant_id, str(uuid.uuid4()), rid, f"design-{rid}"),
            "dv0",
            inp.fixture["brief"],
            answerer="simulator",
            stages=list(TWO_STAGES),
            parent_run_id=inp.ctx.run_id,
            model=inp.model,
            budget_usd=inp.budget_usd,
            persona=inp.fixture["hidden_spec"],
            parent_workflow_id=inp.ctx.workflow_id,
        )
        self.designs[rid] = d
        return PreparedTrial(inp.key, d, inp.fixture["id"], inp.model, inp.trial)

    @activity.defn(name=A_PREPARE_SCORE_BUILD)
    async def prepare_score_build(self, inp: ScoreTrialInput) -> ScoreBuildPlan:
        if not self.plan_build:
            return ScoreBuildPlan(None)
        d = inp.trial.design
        rid = str(uuid.uuid4())
        return ScoreBuildPlan(
            BuildInput(
                RunCtx(d.ctx.tenant_id, d.ctx.project_id, rid, f"build-{rid}"),
                "dv-final",
                REF,
                REF,
                {"until": "check"},
                parent_run_id=d.ctx.run_id,
            )
        )

    @activity.defn(name=A_SCORE_TRIAL)
    async def score_trial(self, inp: ScoreTrialInput) -> TrialResult:
        self.scored.append(inp)
        fid = inp.fixture["id"]
        asked = [q for s in self.sim if s.ctx.run_id == inp.trial.design.ctx.run_id for q in [s.questions]]
        ev = Evidence(
            has_design=inp.design_status == "succeeded",
            metrics=dict(self.metrics.get(fid, GOOD)) if inp.design_status == "succeeded" else {},
            texts={"requirements.yaml": "battery cost", "conflicts.md": "battery against cost"},
            question_texts=[str(q) for q in asked],
            meta={"questions": sum(len(q) for q in asked), "rounds": len(asked)},
        )
        card = score_card(ev, inp.fixture, model=inp.trial.model or "default", trial=inp.trial.trial)
        card.update(
            {
                "key": inp.trial.key,
                "design_run_id": inp.trial.design.ctx.run_id,
                "design_status": inp.design_status,
                "build_run_id": inp.build_run_id or None,
            }
        )
        return TrialResult(inp.trial.key, card, REF)

    @activity.defn(name=A_FINISH_EVAL)
    async def finish_eval(self, inp: FinishEvalInput) -> FinishEvalOut:
        self.finished.append(inp)
        doc = eval_document(
            suite=inp.suite,
            run_id=inp.ctx.run_id,
            cards=inp.cards,
            errors=inp.errors,
            baseline=self.baseline_doc if inp.baseline else None,
            baseline_name=inp.baseline_name,
            options=inp.options,
        )
        render_markdown(doc)
        self.docs.append(doc)
        return FinishEvalOut(doc["verdict"], REF, REF)

    # -- the agent and the simulated product person
    @activity.defn(name=A_AGENT_STAGE)
    async def agent_stage(self, inp: AgentStageInput) -> AgentStageOutput:
        self.agent_calls.append(inp)
        self.scheduled.append(activity.info().current_attempt_scheduled_time)
        self.stage_calls[(inp.ctx.run_id, inp.stage)] += 1
        n = self.stage_calls[(inp.ctx.run_id, inp.stage)] - 1
        self.cur += 1
        self.peak = max(self.peak, self.cur)
        try:
            await asyncio.sleep(self.hold_s)
            fn = self.agent_script.get((self.designs[inp.ctx.run_id].brief, inp.stage))
            if inp.ctx.run_id in self.fail_design_ids():
                raise ApplicationError("the agent crashed", type="AgentFailed", non_retryable=True)
            if fn is not None:
                return await fn(inp, n)
            return await done(inp, n)
        finally:
            self.cur -= 1

    def fail_design_ids(self) -> set[str]:
        return {r for r, d in self.designs.items() if d.brief in self.fail_design}

    @activity.defn(name=A_SIMULATE_ANSWER)
    async def simulate_answer(self, inp: SimulateInput) -> dict:
        self.sim.append(inp)
        return deterministic_answers(inp.persona, inp.questions)

    def acts(self, queue: str) -> list:
        if queue == Q_AGENT:
            return [self.agent_stage, self.simulate_answer]
        acts = super().acts(queue)
        if queue == Q_ENGINE:
            acts += [self.prepare_eval_trial, self.prepare_score_build, self.score_trial, self.finish_eval]
        return acts


def eval_arg(fixtures, **kw) -> EvalInput:
    rid = str(uuid.uuid4())
    kw.setdefault(
        "options", {"visibility": False}
    )  # the time-skipping server has no custom search attributes
    return EvalInput(
        RunCtx(str(uuid.uuid4()), str(uuid.uuid4()), rid, f"eval-{rid}"), "smoke", fixtures, **kw
    )


def workers_for(env, fakes):
    return Workers(
        build_workers(
            env.client,
            None,
            QUEUES,
            slots={q: {"activities": 8, "workflows": 16} for q in QUEUES},
            workflows=[EvalWorkflow, DesignWorkflow, BuildWorkflow, EngineToolWorkflow],
            activities=fakes.acts,
            max_heartbeat_throttle_interval=timedelta(milliseconds=100),
            default_heartbeat_throttle_interval=timedelta(milliseconds=100),
        )
    )


async def wait_for(pred, what: str, n: int = 400):
    for _ in range(n):
        if pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def run(fakes, a: EvalInput, drive=None):
    async def go():
        env = await WorkflowEnvironment.start_time_skipping()
        try:
            async with workers_for(env, fakes):
                h = await env.client.start_workflow(
                    EvalWorkflow.run, a, id=a.ctx.workflow_id, task_queue=Q_ENGINE
                )
                if drive:
                    await drive(env, h)
                return await h.result()
        finally:
            await env.shutdown()

    return asyncio.run(go())


# -- fan-out, scoring ------------------------------------------------------------------------------------


def test_fan_out_is_bounded_and_every_trial_is_scored():
    f = EvalFakes()
    fx = [fixture("a"), fixture("b"), fixture("c")]
    res = run(f, eval_arg(fx, models=["sonnet", "opus"], repetitions=1, max_parallel=2))
    assert isinstance(res, EvalOutput) and res.status == "succeeded" and res.verdict == "PASS"
    assert res.trials == 6 and res.errors == 0
    assert 1 < f.peak <= 2, f.peak  # parallel, but never above the bound of the evaluation
    assert sorted(p.key for p in f.prepared) == sorted(
        f"trial.{x}.{m}.t1" for x in "abc" for m in ("sonnet", "opus")
    )
    # every design run is linked to the eval run, runs the simulator and carries the trial's model
    runs = f.designs.values()
    assert {d.parent_run_id for d in runs} == {f.prepared[0].ctx.run_id}
    assert {d.answerer for d in runs} == {"simulator"} and {d.model for d in runs} == {"sonnet", "opus"}
    assert all(c.model in ("sonnet", "opus") for c in f.agent_calls)
    # scored with the real card; one finish with every card
    doc = f.docs[0]
    assert len(doc["trials"]) == 6 and doc["aggregate"]["a::sonnet"]["n"] == 1
    assert all(t["score"] > 80 for t in doc["trials"]), [t["points"] for t in doc["trials"]]
    assert doc["verdict"] == "PASS" and doc["comparison"] == [] and doc["baseline"] is None
    # the eval run and its trials are recorded as stages, in the order of the plan
    trial_stages = [s for s in f.stages if s.stage == "trial" and s.status == "succeeded"]
    assert sorted(s.key for s in trial_stages) == sorted(p.key for p in f.prepared)
    assert [r.to for r in f.runs if r.ctx.run_id == f.prepared[0].ctx.run_id] == ["running", "succeeded"]


def test_repetitions_and_the_budget_reach_every_trial():
    f = EvalFakes()
    res = run(f, eval_arg([fixture("a")], repetitions=3, max_parallel=3, budget_usd=1.5))
    assert res.trials == 3
    assert sorted(p.trial for p in f.prepared) == [1, 2, 3] and {p.budget_usd for p in f.prepared} == {1.5}
    assert {c.budget_usd for c in f.agent_calls} == {1.5}
    assert f.docs[0]["aggregate"]["a::default"]["n"] == 3


def test_a_failing_fixture_does_not_fail_the_evaluation():
    f = EvalFakes(bad_prepare={"b"}, fail_design={"Brief of c."})
    res = run(f, eval_arg([fixture("a"), fixture("b"), fixture("c")], max_parallel=3))
    assert res.status == "succeeded" and res.trials == 2 and res.errors == 1
    doc = f.docs[0]
    assert [e["fixture"] for e in doc["errors"]] == ["b"] and "StoreUnavailable" in doc["errors"][0]["error"]
    by = {t["fixture"]: t for t in doc["trials"]}
    assert by["a"]["design_status"] == "succeeded" and by["a"]["score"] > 80
    # c's design run failed: it is still scored, with what it left (no design, no metrics)
    assert by["c"]["design_status"] == "failed" and by["c"]["has_design"] is False and by["c"]["score"] < 40
    failed = {s.key for s in f.stages if s.stage == "trial" and s.status == "failed"}
    assert failed == {"trial.b.default.t1"}  # c's trial ended as a scored trial, b's did not
    assert "StoreUnavailable" in md(f)


def md(f) -> str:
    return render_markdown(f.docs[0])


def test_every_trial_failing_gives_a_failed_verdict_not_a_failed_run():
    f = EvalFakes(bad_prepare={"a", "b"})
    res = run(f, eval_arg([fixture("a"), fixture("b")]))
    assert res.status == "succeeded" and res.verdict == "FAIL" and res.trials == 0 and res.errors == 2


# -- baseline --------------------------------------------------------------------------------------------


def baseline_doc(mean: float, key: str = "a::default") -> dict:
    pts = {"design_exists": 5.0}
    return {
        "schema": "hwflow.eval/1",
        "suite": "smoke",
        "aggregate": {key: {"n": 1, "mean": mean, "std": 0.0, "min": mean, "max": mean, "points": pts}},
    }


def test_the_baseline_comparison_flags_a_regression():
    worse = {**GOOD, "drc_errors": 3, "sim_pass_ratio": 0.5}  # about 17 points below the baseline's 99
    f = EvalFakes(baseline=baseline_doc(99.0), metrics={"a": worse})
    res = run(f, eval_arg([fixture("a")], baseline=REF, baseline_name="main"))
    doc = f.docs[0]
    (row,) = doc["comparison"]
    assert row["key"] == "a::default" and row["tag"] == "REGRESSION" and row["delta"] < -5
    assert doc["verdict"] == res.verdict == "FAIL" and doc["baseline"]["name"] == "main"
    assert "REGRESSION" in md(f)


def test_a_score_within_tolerance_of_the_baseline_passes():
    probe = EvalFakes()
    run(probe, eval_arg([fixture("a")]))
    score = probe.docs[0]["trials"][0]["score"]
    f = EvalFakes(baseline=baseline_doc(score - 2))
    res = run(f, eval_arg([fixture("a")], baseline=REF))
    assert f.docs[0]["comparison"][0]["tag"] == "same" and res.verdict == "PASS"


# -- the simulated product person ------------------------------------------------------------------------


async def asks_battery(inp, n):
    if n == 0:
        return AgentStageOutput(
            "needs_answers",
            ws(50),
            None,
            "sess-q",
            questions=[
                {"id": "q1", "text": "Which battery do you want?", "options": ["CR2032", "AA"]},
                {"id": "q2", "text": "What is the colour of the case?"},
            ],
        )
    return await done(inp, n)


def test_questions_are_answered_by_the_simulator_and_the_agent_never_sees_the_hidden_spec():
    fx = fixture("a")
    f = EvalFakes(agent_script={(fx["brief"], "requirements"): asks_battery})
    res = run(f, eval_arg([fx]))
    assert res.status == "succeeded"
    (s,) = f.sim
    assert s.persona == fx["hidden_spec"] and s.stage == "requirements"
    second = [c for c in f.agent_calls if c.stage == "requirements"][1]
    assert second.resume == "answers" and second.round == 1
    assert second.answers == {"q1": "the battery is a CR2032", "q2": "No preference, use your judgment."}
    # nothing the agent receives carries the hidden specification
    assert all("CR2032" not in repr(c) and "below 5 USD" not in repr(c) for c in f.agent_calls[:1])
    assert all(fx["hidden_spec"] not in repr(c) for c in f.agent_calls)
    t = f.docs[0]["trials"][0]
    assert t["questions"] == 2 and t["rounds"] == 1


def test_parse_answers_follows_the_persona_format():
    qs = [{"id": "q1"}, {"id": "q2"}, {"id": "q3"}]
    text = "q1: Keep 12 V.\n- q2: Up to 60 minutes,\n  more if needed (cost: firm).\n"
    assert parse_answers(text, qs) == {
        "q1": "Keep 12 V.",
        "q2": "Up to 60 minutes, more if needed (cost: firm).",
        "q3": "No preference, use your judgment.",
    }
    assert parse_answers("**q1**: bold\nQ2: upper", qs)["q2"] == "upper"


# -- rate limits -----------------------------------------------------------------------------------------


def test_a_child_rate_limit_pauses_new_trials():
    fx = [fixture("a"), fixture("b"), fixture("c")]
    state = {"n": 0}

    async def limited_once(inp, n):
        if state["n"] == 0:
            state["n"] += 1
            raise ApplicationError(
                "rl",
                {**snapshot_details(ws(70), None, "sess-r"), "retry_after_s": 600},
                type=errors.LlmRateLimited,
                non_retryable=True,
            )
        return await done(inp, n)

    f = EvalFakes(agent_script={(fx[0]["brief"], "requirements"): limited_once})
    # a and b start together; a waits out its limit; c must not start before that wait is over
    res = run(f, eval_arg(fx, max_parallel=2))
    assert res.trials == 3
    assert any(c.resume == "retry" for c in f.agent_calls), "the limited stage was retried after its wait"
    # c was prepared only after the 600 s limit had passed (workflow time), not as soon as b freed a slot
    waited = (f.prep_times[2] - min(f.scheduled)).total_seconds()
    assert waited >= 600, waited
    assert (f.prep_times[1] - min(f.scheduled)).total_seconds() < 60  # b started at once


# -- build child -----------------------------------------------------------------------------------------


def test_the_scoring_build_runs_as_a_child_workflow():
    f = EvalFakes(plan_build=True)
    res = run(f, eval_arg([fixture("a")]))
    assert res.trials == 1
    assert f.calls["check"] == 1  # the real BuildWorkflow ran its stage
    (sin,) = f.scored
    assert (
        sin.build_run_id
        and sin.build_run_id in f.run_status
        and f.run_status[sin.build_run_id] == "succeeded"
    )


# -- cancellation ----------------------------------------------------------------------------------------


def test_cancel_reaches_the_child_design_runs():
    async def hang(inp, n):
        while True:
            activity.heartbeat("working")
            await asyncio.sleep(0.05)

    fx = [fixture("a"), fixture("b")]
    f = EvalFakes(agent_script={(x["brief"], "requirements"): hang for x in fx}, hold_s=0)
    a = eval_arg(fx, max_parallel=2)

    async def drive(env, h):
        await wait_for(lambda: len(f.agent_calls) == 2, "two design runs in their first stage")
        await h.cancel()

    with pytest.raises(WorkflowFailureError) as ei:
        run(f, a, drive)
    assert isinstance(ei.value.cause, CancelledError)
    child_runs = set(f.designs)
    assert len(child_runs) == 2
    assert {r.ctx.run_id for r in f.runs if r.to == "cancelled"} >= child_runs | {a.ctx.run_id}
    assert f.run_status[a.ctx.run_id] == "cancelled"
    assert all(f.run_status[r] == "cancelled" for r in child_runs)
    assert f.finished == [] and f.scored == []  # nothing was scored or reported
    assert {s.key for s in f.stages if s.status == "cancelled" and s.stage == "trial"} >= {
        "trial.a.default.t1",
        "trial.b.default.t1",
    }
