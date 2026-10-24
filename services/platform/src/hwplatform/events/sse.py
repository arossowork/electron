"""Server-sent events for the API (ADR 0001, 5.5).

The outbox is the source of truth, NATS JetStream is the low-latency path:

1. subscribe to an ephemeral ordered consumer on `hwflow.<tenant_id>.>` (new messages only) BEFORE reading, so
   nothing committed after the read can be missed;
2. replay from the outbox (`seq > Last-Event-ID`, tenant and run filtered, RLS applies on top);
3. then follow the live messages. A live message whose `seq` is the next one is sent as it is, one at or below the
   last sent is dropped, one beyond a gap triggers a replay of the gap from the outbox. Every `sse_poll_s` the
   outbox is read again, so a stream still works when NATS is down or the relay is late.

Per-run streams use `id: <seq>` and close after run.completed / run.failed / run.cancelled. The tenant and
project streams use `id: <outbox id>` (a live message is mapped to its outbox row by event id) and start from
"now" unless Last-Event-ID is sent. A comment line keeps idle connections open.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator

from sqlalchemy import text

from ..db import Database
from ..settings import Settings
from . import STREAM

log = logging.getLogger("hwplatform.sse")

TERMINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled"})
REPLAY_BATCH = 500


def sse_frame(event_id: int, type_: str, envelope: dict) -> str:
    return f"id: {event_id}\nevent: {type_}\ndata: {json.dumps(envelope, separators=(',', ':'))}\n\n"


# --- the live side ----------------------------------------------------------------------------------


class LiveSubscription:
    def __init__(self, sub, tenant_id: str):
        self._sub, self._tenant = sub, tenant_id

    async def next(self, wait: float) -> dict | None:
        """The next envelope of this tenant, or None after `wait` seconds."""
        from nats.errors import TimeoutError as NatsTimeout

        end = time.monotonic() + wait
        while (left := end - time.monotonic()) > 0:
            try:
                msg = await self._sub.next_msg(timeout=left)
            except NatsTimeout:
                return None
            try:
                env = json.loads(msg.data)
            except ValueError:
                continue
            if isinstance(env, dict) and env.get("tenantid") == self._tenant:  # defense in depth
                return env
        return None

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._sub.unsubscribe()


class NatsEventBus:
    """One shared NATS connection; one ephemeral ordered consumer per SSE request."""

    def __init__(self, url: str):
        self.url = url
        self._nc = None
        self._lock = asyncio.Lock()

    async def _conn(self):
        async with self._lock:
            if self._nc is None or self._nc.is_closed:
                import nats

                async def quiet(*_a) -> None:
                    return None

                self._nc = await asyncio.wait_for(
                    nats.connect(self.url, connect_timeout=3, max_reconnect_attempts=-1, error_cb=quiet),
                    timeout=5,
                )
            return self._nc

    async def subscribe(self, tenant_id: uuid.UUID | str) -> LiveSubscription | None:
        """None when NATS cannot be reached: the caller falls back to polling the outbox."""
        from nats.js.api import DeliverPolicy

        tid = str(uuid.UUID(str(tenant_id)))
        try:
            nc = await self._conn()
            sub = await nc.jetstream().subscribe(
                f"hwflow.{tid}.>",
                stream=STREAM,
                ordered_consumer=True,
                deliver_policy=DeliverPolicy.NEW,
            )
        except Exception as e:
            log.warning("SSE: no live feed (%s: %s); polling the outbox", type(e).__name__, e)
            return None
        return LiveSubscription(sub, tid)

    async def close(self) -> None:
        if self._nc is not None:
            with contextlib.suppress(Exception):
                await self._nc.close()
            self._nc = None


# --- outbox queries ---------------------------------------------------------------------------------


async def _run_rows(db: Database, tenant_id: uuid.UUID, run_id: uuid.UUID, after: int):
    async with db.tx(tenant_id=tenant_id) as s:
        return (
            (
                await s.execute(
                    text(
                        "SELECT seq, type, envelope FROM outbox WHERE tenant_id = :t AND run_id = :r AND seq > :a "
                        "ORDER BY seq LIMIT :n"
                    ),
                    {"t": tenant_id, "r": run_id, "a": after, "n": REPLAY_BATCH},
                )
            )
            .mappings()
            .all()
        )


def _env(v) -> dict:
    return json.loads(v) if isinstance(v, str) else v


async def stream_run(
    db: Database,
    bus: NatsEventBus | None,
    settings: Settings,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    last_event_id: int,
) -> AsyncIterator[str]:
    """SSE frames of one run, in seq order, closing after the terminal event."""
    live = await bus.subscribe(tenant_id) if bus else None
    last = max(last_event_id, 0)
    sent_at = time.monotonic()
    polled_at = 0.0
    try:
        while True:
            # outbox first: replay, gap fill, poll
            rows = await _run_rows(db, tenant_id, run_id, last)
            polled_at = time.monotonic()
            for r in rows:
                last = r["seq"]
                yield sse_frame(last, r["type"], _env(r["envelope"]))
                sent_at = time.monotonic()
                if r["type"] in TERMINAL_EVENTS:
                    return
            if len(rows) == REPLAY_BATCH:
                continue
            # then wait for something new
            while True:
                now = time.monotonic()
                wait = min(sent_at + settings.sse_keepalive_s, polled_at + settings.sse_poll_s) - now
                if wait <= 0:
                    break
                env = await live.next(wait) if live else await _sleep_none(wait)
                if env is None:
                    continue
                if env.get("runid") != str(run_id) or not isinstance(env.get("seq"), int):
                    continue
                if env["seq"] <= last:
                    continue
                if env["seq"] == last + 1:
                    last = env["seq"]
                    yield sse_frame(last, env["type"], env)
                    sent_at = time.monotonic()
                    if env["type"] in TERMINAL_EVENTS:
                        return
                else:
                    break  # a gap: read it from the outbox
            if time.monotonic() - sent_at >= settings.sse_keepalive_s:
                yield ": keep-alive\n\n"
                sent_at = time.monotonic()
    finally:
        if live:
            await live.close()


async def _sleep_none(seconds: float) -> None:
    await asyncio.sleep(seconds)
    return None


_PROJECT_SQL = (
    "(CAST(:p AS uuid) IS NULL OR (o.envelope->>'subject') LIKE 'projects/' || CAST(:p AS text) || '/%' "
    "OR o.run_id IN (SELECT id FROM runs WHERE project_id = CAST(:p AS uuid)))"
)


async def stream_tenant(
    db: Database,
    bus: NatsEventBus | None,
    settings: Settings,
    tenant_id: uuid.UUID,
    last_event_id: int | None,
    project_id: uuid.UUID | None = None,
) -> AsyncIterator[str]:
    """SSE frames of every event of the tenant (or of one project), `id:` = outbox id. Without Last-Event-ID
    it starts at the current end of the outbox."""
    live = await bus.subscribe(tenant_id) if bus else None
    seen: deque[int] = deque(maxlen=2000)
    seen_set: set[int] = set()
    project_cache: dict[str, str | None] = {}

    def mark(i: int) -> bool:
        if i in seen_set:
            return False
        if len(seen) == seen.maxlen:
            seen_set.discard(seen[0])
        seen.append(i)
        seen_set.add(i)
        return True

    async def project_of(env: dict) -> str | None:
        subj = env.get("subject", "")
        if subj.startswith("projects/"):
            return subj.split("/")[1]
        rid = env.get("runid")
        if not rid:
            return None
        if rid not in project_cache:
            async with db.tx(tenant_id=tenant_id) as s:
                v = (
                    await s.execute(text("SELECT project_id FROM runs WHERE id = :r"), {"r": uuid.UUID(rid)})
                ).scalar_one_or_none()
            project_cache[rid] = str(v) if v else None
        return project_cache[rid]

    pid = str(project_id) if project_id else None
    try:
        if last_event_id is None:
            async with db.tx(tenant_id=tenant_id) as s:
                last = (
                    await s.execute(
                        text("SELECT coalesce(max(id), 0) FROM outbox WHERE tenant_id = :t"), {"t": tenant_id}
                    )
                ).scalar_one()
        else:
            last = last_event_id
        sent_at = time.monotonic()
        polled_at = 0.0
        while True:
            async with db.tx(tenant_id=tenant_id) as s:
                rows = (
                    (
                        await s.execute(
                            text(
                                "SELECT o.id, o.type, o.envelope FROM outbox o WHERE o.tenant_id = :t AND o.id > :a AND "
                                + _PROJECT_SQL
                                + " ORDER BY o.id LIMIT :n"
                            ),
                            {"t": tenant_id, "a": last, "p": pid, "n": REPLAY_BATCH},
                        )
                    )
                    .mappings()
                    .all()
                )
            polled_at = time.monotonic()
            for r in rows:
                last = max(last, r["id"])
                if mark(r["id"]):
                    yield sse_frame(r["id"], r["type"], _env(r["envelope"]))
                    sent_at = time.monotonic()
            if len(rows) == REPLAY_BATCH:
                continue
            while True:
                now = time.monotonic()
                wait = min(sent_at + settings.sse_keepalive_s, polled_at + settings.sse_poll_s) - now
                if wait <= 0:
                    break
                env = await live.next(wait) if live else await _sleep_none(wait)
                if env is None:
                    continue
                if pid is not None and await project_of(env) != pid:
                    continue
                async with db.tx(tenant_id=tenant_id) as s:
                    oid = (
                        await s.execute(
                            text("SELECT id FROM outbox WHERE tenant_id = :t AND event_id = :e"),
                            {"t": tenant_id, "e": uuid.UUID(env["id"])},
                        )
                    ).scalar_one_or_none()
                if oid is None or not mark(oid):
                    continue
                last = max(last, oid)
                yield sse_frame(oid, env["type"], env)
                sent_at = time.monotonic()
            if time.monotonic() - sent_at >= settings.sse_keepalive_s:
                yield ": keep-alive\n\n"
                sent_at = time.monotonic()
    finally:
        if live:
            await live.close()
