"""The DesignWorkflow's record and design-version activities (ADR 0001, 4.4 and 7.3).

- `record_question` (local): the question row and its events, and the run's waiting/running transitions, each
  change with its outbox event in one transaction. Idempotent: the question id comes from the workflow
  (`workflow.uuid4()`), so a retried ask inserts nothing new, and a repeated transition reports what is there.
- `get_design_version` / `create_design_version` (engine queue): the version a run starts from, and a new
  version (source "agent") from a workspace tree after a stage that changed it.
"""

from __future__ import annotations

import logging

from hwflow.runs import InvalidTransition, RunStatus
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..contracts import (
    A_CREATE_DESIGN_VERSION,
    A_GET_DESIGN_VERSION,
    A_RECORD_QUESTION,
    DesignVersionInput,
    DesignVersionOut,
    RecordQuestion,
    errors,
)
from ..designs import UploadError, version_from_tree
from ..questions import QuestionStore
from ..runs import PgRunStore
from .deps import Deps

log = logging.getLogger(__name__)


class DesignActivities:
    def __init__(self, deps: Deps):
        self.deps = deps

    async def _transition(self, r: RecordQuestion, to: RunStatus, **data) -> None:
        try:
            await PgRunStore(self.deps.db, r.ctx.tenant_id).transition(r.ctx.run_id, to, event_data=data)
        except InvalidTransition:
            pass  # already there (a retried call), or the run was cancelled meanwhile: the workflow sees it

    @activity.defn(name=A_RECORD_QUESTION)
    async def record_question(self, r: RecordQuestion) -> str:
        qs = QuestionStore(self.deps.db, r.ctx.tenant_id)
        if r.op == "ask":
            q = await qs.ask(r.ctx.run_id, r.stage, list(r.questions), question_id=r.question_id)
            await self._transition(r, RunStatus.waiting, reason="question", question_id=q.id)
            return q.id
        if r.op == "resumed":
            await self._transition(r, RunStatus.running, reason="question", question_id=r.question_id)
        elif r.op in ("expired", "cancelled"):
            await qs.set_status(r.question_id, r.op)
        elif r.op == "auto_answered":  # the timeout passed on a non-critical question (#54)
            done = await qs.auto_answer(r.question_id, dict(r.answers))
            return "auto" if done is not None else "human"
        elif r.op == "critical_wait":  # ... on a critical one: tell the UI, keep waiting
            await qs.waiting_critical(r.question_id)
        elif r.op == "key_wait":
            await self._transition(r, RunStatus.waiting, reason="llm_key_invalid")
        elif r.op == "key_resumed":
            await self._transition(r, RunStatus.running, reason="llm_key_invalid")
        else:
            raise ApplicationError(f"unknown record_question op {r.op!r}", non_retryable=True)
        return r.question_id or ""

    @activity.defn(name=A_GET_DESIGN_VERSION)
    async def get_design_version(self, r: DesignVersionInput) -> DesignVersionOut:
        dv = await PgRunStore(self.deps.db, r.ctx.tenant_id).get_design_version(r.design_version_id)
        return DesignVersionOut(dv.id, dv.content_sha256, dv.design, dv.project)

    @activity.defn(name=A_CREATE_DESIGN_VERSION)
    async def create_design_version(self, r: DesignVersionInput) -> DesignVersionOut:
        try:
            dv, created = await version_from_tree(
                self.deps.db,
                self.deps.store(r.ctx.tenant_id),
                r.ctx.tenant_id,
                r.ctx.project_id,
                r.workspace,
                source="agent",
                created_by_run=r.ctx.run_id,
                parent_id=r.parent_id,
                message=r.message,
            )
        except UploadError as e:  # the agent left no valid design.yaml: a design problem, not an infra one
            raise ApplicationError(str(e), type=errors.DesignInvalid, non_retryable=True) from None
        return DesignVersionOut(dv.id, dv.content_sha256, dv.design, dv.project, created)
