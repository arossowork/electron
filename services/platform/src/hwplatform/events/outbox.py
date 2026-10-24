"""Write an event to the outbox inside the caller's transaction (ADR 0001, 5.3).

`emit` runs in the same session (= the same DB transaction) as the state change it describes: the state
and its event commit together or not at all. Nothing here talks to NATS; the relay does, after commit.
Tool activities never emit.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import DEFAULT_SOURCE, Event


async def emit(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    run_id: uuid.UUID | str | None,
    type: str,
    subject: str,
    data: dict,
    *,
    source: str = DEFAULT_SOURCE,
) -> Event:
    """Insert one outbox row. With a run, its `seq` is the run's next event_seq (taken under the row lock,
    so seq is gapless and ordered even with concurrent writers); without one, seq is NULL."""
    seq = None
    if run_id is not None:
        seq = (
            await session.execute(
                text("UPDATE runs SET event_seq = event_seq + 1 WHERE id = :r RETURNING event_seq"),
                {"r": uuid.UUID(str(run_id))},
            )
        ).scalar_one_or_none()
        if seq is None:
            raise LookupError(f"run {run_id} not found (or not in this tenant)")
    ev = Event(
        type=type,
        tenantid=str(uuid.UUID(str(tenant_id))),
        subject=subject,
        data=data,
        runid=str(run_id) if run_id is not None else None,
        seq=seq,
        source=source,
    )
    await session.execute(
        text(
            "INSERT INTO outbox (event_id, tenant_id, run_id, seq, type, subject, envelope) "
            "VALUES (:eid, :tid, :rid, :seq, :type, :subject, CAST(:env AS jsonb))"
        ),
        {
            "eid": uuid.UUID(ev.id),
            "tid": uuid.UUID(ev.tenantid),
            "rid": uuid.UUID(ev.runid) if ev.runid else None,
            "seq": seq,
            "type": type,
            "subject": ev.nats_subject,
            "env": json.dumps(ev.to_envelope(), separators=(",", ":")),
        },
    )
    return ev
