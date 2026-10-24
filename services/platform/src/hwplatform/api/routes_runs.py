"""Runs: start (with quota admission), list, get, cancel, manifest, report, artifacts (ADR 0001, sections 4.8 and 6)."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from hwflow.artifacts import ArtifactNotFound, StoreUnavailable
from hwflow.ids import uuid7
from hwflow.runs import TERMINAL, InvalidTransition, Run, RunStatus
from sqlalchemy import text

from .. import run_options
from ..contracts import BuildInput, DesignInput, RunCtx
from ..db import Database
from ..gateway import (
    GatewayError,
    WorkflowGateway,
    WorkflowNotFound,
    build_workflow_id,
    design_workflow_id,
)
from ..questions import QuestionStore
from ..runs import PgRunStore, QuotaExceeded, RunNotFound
from ..tenancy import ProjectRepo, TenantContext
from .deps import get_db, get_gateway, tenant_context, tenant_store
from .downloads import serve_object
from .schemas import (
    ArtifactOut,
    ErrorOut,
    Page,
    QuestionOut,
    RunIn,
    RunKindT,
    RunOut,
    RunStatusT,
    RunSummaryOut,
    StageOut,
)

log = logging.getLogger("hwplatform.api.runs")
router = APIRouter(tags=["runs"])

_NO_RUN = "run not found"
_NO_PROJECT = "project not found"


def run_out(run: Run) -> dict:
    """Run (contracts) -> RunOut."""
    return {
        "id": run.run_id,
        "project_id": run.project_id,
        "design_version_id": run.design_version.id,
        "kind": run.kind,
        "status": run.status.value,
        "verdict": run.verdict,
        "options_hash": run.options_hash,
        "parent_run_id": run.parent_run_id,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "workflow_id": run.workflow_id,
        "options": run.options,
        "error": run.error,
        "artifacts": sorted(run.artifacts),
        "stages": [
            StageOut(
                key=s.key,
                stage=s.stage,
                seq=s.seq,
                status=s.status.value,
                verdict=s.verdict,
                cached=s.cached,
                attempt=s.attempt,
                started_at=s.started_at,
                ended_at=s.ended_at,
                seconds=s.seconds,
                summary=s.summary,
                error=s.error,
            )
            for s in run.stages
        ],
    }


async def load_run(db: Database, ctx: TenantContext, run_id: uuid.UUID) -> Run:
    """The run, or 404 (absent or another tenant's: RLS hides it)."""
    try:
        return await PgRunStore(db, ctx.tenant_id).get(str(run_id))
    except RunNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_RUN) from e


