"""EvalActivities on the real database and S3 (#19): trial preparation, scoring from a real manifest and events,
the report artifacts. The activities are called directly (no Temporal); the workflow is tested in test_workflow_eval."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest
from hwflow.ids import uuid7
from hwflow.runs import Run, RunStatus
from sqlalchemy import text

from hwplatform.activities.deps import Deps
from hwplatform.activities.evals import EvalActivities
from hwplatform.contracts import (
    FinishEvalInput,
    PrepareTrial,
    RunCtx,
    ScoreTrialInput,
)
from hwplatform.designs import FileSet, store_design_version

pytestmark = pytest.mark.integration

FX = {
    "id": "tiny",
    "brief": "A small night light.",
    "hidden_spec": "9 V battery.",
    "mode": "full",
    "planted_conflicts": [{"id": "c1", "desc": "", "keywords": [["battery"], ["led"]]}],
    "expected_requirements": [["night"], ["zzz-never"]],
    "limits": {},
}


async def eval_run(env) -> tuple[Run, RunCtx]:
    dv = await env.design_version("e")
    rid = str(uuid7())
    run = Run(
        run_id=rid,
        kind="eval",
        tenant_id=str(env.tenant_id),
        project_id=str(env.project_id),
        design_version=dv,
        status=RunStatus.queued,
        verdict=None,
        options={"suite": "s"},
        options_hash="abc123abc123",
        settings={},
        code={},
        tools={},
        stages=[],
        artifacts={},
        created_at=datetime.now(UTC),
        workflow_id=f"eval-{env.tenant_id}-{rid}",
    )
    run = await env.store.create(run)
    return run, RunCtx(str(env.tenant_id), str(env.project_id), run.run_id, run.workflow_id)


def test_prepare_score_and_finish(run_env, api_settings, s3_client):
    async def main():
        async with run_env("evalact") as env:
            deps = Deps(api_settings(), db=env.db, s3_client=s3_client)
            acts = EvalActivities(deps)
            _, ctx = await eval_run(env)

            # prepare: a project, a stub design version, a design run linked to the eval run; a retry is the same
            p = PrepareTrial(ctx, "trial.tiny.default.t1", FX, "", 1, budget_usd=2.0, question_timeout_s=60)
            t = await acts.prepare_eval_trial(p)
            again = await acts.prepare_eval_trial(p)
            assert again.design.ctx.run_id == t.design.ctx.run_id
            d = t.design
            assert d.answerer == "simulator" and d.parent_run_id == ctx.run_id and d.persona == "9 V battery."
            assert (
                d.stages[0] == "requirements"
                and d.budget_usd == 2.0
                and d.parent_workflow_id == ctx.workflow_id
            )
            assert "9 V battery" not in d.brief  # the agent never sees the hidden specification
            child = await env.store.get(d.ctx.run_id)
            assert (
                child.kind == "design"
                and child.parent_run_id == ctx.run_id
                and child.status == RunStatus.queued
            )

            # a second trial gets another project
            t2 = await acts.prepare_eval_trial(PrepareTrial(ctx, "trial.tiny.default.t2", FX, "", 2))
            assert t2.design.ctx.project_id != d.ctx.project_id

            # the design run's work: a final design version with documents, questions asked, stages
            fs = FileSet()
            big = dict(max_bytes=1_000_000, max_files=10)
            fs.add("design.yaml", b"name: tiny\nparts: []\n", **big)
            fs.add("conflicts.md", b"The battery cannot drive the led at night.", **big)
            fs.add("requirements.yaml", b"requirements:\n  - id: R-1\n    text: works at night\n", **big)
            await store_design_version(
                env.db,
                deps.store(ctx.tenant_id),
                env.tenant_id,
                uuid.UUID(d.ctx.project_id),
                fs,
                user_id=None,
                message="agent",
            )
            await env.store.transition(d.ctx.run_id, RunStatus.running)
            await env.store.append_event(
                d.ctx.run_id, "question.asked", {"questions": [{"id": "q1", "text": "Which battery?"}]}
            )
            await env.store.transition(d.ctx.run_id, RunStatus.succeeded, verdict="PASS")

            # no scoring build planned with one version only for trial 2; the first has two versions
            plan = await acts.prepare_score_build(ScoreTrialInput(ctx, t, FX))
            assert plan.build is not None and plan.build.options["until"] == "fab"
            assert plan.build.parent_run_id == d.ctx.run_id
            plan2 = await acts.prepare_score_build(ScoreTrialInput(ctx, t2, FX))
            assert plan2.build is None

            res = await acts.score_trial(ScoreTrialInput(ctx, t, FX))
            c = res.card
            assert c["fixture"] == "tiny" and c["has_design"] is True and c["conflicts_found"] == ["c1"]
            assert c["req_hits"] == 1 and c["points"]["req_coverage"] == 5.0
            assert c["questions"] == 1 and c["rounds"] == 1 and c["design_status"] == "succeeded"
            assert (
                c["points"]["routed"] == 0
            )  # no scoring build, no metrics: scored as missing, not as zero errors
            bad = await acts.score_trial(
                ScoreTrialInput(ctx, t2, FX, design_status="failed", design_error={"m": 1})
            )
            assert bad.card["has_design"] is False and bad.card["design_status"] == "failed"

            # finish: eval.json and REPORT.md become artifacts of the eval run
            base = deps.store(ctx.tenant_id).put_bytes(
                json.dumps({"tiny::default": {"mean": 99, "std": 0, "points": {}}}).encode(),
                media_type="application/json",
                name="baseline.json",
            )
            out = await acts.finish_eval(
                FinishEvalInput(
                    ctx, "s", [c, bad.card], [{"fixture": "x", "error": "boom"}], base, "prev", {"k": 1}
                )
            )
            assert out.verdict in ("PASS", "FAIL")
            run = await env.store.get(ctx.run_id)
            assert {"eval.json", "REPORT.md"} <= set(run.artifacts)
            with deps.store(ctx.tenant_id).open(run.artifacts["eval.json"].sha256) as f:
                doc = json.load(f)
            assert doc["suite"] == "s" and len(doc["trials"]) == 2 and doc["baseline"]["name"] == "prev"
            assert doc["comparison"] and doc["errors"][0]["error"] == "boom"
            with deps.store(ctx.tenant_id).open(run.artifacts["REPORT.md"].sha256) as f:
                assert f.read().startswith(b"# Evaluation s")

    asyncio.run(main())


def test_tenant_isolation_of_trial_projects(run_env, api_settings, s3_client):
    async def main():
        async with run_env("evalact-a") as a, run_env("evalact-b") as b:
            deps = Deps(api_settings(), db=a.db, s3_client=s3_client)
            _, ctx = await eval_run(a)
            t = await EvalActivities(deps).prepare_eval_trial(PrepareTrial(ctx, "k", FX, "", 1))
            async with b.db.tx(tenant_id=b.tenant_id) as s:
                n = (
                    await s.execute(
                        text("SELECT count(*) FROM projects WHERE id = :p"),
                        {"p": uuid.UUID(t.design.ctx.project_id)},
                    )
                ).scalar_one()
            assert n == 0

    asyncio.run(main())
