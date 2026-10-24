"""Server-sent events: one run, one project, the whole tenant (ADR 0001, 5.5)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ..db import Database
from ..events.sse import stream_run, stream_tenant
from ..tenancy import ProjectRepo, TenantContext
from .deps import get_db, tenant_context
from .routes_runs import load_run
from .schemas import ErrorOut

router = APIRouter(tags=["events"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
_SSE_DOC = {
    200: {"content": {"text/event-stream": {}}, "description": "Server-sent events"},
    404: {"model": ErrorOut},
}


def _last_id(header: str | None, query: int | None) -> int | None:
    """Last-Event-ID header (what EventSource resends) or ?last_event_id=; None when neither is a number."""
    for v in (header, query):
        if v is not None:
            try:
                return max(int(v), 0)
            except (TypeError, ValueError):
                continue
    return None


def _response(gen) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get(
    "/runs/{run_id}/events",
    summary="Events of one run (SSE)",
    description=(
        "Replays the run's events from the outbox after `Last-Event-ID` (or `?last_event_id=`), then follows "
        "live events from NATS JetStream. `id:` is the per-run `seq`, `event:` the type, `data:` the CloudEvents "
        "envelope. A comment line every 15 s keeps the connection open; the stream ends after run.completed, "
        "run.failed or run.cancelled."
    ),
    responses=_SSE_DOC,
)
async def run_events(
    run_id: uuid.UUID,
    request: Request,
    last_event_id: str | None = Header(default=None),
    last_event_id_q: int | None = Query(default=None, alias="last_event_id"),
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    await load_run(db, ctx, run_id)
    st = request.app.state
    after = _last_id(last_event_id, last_event_id_q) or 0
    return _response(stream_run(db, st.event_bus, st.settings, ctx.tenant_id, run_id, after))


@router.get(
    "/projects/{project_id}/events",
    summary="Events of one project's runs and design versions (SSE)",
    description="`id:` is the outbox id. Without Last-Event-ID the stream starts at the current end.",
    responses=_SSE_DOC,
)
async def project_events(
    project_id: uuid.UUID,
    request: Request,
    last_event_id: str | None = Header(default=None),
    last_event_id_q: int | None = Query(default=None, alias="last_event_id"),
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        if await ProjectRepo(s, ctx).get(project_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    st = request.app.state
    after = _last_id(last_event_id, last_event_id_q)
    return _response(stream_tenant(db, st.event_bus, st.settings, ctx.tenant_id, after, project_id))


@router.get(
    "/events",
    summary="Every event of the caller's tenant (SSE)",
    description="For notifications. `id:` is the outbox id. Without Last-Event-ID the stream starts at the current end.",
    responses={200: _SSE_DOC[200]},
)
async def tenant_events(
    request: Request,
    last_event_id: str | None = Header(default=None),
    last_event_id_q: int | None = Query(default=None, alias="last_event_id"),
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    st = request.app.state
    after = _last_id(last_event_id, last_event_id_q)
    return _response(stream_tenant(db, st.event_bus, st.settings, ctx.tenant_id, after))