# --- start -------------------------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/runs",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a run",
    description=(
        "Starts a build (or design) run for a design version. Admission checks the tenant's "
        "`max_concurrent_runs` (429 `quota_exceeded`). Starting the same build again while it is active returns "
        "the active run with 200; so does repeating an `Idempotency-Key`."
    ),
    responses={
        200: {
            "description": "An identical run is already active, or the Idempotency-Key was used",
            "model": RunOut,
        },
        404: {"model": ErrorOut},
        422: {"model": ErrorOut},
        429: {"description": "quota_exceeded"},
        503: {"model": ErrorOut},
    },
)
async def start_run(
    project_id: uuid.UUID,
    body: RunIn,
    response: Response,
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
    gateway: WorkflowGateway = Depends(get_gateway),
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=200),
):
    try:
        if body.kind == "build":
            options = run_options.normalize_build_options(body.options)
            overrides = run_options.normalize_settings_overrides(body.settings_overrides)
        else:
            options = run_options.normalize_design_options(body.options)
            overrides = {}
            if body.settings_overrides:
                raise run_options.OptionsError("settings_overrides apply to builds only")
    except run_options.OptionsError as e:
        raise HTTPException(422, str(e)) from e

    store = PgRunStore(db, ctx.tenant_id)
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        if await ProjectRepo(s, ctx).get(project_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_PROJECT)
        in_project = (
            await s.execute(
                text("SELECT 1 FROM design_versions WHERE tenant_id = :t AND project_id = :p AND id = :i"),
                {"t": ctx.tenant_id, "p": project_id, "i": body.design_version_id},
            )
        ).first()
    if in_project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "design version not found")
    dv = await store.get_design_version(body.design_version_id)

    rid = str(uuid7())
    ohash = run_options.options_hash(options, overrides)
    if body.kind == "build":
        wf_id = build_workflow_id(str(ctx.tenant_id), str(project_id), dv.content_sha256, ohash)
    else:
        wf_id = design_workflow_id(str(ctx.tenant_id), str(project_id), rid)
    run = Run(
        run_id=rid,
        kind=body.kind,
        tenant_id=str(ctx.tenant_id),
        project_id=str(project_id),
        design_version=dv,
        status=RunStatus.queued,
        verdict=None,
        options=options,
        options_hash=ohash,
        settings=overrides,
        code={},
        tools={},
        stages=[],
        artifacts={},
        created_at=datetime.now(UTC),
        workflow_id=wf_id,
    )
    try:
        adm = await store.admit(run, idempotency_key=idempotency_key, requested_by=ctx.user_id)
    except QuotaExceeded as e:
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
        response.status_code = status.HTTP_200_OK
        return run_out(adm.run)

    ctx_arg = RunCtx(str(ctx.tenant_id), str(project_id), rid, wf_id)
    try:
        if body.kind == "build":
            await gateway.start_build(
                wf_id,
                BuildInput(
                    ctx=ctx_arg,
                    design_version_id=dv.id,
                    design=dv.design,
                    project=dv.project,
                    options=options,
                    settings_overrides=overrides,
                ),
            )
        else:
            await gateway.start_design(
                wf_id,
                DesignInput(
                    ctx=ctx_arg,
                    design_version_id=dv.id,
                    brief=options["brief"],
                    answerer=options["answerer"],
                    stages=options["stages"],
                ),
            )
    except GatewayError as e:  # the row exists as queued: do not leave it holding a quota slot
        log.warning("start of run %s failed: %s", rid, e)
        await store.transition(
            rid, RunStatus.failed, error={"type": "StartFailed", "message": str(e)[:500], "retryable": True}
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "workflow engine unavailable") from e
    return run_out(adm.run)


# --- read --------------------------------------------------------------------------------------------

_SUMMARY_COLS = (
    "id, project_id, design_version_id, kind::text AS kind, status::text AS status, verdict, options_hash, "
    "parent_run_id, created_at, started_at, ended_at"
)


@router.get(
    "/projects/{project_id}/runs",
    response_model=Page[RunSummaryOut],
    summary="List runs of a project, newest first",
    responses={404: {"model": ErrorOut}},
)
async def list_runs(
    project_id: uuid.UUID,
    kind: RunKindT | None = None,
    status_: RunStatusT | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: uuid.UUID | None = None,
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        if await ProjectRepo(s, ctx).get(project_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_PROJECT)
        rows = (
            (
                await s.execute(
                    text(
                        f"SELECT {_SUMMARY_COLS} FROM runs WHERE tenant_id = :t AND project_id = :p "
                        "AND (CAST(:k AS text) IS NULL OR kind::text = :k) "
                        "AND (CAST(:st AS text) IS NULL OR status::text = :st) "
                        "AND (CAST(:c AS uuid) IS NULL OR id < CAST(:c AS uuid)) ORDER BY id DESC LIMIT :n"
                    ),
                    {
                        "t": ctx.tenant_id,
                        "p": project_id,
                        "k": kind,
                        "st": status_,
                        "c": cursor,
                        "n": limit + 1,
                    },
                )
            )
            .mappings()
            .all()
        )
    items = [dict(r) for r in rows[:limit]]
    return {"items": items, "next_cursor": str(items[-1]["id"]) if len(rows) > limit else None}


@router.get(
    "/runs/{run_id}",
    response_model=RunOut,
    summary="A run with its stages and verdict",
    responses={404: {"model": ErrorOut}},
)
async def get_run(
    run_id: uuid.UUID, ctx: TenantContext = Depends(tenant_context), db: Database = Depends(get_db)
):
    return run_out(await load_run(db, ctx, run_id))


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Cancel a run",
    description="Asks the workflow to cancel. A queued run (no workflow has started it) turns `cancelled` at once; a running one turns `cancelled` when the workflow has stopped its work.",
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 503: {"model": ErrorOut}},
)
async def cancel_run(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
    gateway: WorkflowGateway = Depends(get_gateway),
):
    run = await load_run(db, ctx, run_id)
    if run.status.value in TERMINAL:
        raise HTTPException(status.HTTP_409_CONFLICT, f"run already {run.status.value}")
    try:
        await gateway.cancel(run.workflow_id or "")
    except WorkflowNotFound:  # no workflow to stop it (never started, history gone)
        pass
    except GatewayError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "workflow engine unavailable") from e
    # ADR 4.6: a running workflow records `cancelled` itself once its work has stopped. A run still `queued`
    # has no workflow task running (no worker yet, or no workflow at all), so nobody else will record it:
    # transition it here, which also emits run.cancelled. The cancel request stays on the workflow (if any), so
    # a worker that picks it up later stops at once; its record_run(running) then hits a terminal state.
    run = await load_run(db, ctx, run_id)
    if run.status.value == "queued":
        try:
            run = await PgRunStore(db, ctx.tenant_id).transition(
                run.run_id, RunStatus.cancelled, event_data={"by": str(ctx.user_id)}
            )
        except InvalidTransition:  # the workflow moved it on in the meantime: report what it is now
            run = await load_run(db, ctx, run_id)
    return run_out(run)


