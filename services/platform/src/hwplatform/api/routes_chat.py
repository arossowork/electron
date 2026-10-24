"""Free chat with the agent of a design run (#54, ADR 0001 7.7): store the message, signal the workflow.

The agent reads the message at its next turn boundary (never in the middle of a tool call) and its reply is stored
as an `agent` message with an `agent.message` event; the web chat panel follows both events on the run's SSE stream.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from ..chat import ChatStore
from ..contracts import ChatSignal
from ..db import Database
from ..gateway import GatewayError, WorkflowGateway, WorkflowNotFound
from ..tenancy import TenantContext
from .deps import get_db, get_gateway, tenant_context
from .routes_runs import load_run
from .schemas import ChatIn, ChatMessageOut, ErrorOut

log = logging.getLogger("hwplatform.api.chat")
router = APIRouter(tags=["chat"])


@router.get(
    "/runs/{run_id}/chat",
    response_model=list[ChatMessageOut],
    summary="Chat messages of a run",
    description="The person's messages and the agent's replies, oldest first (at most 500).",
    responses={404: {"model": ErrorOut}},
)
async def list_chat(
    run_id: uuid.UUID, ctx: TenantContext = Depends(tenant_context), db: Database = Depends(get_db)
):
    await load_run(db, ctx, run_id)
    return [dataclasses.asdict(m) for m in await ChatStore(db, ctx.tenant_id).list_for_run(run_id)]


@router.post(
    "/runs/{run_id}/chat",
    response_model=ChatMessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Write to the agent",
    description=(
        "Stores the message and signals `chat` to the run's DesignWorkflow. The agent gets it at its next turn "
        "boundary (a message sent while a tool runs waits for the tool to end). 409 for a run that is not a "
        "design run or has ended; 503 when the workflow engine cannot be reached (nothing is stored)."
    ),
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 503: {"model": ErrorOut}},
)
async def post_chat(
    run_id: uuid.UUID,
    body: ChatIn,
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
    gateway: WorkflowGateway = Depends(get_gateway),
):
    run = await load_run(db, ctx, run_id)
    if run.kind != "design":
        raise HTTPException(status.HTTP_409_CONFLICT, "only a design run has an agent to talk to")
    if run.status.value in ("succeeded", "failed", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"run already {run.status.value}")
    store = ChatStore(db, ctx.tenant_id)
    msg = await store.post_user(run_id, body.text, ctx.user_id)
    try:
        await gateway.signal_chat(run.workflow_id, ChatSignal(message_id=msg.id))
    except (
        GatewayError,
        WorkflowNotFound,
    ) as e:  # nobody was told: do not keep a message the agent will never get
        log.warning("chat signal for run %s failed: %s", run_id, e)
        await store.discard(msg.id)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "workflow engine unavailable") from e
    return dataclasses.asdict(msg)
