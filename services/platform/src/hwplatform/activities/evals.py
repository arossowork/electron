"""The EvalWorkflow's engine-queue activities (ADR 0001, 4.4 and 7.7, issue #19).

- `prepare_eval_trial`: for one trial (fixture x model x repetition) a project, a design version holding the
  brief (and `requirements.yaml` for a fixture that starts from final requirements) and the queued design run,
  linked to the eval run by `parent_run_id`. Idempotent: the run is created under the key
  `eval:<run>:<trial>`.
- `prepare_score_build`: the build that produces the metrics of a trial, on the design run's final design
  version. The stage cache makes it nearly free when the agent already built that version. The workflow
  starts it
  as a child workflow, so it holds no activity slot while it runs.
- `score_trial`: the trial's score card from the design run's manifest and events, the final design version's
  documents and the `score.json` of the scoring build (`hwharness.evals.cards.evidence_from_run`). It reads no
  `status.json`.
- `finish_eval`: aggregates the cards, compares them with the baseline and stores `eval.json` and
`REPORT.md` as
  artifacts of the eval run.

Everything here reads and writes through the tenant's stores; the hidden specification of a fixture goes
into the
child's `DesignInput.persona` and from there only to `simulate_answer`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime

from hwflow.artifacts import ArtifactRef
from hwflow.ids import uuid7
from hwflow.runs import Run, RunStatus, run_to_manifest
from sqlalchemy import text
from temporalio import activity
from temporalio.exceptions import ApplicationError

from .. import run_options
from ..contracts import (
    A_FINISH_EVAL,
    A_PREPARE_EVAL_TRIAL,
    A_PREPARE_SCORE_BUILD,
    A_SCORE_TRIAL,
    DESIGN_STAGES,
    BuildInput,
    DesignInput,
    FinishEvalInput,
    FinishEvalOut,
    PreparedTrial,
    PrepareTrial,
    RunCtx,
    ScoreBuildPlan,
    ScoreTrialInput,
    TrialResult,
)
from ..designs import FileSet, read_tree, store_design_version
from ..gateway import build_workflow_id, design_workflow_id
from ..runs import PgRunStore
from .deps import Deps

log = logging.getLogger(__name__)

MAX_DOC_BYTES = 1_000_000  # one agent document read for scoring


def trial_slug(eval_run_id: str, key: str) -> str:
    h = hashlib.sha1(f"{eval_run_id}/{key}".encode(), usedforsecurity=False).hexdigest()  # noqa: S324
    return f"eval-{h[:16]}"


def design_stages(fixture: dict) -> list[str]:
    """A fixture that starts from final requirements skips the requirements stage."""
    stages = list(DESIGN_STAGES)
    return stages[1:] if fixture.get("mode") == "from_requirements" else stages


def design_brief(fixture: dict) -> str:
    brief = str(fixture["brief"]).strip()
    if fixture.get("mode") == "from_requirements":
        brief += (
            "\n\nrequirements.yaml is final. Start at the partition stage and run to sign-off "
            "without asking questions."
        )
    return brief


class EvalActivities:
    def __init__(self, deps: Deps):
        self.deps = deps

    # -- helpers ----------------------------------------------------------------------------------
    def _runs(self, tenant_id: str) -> PgRunStore:
        return PgRunStore(self.deps.db, tenant_id)

    async def _read(self, tenant_id: str, ref: ArtifactRef, limit: int = 16 * 1024 * 1024) -> bytes:
        store = self.deps.store(tenant_id)

        def read() -> bytes:
            with store.open(ref.sha256) as f:
                return f.read(limit)

        return await asyncio.to_thread(read)

    async def _requested_by(self, tenant_id: str, run_id: str) -> uuid.UUID | None:
        async with self.deps.db.tx(tenant_id=uuid.UUID(tenant_id)) as s:
            return (
                await s.execute(text("SELECT requested_by FROM runs WHERE id = :i"), {"i": uuid.UUID(run_id)})
            ).scalar_one_or_none()

    # -- prepare_eval_trial -----------------------------------------------------------------------
    @activity.defn(name=A_PREPARE_EVAL_TRIAL)
    async def prepare_eval_trial(self, inp: PrepareTrial) -> PreparedTrial:
        ctx, fx = inp.ctx, inp.fixture
        tenant = uuid.UUID(ctx.tenant_id)
        user = await self._requested_by(ctx.tenant_id, ctx.run_id)
        slug = trial_slug(ctx.run_id, inp.key)
        label = f"eval {fx['id']} {inp.model or 'default'} #{inp.trial}"
        async with self.deps.db.tx(tenant_id=tenant) as s:
            pid = (
                await s.execute(
                    text(
                        "INSERT INTO projects (id, tenant_id, name, slug, created_by) "
                        "VALUES (:i, :t, :n, :s, :u) "
                        "ON CONFLICT (tenant_id, slug) DO UPDATE SET name = EXCLUDED.name RETURNING id"
                    ),
                    {"i": uuid.uuid4(), "t": tenant, "n": label[:200], "s": slug, "u": user},
                )
            ).scalar_one()
        fs = FileSet()
        big = dict(max_bytes=2_000_000, max_files=10)
        fs.add("design.yaml", f"name: {fx['id']}\ncomponents: {{}}\nnets: {{}}\n".encode(), **big)
        fs.add("brief.md", str(fx["brief"]).encode(), **big)
        if fx.get("mode") == "from_requirements" and fx.get("requirements"):
            fs.add("requirements.yaml", str(fx["requirements"]).encode(), **big)
        dv, _ = await store_design_version(
            self.deps.db, self.deps.store(ctx.tenant_id), tenant, pid, fs, user_id=user, message=label
        )
        stages = design_stages(fx)
        options = {
            "brief": design_brief(fx),
            "answerer": "simulator",
            "stages": stages,
            "eval": {"run_id": ctx.run_id, "fixture": fx["id"], "model": inp.model, "trial": inp.trial},
        }
        rid = str(uuid7())
        wf = design_workflow_id(ctx.tenant_id, str(pid), rid)
        run = Run(
            run_id=rid,
            kind="design",
            tenant_id=ctx.tenant_id,
            project_id=str(pid),
            design_version=dv,
            status=RunStatus.queued,
            verdict=None,
            options=options,
            options_hash=run_options.options_hash(options),
            settings={},
            code={},
            tools={},
            stages=[],
            artifacts={},
            created_at=datetime.now(UTC),
            parent_run_id=ctx.run_id,
            workflow_id=wf,
        )
        # A retried call gets the run the first call created (and its workflow ID), not a second one.
        run = await self._runs(ctx.tenant_id).create(
            run, idempotency_key=f"eval:{ctx.run_id}:{inp.key}", requested_by=user
        )
        design = DesignInput(
            ctx=RunCtx(ctx.tenant_id, str(pid), run.run_id, run.workflow_id or wf),
            design_version_id=dv.id,
            brief=design_brief(fx),
            answerer="simulator",
            stages=stages,
            question_timeout_s=inp.question_timeout_s,
            parent_run_id=ctx.run_id,
            model=inp.model,
            budget_usd=inp.budget_usd,
            persona=str(fx.get("hidden_spec") or ""),
            parent_workflow_id=ctx.workflow_id,
        )
        return PreparedTrial(inp.key, design, fx["id"], inp.model, inp.trial)

    # -- prepare_score_build ----------------------------------------------------------------------
    async def _versions(self, tenant: str, project_id: str) -> list:
        async with self.deps.db.tx(tenant_id=uuid.UUID(tenant)) as s:
            return list(
                (
                    await s.execute(
                        text(
                            "SELECT id, seq, content_sha256, project_tree_sha256 FROM design_versions "
                            "WHERE project_id = :p ORDER BY seq"
                        ),
                        {"p": uuid.UUID(project_id)},
                    )
                ).all()
            )

    @activity.defn(name=A_PREPARE_SCORE_BUILD)
    async def prepare_score_build(self, inp: ScoreTrialInput) -> ScoreBuildPlan:
        d = inp.trial.design
        versions = await self._versions(d.ctx.tenant_id, d.ctx.project_id)
        if len(versions) < 2:  # the agent saved no design version: nothing to build
            return ScoreBuildPlan(None)
        last = versions[-1]
        store = self._runs(d.ctx.tenant_id)
        dv = await store.get_design_version(last.id)
        opts = run_options.normalize_build_options({"until": "fab"})
        ohash = run_options.options_hash(opts)
        wf = build_workflow_id(d.ctx.tenant_id, d.ctx.project_id, dv.content_sha256, ohash)
        run = Run(
            run_id=str(uuid7()),
            kind="build",
            tenant_id=d.ctx.tenant_id,
            project_id=d.ctx.project_id,
            design_version=dv,
            status=RunStatus.queued,
            verdict=None,
            options=opts,
            options_hash=ohash,
            settings={},
            code={},
            tools={},
            stages=[],
            artifacts={},
            created_at=datetime.now(UTC),
            parent_run_id=d.ctx.run_id,
            workflow_id=wf,
        )
        run = await store.create(run, idempotency_key=f"eval-score:{d.ctx.run_id}")
        return ScoreBuildPlan(
            BuildInput(
                ctx=RunCtx(d.ctx.tenant_id, d.ctx.project_id, run.run_id, run.workflow_id or wf),
                design_version_id=dv.id,
                design=dv.design,
                project=dv.project,
                options=opts,
                parent_run_id=d.ctx.run_id,
            )
        )

    # -- score_trial ------------------------------------------------------------------------------
    async def _events(self, tenant: str, run_id: str) -> list[dict]:
        async with self.deps.db.tx(tenant_id=uuid.UUID(tenant)) as s:
            rows = (
                await s.execute(
                    text("SELECT envelope FROM outbox WHERE run_id = :r ORDER BY seq, id"),
                    {"r": uuid.UUID(run_id)},
                )
            ).scalars()
            return [json.loads(r) if isinstance(r, str) else r for r in rows]

    async def _artifact(self, tenant: str, run_id: str, name: str) -> ArtifactRef | None:
        async with self.deps.db.tx(tenant_id=uuid.UUID(tenant)) as s:
            r = (
                (
                    await s.execute(
                        text(
                            "SELECT sha256, size, media_type FROM run_artifacts "
                            "WHERE run_id = :r AND name = :n"
                        ),
                        {"r": uuid.UUID(run_id), "n": name},
                    )
                )
                .mappings()
                .first()
            )
        return ArtifactRef(r["sha256"], r["size"], r["media_type"], name) if r else None

    @activity.defn(name=A_SCORE_TRIAL)
    async def score_trial(self, inp: ScoreTrialInput) -> TrialResult:
        from hwharness.evals.cards import TEXT_FILES, evidence_from_run, score_card

        d = inp.trial.design
        tenant = d.ctx.tenant_id
        runs = self._runs(tenant)
        manifest = run_to_manifest(await runs.get(d.ctx.run_id))
        events = await self._events(tenant, d.ctx.run_id)
        versions = await self._versions(tenant, d.ctx.project_id)
        files: dict[str, str] = {}
        if versions:
            tree = await read_tree(self.deps.store(tenant), versions[-1].project_tree_sha256)
            for name in TEXT_FILES:
                if name in tree and int(tree[name]["size"]) <= MAX_DOC_BYTES:
                    ref = ArtifactRef(tree[name]["sha256"], int(tree[name]["size"]), "text/plain", name)
                    files[name] = (await self._read(tenant, ref, MAX_DOC_BYTES)).decode("utf-8", "replace")
        metrics = None
        if inp.build_run_id:
            ref = await self._artifact(tenant, inp.build_run_id, "score.json")
            if ref is not None:
                metrics = json.loads(await self._read(tenant, ref))
        ev = evidence_from_run(manifest, events, files=files, metrics=metrics, has_design=len(versions) > 1)
        card = score_card(ev, inp.fixture, model=inp.trial.model or "default", trial=inp.trial.trial)
        card.update(
            {
                "key": inp.trial.key,
                "design_run_id": d.ctx.run_id,
                "design_status": inp.design_status,
                "design_error": inp.design_error,
                "build_run_id": inp.build_run_id or None,
            }
        )
        ref = await asyncio.to_thread(
            self.deps.store(tenant).put_bytes,
            json.dumps(card, indent=1, sort_keys=True, default=str).encode(),
            media_type="application/json",
            name=f"{inp.trial.key}.card.json",
        )
        return TrialResult(inp.trial.key, card, ref)

    # -- finish_eval ------------------------------------------------------------------------------
    @activity.defn(name=A_FINISH_EVAL)
    async def finish_eval(self, inp: FinishEvalInput) -> FinishEvalOut:
        from hwharness.evals.report import eval_document, render_markdown

        tenant = inp.ctx.tenant_id
        baseline = None
        if inp.baseline is not None:
            try:
                baseline = json.loads(await self._read(tenant, inp.baseline))
            except ValueError as e:
                raise ApplicationError(f"the baseline is not JSON: {e}", non_retryable=True) from None
        doc = eval_document(
            suite=inp.suite,
            run_id=inp.ctx.run_id,
            cards=inp.cards,
            errors=inp.errors,
            baseline=baseline,
            baseline_name=inp.baseline_name or None,
            options=inp.options,
            created=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        store = self.deps.store(tenant)
        body = json.dumps(doc, indent=1, sort_keys=True, default=str).encode()
        report = render_markdown(doc).encode()
        eval_ref = await asyncio.to_thread(
            store.put_bytes, body, media_type="application/json", name="eval.json"
        )
        report_ref = await asyncio.to_thread(
            store.put_bytes, report, media_type="text/markdown", name="REPORT.md"
        )
        await self._runs(tenant).set_artifacts(
            inp.ctx.run_id, {"eval.json": eval_ref, "REPORT.md": report_ref}
        )
        return FinishEvalOut(doc["verdict"], report_ref, eval_ref)