@router.get(
    "/runs/{run_id}/manifest",
    summary="manifest.json of the run (rendered from the rows, live while it runs)",
    responses={404: {"model": ErrorOut}},
)
async def get_manifest(
    run_id: uuid.UUID, ctx: TenantContext = Depends(tenant_context), db: Database = Depends(get_db)
):
    try:
        return await PgRunStore(db, ctx.tenant_id).manifest(str(run_id))
    except RunNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_RUN) from e


async def _artifact_row(db: Database, ctx: TenantContext, run_id: uuid.UUID, name: str):
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        return (
            (
                await s.execute(
                    text(
                        "SELECT a.name, a.sha256, a.size, a.media_type FROM run_artifacts a "
                        "WHERE a.tenant_id = :t AND a.run_id = :r AND a.name = :n"
                    ),
                    {"t": ctx.tenant_id, "r": run_id, "n": name},
                )
            )
            .mappings()
            .first()
        )


@router.get(
    "/runs/{run_id}/report",
    summary="REPORT.md of the run",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {}}}, 404: {"model": ErrorOut}},
)
async def get_report(
    run_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    await load_run(db, ctx, run_id)
    row = await _artifact_row(db, ctx, run_id, "REPORT.md")
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
    return Response(data, media_type="text/markdown; charset=utf-8")


@router.get(
    "/runs/{run_id}/artifacts",
    response_model=list[ArtifactOut],
    summary="Artifacts of a run",
    responses={404: {"model": ErrorOut}},
)
async def list_artifacts(
    run_id: uuid.UUID, ctx: TenantContext = Depends(tenant_context), db: Database = Depends(get_db)
):
    await load_run(db, ctx, run_id)
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT name, stage_key, sha256, size, media_type FROM run_artifacts "
                        "WHERE tenant_id = :t AND run_id = :r ORDER BY name"
                    ),
                    {"t": ctx.tenant_id, "r": run_id},
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


@router.get(
    "/runs/{run_id}/artifacts/{name:path}",
    summary="Download an artifact of a run",
    description="302 to a presigned URL (TTL 300 s); the bytes when the API has no public S3 URL.",
    status_code=status.HTTP_302_FOUND,
    responses={404: {"model": ErrorOut}},
)
async def download_artifact(
    run_id: uuid.UUID,
    name: str,
    request: Request,
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    await load_run(db, ctx, run_id)
    row = await _artifact_row(db, ctx, run_id, name)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found")
    return await serve_object(
        request, ctx, row["sha256"], filename=name, media_type=row["media_type"] or "application/octet-stream"
    )


@router.get(
    "/runs/{run_id}/questions",
    tags=["questions"],
    response_model=list[QuestionOut],
    summary="Questions of a run (open and answered)",
    responses={404: {"model": ErrorOut}},
)
async def list_questions(
    run_id: uuid.UUID,
    status_: str | None = Query(default=None, alias="status", pattern="^(open|answered|expired|cancelled)$"),
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    await load_run(db, ctx, run_id)
    qs = await QuestionStore(db, ctx.tenant_id).list_for_run(run_id, status=status_)
    return [dataclasses.asdict(q) for q in qs]
