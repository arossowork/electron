"""Evaluations (ADR 0001, sections 6 and 7.7, issue #19): fixtures run as design workflows and are scored.

`POST /evals` starts an EvalWorkflow (run kind `eval`). Each trial of it is a design run linked to the eval run by
`parent_run_id`, answered by the simulated product person. The report is the eval run's `REPORT.md` (so
`GET /runs/{id}/report` serves it too) and `eval.json`. A baseline is promoted on purpose
(`POST /evals/{id}/promote-baseline`); it is the default comparison of the next evaluation of the suite.

Starting an evaluation needs the owner or admin role: it spends the tenant's agent credential.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from hwflow.artifacts import ArtifactNotFound, ArtifactRef, StoreUnavailable
from hwflow.ids import uuid7
from hwflow.runs import Run, RunStatus
from sqlalchemy import text

from .. import run_options
from ..contracts import EvalInput, RunCtx
from ..db import Database
from ..designs import FileSet, UploadError, store_design_version
from ..gateway import GatewayError, WorkflowGateway, eval_workflow_id
from ..runs import PgRunStore, QuotaExceeded
from ..tenancy import ProjectRepo, TenantContext, slugify
from .deps import get_db, get_gateway, require_admin, tenant_context, tenant_store
from .routes_runs import _SUMMARY_COLS, _artifact_row, load_run, run_out
from .schemas import (
    BaselineIn,
    BaselineOut,
    ErrorOut,
    EvalIn,
    EvalOut,
    EvalSummaryOut,
    FixtureIn,
    Page,
    RunStatusT,
)

log = logging.getLogger("hwplatform.api.evals")
router = APIRouter(prefix="/evals", tags=["evals"])

_NO_EVAL = "evaluation not found"
SCHEMA = "hwflow.eval/1"
MAX_BASELINE_BYTES = 2_000_000


# --- helpers ---------------------------------------------------------------------------------------------


def _fixture_from_dir(root: str, fid: str) -> FixtureIn:
    if not root or not fid.replace("-", "").replace("_", "").replace(".", "").isalnum() or ".." in fid:
        raise HTTPException(422, f"unknown fixture id {fid!r}: send the fixture document")
    p = Path(root) / f"{fid}.yaml"
    if not p.is_file():
        raise HTTPException(422, f"unknown fixture id {fid!r}: send the fixture document")
    doc = yaml.safe_load(p.read_text()) or {}
    rf = doc.pop("requirements_file", None)
    if rf and not doc.get("requirements") and (Path(root) / str(rf)).is_file():
        doc["requirements"] = (Path(root) / str(rf)).read_text()
    return FixtureIn.model_validate(doc)


def resolve_fixtures(raw: list[FixtureIn | str], root: str) -> list[dict]:
    out: list[dict] = []
    for f in raw:
        fx = _fixture_from_dir(root, f) if isinstance(f, str) else f
        if fx.mode == "from_requirements" and not fx.requirements.strip():
            raise HTTPException(422, f"fixture {fx.id}: mode from_requirements needs `requirements`")
        out.append(fx.model_dump())
    ids = [f["id"] for f in out]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "fixture ids must be unique")
    return out


def check_baseline_doc(doc: dict) -> dict:
    """An eval document, or the legacy aggregate (`evals/baseline.json`): keys `<fixture>::<model>`."""
    if doc.get("schema") == SCHEMA and isinstance(doc.get("aggregate"), dict):
        agg = doc["aggregate"]
    else:
        agg = doc
    keys = [k for k in agg if not str(k).startswith("_")]
    ok = bool(keys) and all("::" in str(k) and isinstance(agg[k], dict) and "mean" in agg[k] for k in keys)
    if not ok:
        raise HTTPException(
            422,
            "baseline must be an eval document (schema hwflow.eval/1) or an aggregate keyed fixture::model",
        )
    return doc


async def _artifact_ref(db: Database, ctx: TenantContext, sha: str, name: str) -> ArtifactRef:
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        r = (
            (
                await s.execute(
                    text("SELECT size, media_type FROM artifacts WHERE tenant_id = :t AND sha256 = :s"),
                    {"t": ctx.tenant_id, "s": sha},
                )
            )
            .mappings()
            .first()
        )
    if r is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "the baseline artifact is gone")
    return ArtifactRef(sha, r["size"], r["media_type"] or "application/json", name)


async def _eval_run(db: Database, ctx: TenantContext, eval_id: uuid.UUID) -> Run:
    run = await load_run(db, ctx, eval_id)
    if run.kind != "eval":
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_EVAL)
    return run


async def _children(db: Database, ctx: TenantContext, eval_id: uuid.UUID) -> list[dict]:
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        rows = (
            (
                await s.execute(
                    text(
                        f"SELECT {_SUMMARY_COLS} FROM runs WHERE tenant_id = :t AND parent_run_id = :r "
                        "AND kind = 'design' ORDER BY id"
                    ),
                    {"t": ctx.tenant_id, "r": eval_id},
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


# --- start -----------------------------------------------------------------------------------------------


@router.post(
    "",
    response_model=EvalOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an evaluation",
    description=(
        "Runs each fixture (times the models and repetitions) as a design run answered by a simulated product "
        "person, scores the results and compares them with a baseline. Owner or admin. Counts as one run for the "
        "tenant's `max_concurrent_runs` (429 `quota_exceeded`); its trials run at most `max_parallel` at a time, "
        "never above that quota. The baseline is, in order: `baseline_run_id`, an inline `baseline` document, the "
        "suite's promoted baseline."
    ),
    responses={
        200: {"description": "The Idempotency-Key was used", "model": EvalOut},
        404: {"model": ErrorOut},
        409: {"model": ErrorOut},
        422: {"model": ErrorOut},
        429: {"description": "quota_exceeded"},
        503: {"model": ErrorOut},
    },
)
async def start_eval(
    body: EvalIn,
    request: Request,
    response: Response,
    ctx: TenantContext = Depends(require_admin),
    db: Database = Depends(get_db),
    gateway: WorkflowGateway = Depends(get_gateway),
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=200),
):
    settings = request.app.state.settings
    fixtures = resolve_fixtures(body.fixtures, settings.eval_fixtures_dir)
    models = list(dict.fromkeys(body.models)) or [""]
    if any(len(m) > 100 or any(c.isspace() for c in m) for m in models):
        raise HTTPException(422, "models must be aliases or model IDs without spaces")
    trials = len(fixtures) * len(models) * body.repetitions
    if trials > settings.eval_max_trials:
        raise HTTPException(422, f"{trials} trials; at most {settings.eval_max_trials} per evaluation")
    store = tenant_store(request, ctx)
    runs = PgRunStore(db, ctx.tenant_id)

    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        quota = (
            await s.execute(
                text("SELECT max_concurrent_runs FROM tenants WHERE id = :t"), {"t": ctx.tenant_id}
            )
        ).scalar_one()
    max_parallel = min(body.max_parallel or settings.eval_default_parallel, quota)

    # the baseline: an earlier evaluation, an inline document, or the suite's promoted one
    baseline_ref: ArtifactRef | None = None
    baseline_src: dict = {"source": None}
    inline: bytes | None = None
    if body.baseline_run_id is not None:
        base = await _eval_run(db, ctx, body.baseline_run_id)
        row = await _artifact_row(db, ctx, body.baseline_run_id, "eval.json")
        if row is None:
            raise HTTPException(status.HTTP_409_CONFLICT, f"evaluation {base.run_id} has no result yet")
        baseline_ref = await _artifact_ref(db, ctx, row["sha256"], "baseline.json")
        baseline_src = {"source": "run", "run_id": base.run_id}
    elif body.baseline is not None:
        inline = json.dumps(check_baseline_doc(body.baseline), sort_keys=True).encode()
        if len(inline) > MAX_BASELINE_BYTES:
            raise HTTPException(422, "baseline is too large")
        baseline_src = {"source": "inline"}
    else:
        async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
            promoted = (
                (
                    await s.execute(
                        text(
                            "SELECT sha256, name, run_id FROM eval_baselines WHERE tenant_id = :t AND suite = :su "
                            "ORDER BY promoted_at DESC, id DESC LIMIT 1"
                        ),
                        {"t": ctx.tenant_id, "su": body.suite},
                    )
                )
                .mappings()
                .first()
            )
        if promoted is not None:
            baseline_ref = await _artifact_ref(db, ctx, promoted["sha256"], "baseline.json")
            baseline_src = {
                "source": "promoted",
                "name": promoted["name"],
                "run_id": str(promoted["run_id"]) if promoted["run_id"] else None,
            }
    if inline is not None:
        baseline_ref = await asyncio.to_thread(
            store.put_bytes, inline, media_type="application/json", name="baseline.json"
        )

    # the eval run's own project and design version: the fixtures, as provenance
    rid = str(uuid7())
    fs = FileSet()
    lim = dict(max_bytes=8_000_000, max_files=len(fixtures) + 5)
    try:
        fs.add("design.yaml", f"name: eval-{body.suite}\ncomponents: {{}}\nnets: {{}}\n".encode(), **lim)
        for fx in fixtures:
            fs.add(f"fixtures/{fx['id']}.yaml", yaml.safe_dump(fx, sort_keys=True).encode(), **lim)
    except UploadError as e:
        raise HTTPException(422, str(e)) from e
    slug = f"eval-{slugify(body.suite, 40)}-{rid.replace('-', '')[-10:]}"
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        project = await ProjectRepo(s, ctx).create(f"eval {body.suite}", slug)
    dv, _ = await store_design_version(
        db, store, ctx.tenant_id, project.id, fs, user_id=ctx.user_id, message=f"evaluation {body.suite}"
    )
    options = {
        "suite": body.suite,
        "fixtures": [f["id"] for f in fixtures],
        "models": models,
        "repetitions": body.repetitions,
        "max_parallel": max_parallel,
        "budget_usd": body.budget_usd,
        "baseline": baseline_src,
    }
    wf_id = eval_workflow_id(str(ctx.tenant_id), rid)
    run = Run(
        run_id=rid,
        kind="eval",
        tenant_id=str(ctx.tenant_id),
        project_id=str(project.id),
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
        workflow_id=wf_id,
    )

    async def drop_project() -> None:
        async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
            await ProjectRepo(s, ctx).delete(project.id)

    try:
        adm = await runs.admit(run, idempotency_key=idempotency_key, requested_by=ctx.user_id)
    except QuotaExceeded as e:
        await drop_project()
        return JSONResponse(
            {
                "detail": f"quota_exceeded: {e}",
                "code": "quota_exceeded",
                "limit": e.limit,
                "active": e.active,
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": "30"},
        )
    if not adm.created:
        await drop_project()
        response.status_code = status.HTTP_200_OK
        return {**run_out(adm.run), "suite": adm.run.options.get("suite", ""), "children": []}
    if baseline_ref is not None:
        await runs.set_artifacts(rid, {"baseline.json": baseline_ref})

    arg = EvalInput(
        ctx=RunCtx(str(ctx.tenant_id), str(project.id), rid, wf_id),
        suite=body.suite,
        fixtures=fixtures,
        baseline=baseline_ref,
        baseline_name=baseline_src.get("name") or baseline_src.get("source") or "",
        models=models,
        repetitions=body.repetitions,
        max_parallel=max_parallel,
        budget_usd=body.budget_usd,
        question_timeout_s=settings.agent_question_timeout_s,
        options={k: v for k, v in options.items() if k != "baseline"},
    )
    try:
        await gateway.start_eval(wf_id, arg)
    except GatewayError as e:  # the row exists as queued: do not leave it holding a quota slot
        log.warning("start of evaluation %s failed: %s", rid, e)
        await runs.transition(
            rid, RunStatus.failed, error={"type": "StartFailed", "message": str(e)[:500], "retryable": True}
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "workflow engine unavailable") from e
    return {**run_out(await runs.get(rid)), "suite": body.suite, "children": []}


# --- read ------------------------------------------------------------------------------------------------


@router.get(
    "/baselines",
    response_model=list[BaselineOut],
    summary="Promoted baselines, newest first",
    responses={},
)
async def list_baselines(
    suite: str | None = Query(default=None, max_length=60),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT id, suite, run_id, sha256, name, promoted_by, promoted_at FROM eval_baselines "
                        "WHERE tenant_id = :t AND (CAST(:su AS text) IS NULL OR suite = :su) "
                        "ORDER BY promoted_at DESC, id DESC LIMIT :n"
                    ),
                    {"t": ctx.tenant_id, "su": suite, "n": limit},
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


@router.get(
    "",
    response_model=Page[EvalSummaryOut],
    summary="List evaluations, newest first",
)
async def list_evals(
    status_: RunStatusT | None = Query(default=None, alias="status"),
    suite: str | None = Query(default=None, max_length=60),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: uuid.UUID | None = None,
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        rows = (
            (
                await s.execute(
                    text(
                        f"SELECT {_SUMMARY_COLS}, COALESCE(options->>'suite', '') AS suite FROM runs "
                        "WHERE tenant_id = :t AND kind = 'eval' "
                        "AND (CAST(:su AS text) IS NULL OR options->>'suite' = :su) "
                        "AND (CAST(:st AS text) IS NULL OR status::text = :st) "
                        "AND (CAST(:c AS uuid) IS NULL OR id < CAST(:c AS uuid)) ORDER BY id DESC LIMIT :n"
                    ),
                    {"t": ctx.tenant_id, "su": suite, "st": status_, "c": cursor, "n": limit + 1},
                )
            )
            .mappings()
            .all()
        )
    items = [dict(r) for r in rows[:limit]]
    return {"items": items, "next_cursor": str(items[-1]["id"]) if len(rows) > limit else None}


@router.get(
    "/{eval_id}",
    response_model=EvalOut,
    summary="An evaluation: its stages (one per trial) and its design runs",
    responses={404: {"model": ErrorOut}},
)
async def get_eval(
    eval_id: uuid.UUID, ctx: TenantContext = Depends(tenant_context), db: Database = Depends(get_db)
):
    run = await _eval_run(db, ctx, eval_id)
    return {
        **run_out(run),
        "suite": run.options.get("suite", ""),
        "children": await _children(db, ctx, eval_id),
    }


async def _download(
    request: Request, ctx: TenantContext, db: Database, eval_id: uuid.UUID, name: str, media: str
):
    await _eval_run(db, ctx, eval_id)
    row = await _artifact_row(db, ctx, eval_id, name)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no report yet")
    store = tenant_store(request, ctx)

    def read() -> bytes:
        with store.open(row["sha256"]) as f:
            return f.read()

    try:
        data = await asyncio.to_thread(read)
    except ArtifactNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no report yet") from e
    except StoreUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "object store unavailable") from e
    return Response(data, media_type=media)


@router.get(
    "/{eval_id}/report",
    summary="REPORT.md of the evaluation",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {}}}, 404: {"model": ErrorOut}},
)
async def get_eval_report(
    eval_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    return await _download(request, ctx, db, eval_id, "REPORT.md", "text/markdown; charset=utf-8")


@router.get(
    "/{eval_id}/result",
    summary="eval.json of the evaluation: trials, aggregate, comparison, verdict",
    responses={200: {"content": {"application/json": {}}}, 404: {"model": ErrorOut}},
)
async def get_eval_result(
    eval_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    return await _download(request, ctx, db, eval_id, "eval.json", "application/json")


@router.post(
    "/{eval_id}/promote-baseline",
    response_model=BaselineOut,
    status_code=status.HTTP_201_CREATED,
    summary="Make this evaluation the baseline of its suite",
    description=(
        "Explicit and admin only: a baseline is never promoted automatically. The next evaluation of the suite "
        "compares with it unless it names another baseline. The history of promotions is kept."
    ),
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
async def promote_baseline(
    eval_id: uuid.UUID,
    body: BaselineIn,
    ctx: TenantContext = Depends(require_admin),
    db: Database = Depends(get_db),
):
    run = await _eval_run(db, ctx, eval_id)
    if run.status.value != "succeeded":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"evaluation is {run.status.value}; only a finished one can be promoted"
        )
    row = await _artifact_row(db, ctx, eval_id, "eval.json")
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "evaluation has no result")
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        out = (
            (
                await s.execute(
                    text(
                        "INSERT INTO eval_baselines (id, tenant_id, suite, run_id, sha256, name, promoted_by) "
                        "VALUES (:i, :t, :su, :r, :sha, :n, :u) "
                        "RETURNING id, suite, run_id, sha256, name, promoted_by, promoted_at"
                    ),
                    {
                        "i": uuid7(),
                        "t": ctx.tenant_id,
                        "su": run.options.get("suite", ""),
                        "r": eval_id,
                        "sha": row["sha256"],
                        "n": body.name or f"{run.options.get('suite', '')} {run.run_id[:8]}",
                        "u": ctx.user_id,
                    },
                )
            )
            .mappings()
            .one()
        )
    return dict(out)
