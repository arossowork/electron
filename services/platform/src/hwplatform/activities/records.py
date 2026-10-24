"""record_run and record_stage: the workflow's local activities (ADR 0001, 4.4). Each is one PgRunStore call,
so the row change and its outbox event (run.*, stage.*) commit in one transaction. Both are idempotent: a
retried call after a commit whose answer was lost changes nothing (an illegal repeat transition reports the
current status; a stage upsert of the same state emits no second event)."""

from __future__ import annotations

import logging
from datetime import datetime

from hwflow.runs import TERMINAL, InvalidTransition, RunStatus, StageRecord, StageStatus
from temporalio import activity

from ..contracts import A_RECORD_RUN, A_RECORD_STAGE, RecordedRun, RecordRun, RecordStage
from ..runs import PgRunStore
from .deps import Deps

log = logging.getLogger(__name__)


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


class Records:
    def __init__(self, deps: Deps):
        self.deps = deps

    @activity.defn(name=A_RECORD_RUN)
    async def record_run(self, r: RecordRun) -> RecordedRun:
        runs = PgRunStore(self.deps.db, r.ctx.tenant_id)
        try:
            run = await runs.transition(
                r.ctx.run_id,
                RunStatus(r.to),
                error=r.error,
                verdict=r.verdict,
                event_data={"by": r.by} if r.by else None,
            )
            applied = True
        except InvalidTransition:
            run = await runs.get(r.ctx.run_id)
            applied = False
        if run.status.value in TERMINAL and applied:
            try:  # the run's manifest, from its rows, once it is final (ADR 3.4)
                await runs.store_manifest(r.ctx.run_id, self.deps.store(r.ctx.tenant_id))
            except Exception as e:  # noqa: BLE001 - the transition is what counts; the manifest is a view
                log.warning("run %s: manifest not stored: %s", r.ctx.run_id, type(e).__name__)
        return RecordedRun(status=run.status.value, applied=applied)

    @activity.defn(name=A_RECORD_STAGE)
    async def record_stage(self, r: RecordStage) -> None:
        o = r.out
        outputs = {}
        summary = {"reason": r.reason} if r.reason else {}
        if o is not None:
            outputs = dict(o.outputs)
            outputs.update({f"state/{k}": v for k, v in o.state.items()})
            summary = dict(o.summary)
        rec = StageRecord(
            key=r.key,
            stage=r.stage,
            seq=r.seq,
            status=StageStatus(r.status),
            verdict=o.verdict if o else None,
            cached=o.cached if o else False,
            cache_key=(o.cache_key or None) if o else None,
            attempt=r.attempt,
            started_at=_dt(r.started_at),
            ended_at=_dt(r.ended_at),
            seconds=o.seconds if o else None,
            summary=summary,
            result=o.result if o else None,
            outputs=outputs,
            error=r.error,
        )
        await PgRunStore(self.deps.db, r.ctx.tenant_id).upsert_stage(r.ctx.run_id, rec)
