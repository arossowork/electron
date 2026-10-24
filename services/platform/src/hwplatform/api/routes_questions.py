"""Answer a question of a run: write the answer, then signal the workflow (ADR 0001, sections 6 and 7.3)."""

from __future__ import annotations

import dataclasses
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text

from ..contracts import QuestionAnswer
from ..db import Database
from ..gateway import GatewayError, WorkflowGateway
from ..questions import QuestionNotFound, QuestionNotOpen, QuestionStore
from ..tenancy import TenantContext
from .deps import get_db, get_gateway, tenant_context
from .schemas import AnswerIn, ErrorOut, QuestionOut

log = logging.getLogger("hwplatform.api.questions")
router = APIRouter(tags=["questions"])


@router.post(
    "/questions/{question_id}/answer",
    response_model=QuestionOut,
    summary="Answer a question",
    description=(
        "Writes the answer, then signals `answer_question` to the run's workflow. 409 when the question is not "
        "open (already answered, expired, cancelled) or its run has ended; 422 for an answer to an unknown "
        "question id or a missing answer for a question without a default."
    ),
    responses={
        404: {"model": ErrorOut},
        409: {"model": ErrorOut},
        422: {"model": ErrorOut},
        503: {"model": ErrorOut},
    },
)
async def answer_question(
    question_id: uuid.UUID,
    body: AnswerIn,
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
    gateway: WorkflowGateway = Depends(get_gateway),
):
    qs = QuestionStore(db, ctx.tenant_id)
    try:
        q = await qs.get(question_id)
    except QuestionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "question not found") from e
    if q.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, f"question is {q.status}")
    ids = {x.get("id") for x in q.questions}
    unknown = sorted(set(body.answers) - ids)
    if unknown:
        raise HTTPException(422, f"unknown question ids: {', '.join(unknown)}")
    missing = sorted(
        x["id"] for x in q.questions if x.get("id") not in body.answers and x.get("default") is None
    )
    if missing:
        raise HTTPException(422, f"no answer for: {', '.join(missing)}")
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        row = (
            await s.execute(
                text("SELECT status::text AS status, workflow_id FROM runs WHERE tenant_id = :t AND id = :r"),
                {"t": ctx.tenant_id, "r": uuid.UUID(q.run_id)},
            )
        ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "question not found")
    if row.status in ("succeeded", "failed", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"run already {row.status}")
    try:
        done = await qs.answer(question_id, body.answers, ctx.user_id)
    except QuestionNotOpen as e:  # answered by someone else in between
        raise HTTPException(status.HTTP_409_CONFLICT, "question is not open") from e
    try:
        await gateway.signal_answer(
            row.workflow_id,
            QuestionAnswer(question_id=str(question_id), answers=body.answers, answered_by=str(ctx.user_id)),
        )
    except GatewayError as e:  # nobody was woken: let the person answer again
        log.warning("signal for question %s failed: %s", question_id, e)
        await qs.reopen(question_id)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "workflow engine unavailable") from e
    return dataclasses.asdict(done)
