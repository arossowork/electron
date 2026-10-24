"""Outbox relay: outbox rows -> NATS JetStream (ADR 0001, 5.4).

* Leader: one relay publishes at a time, chosen with the session advisory lock
  pg_try_advisory_lock(hashtext('hwflow.outbox.relay')) on the relay's own Postgres connection. A replica
  that does not get the lock keeps retrying; when the leader dies its session ends and the lock frees.
* Wakes on LISTEN outbox (trigger in migration 0005) or every `relay_poll_s`.
* A batch is SELECT ... WHERE published_at IS NULL ORDER BY id LIMIT n FOR UPDATE SKIP LOCKED. Each event is
  published to hwflow.<tenant_id>.<type> with header Nats-Msg-Id = event id and the JetStream ack is awaited,
  then published_at is set. A crash between ack and UPDATE republishes on restart; the stream drops the
  duplicate inside its duplicate_window (2 h). Delivery is therefore at least once, deduplicated to once.
* Ordering: rows are published in id order, but id is assigned at INSERT and a transaction with a lower id may
  commit after a higher one. Consumers that need exact order use the per-run `seq` (gapless) and the outbox.

The relay connects as the role hwflow_relay (see migration 0005) so it sees all tenants without BYPASSRLS.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import asyncpg
from nats.js.api import DiscardPolicy, RetentionPolicy, StorageType, StreamConfig

from ..observability import metrics
from ..settings import Settings
from . import STREAM

log = logging.getLogger("hwplatform.relay")

LOCK_NAME = "hwflow.outbox.relay"
MAX_BACKOFF_S = 60.0

# Not ready yet, not a bug: before the migrations ran the database, the outbox table or the hwflow_relay role
# does not exist; Postgres or NATS may be starting. These get one short warning, no traceback.
NOT_READY = (
    asyncpg.UndefinedTableError,
    asyncpg.InvalidCatalogNameError,
    asyncpg.InvalidAuthorizationSpecificationError,
    asyncpg.InvalidPasswordError,
    asyncpg.CannotConnectNowError,
    OSError,
    asyncio.TimeoutError,
)


def stream_config() -> StreamConfig:
    return StreamConfig(
        name=STREAM,
        subjects=["hwflow.>"],
        storage=StorageType.FILE,
        retention=RetentionPolicy.LIMITS,
        max_age=7 * 24 * 3600,  # seconds (nats-py converts to ns)
        max_bytes=512 * 1024 * 1024,
        discard=DiscardPolicy.OLD,
        duplicate_window=2 * 3600,
        num_replicas=1,
    )


async def ensure_stream(js) -> None:
    """Create the stream, or bring an existing one to this config. Safe to call from every process."""
    from nats.js.errors import BadRequestError, NotFoundError

    cfg = stream_config()
    for _ in range(3):
        try:
            await js.stream_info(STREAM)
        except NotFoundError:
            try:
                await js.add_stream(cfg)
                return
            except BadRequestError:  # created concurrently by another process: update it next round
                continue
        await js.update_stream(cfg)
        return
    raise RuntimeError(f"cannot create or update stream {STREAM}")


async def ensure_stream_at(nats_url: str) -> None:
    nc = await asyncio.wait_for(_connect(nats_url, allow_reconnect=False), timeout=15)
    try:
        await ensure_stream(nc.jetstream())
    finally:
        await nc.close()


async def _quiet(
    *_a,
) -> None:  # nats-py prints a traceback per failed attempt unless an error callback exists
    return None


async def _connect(url: str, *, allow_reconnect: bool):
    import nats

    return await nats.connect(
        url,
        connect_timeout=5,
        allow_reconnect=allow_reconnect,
        max_reconnect_attempts=-1 if allow_reconnect else 0,
        error_cb=_quiet,
    )


def pg_dsn(url: str) -> str:
    """SQLAlchemy URL -> plain asyncpg DSN."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


class Relay:
    def __init__(self, settings: Settings, *, dsn: str | None = None, nats_url: str | None = None):
        self.dsn = dsn or pg_dsn(settings.relay_database_url)
        self.nats_url = nats_url or settings.nats_url
        self.poll_s = settings.relay_poll_s
        self.batch = settings.relay_batch
        self.is_leader = False
        self.published = 0  # events acked by JetStream since start (duplicates included)
        self._pg: asyncpg.Connection | None = None
        self._nc = None
        self._js = None
        self._wake = asyncio.Event()

    # -- connections ----------------------------------------------------------------------------
    async def connect(self) -> None:
        self._pg = await asyncpg.connect(self.dsn, timeout=5)
        await self._pg.add_listener("outbox", lambda *_: self._wake.set())
        self._nc = await asyncio.wait_for(_connect(self.nats_url, allow_reconnect=True), timeout=15)
        self._js = self._nc.jetstream()
        await ensure_stream(self._js)

    async def close(self) -> None:
        self.is_leader = False
        metrics.RELAY_LEADER.set(0)
        if self._nc is not None:
            with contextlib.suppress(Exception):
                await self._nc.close()
        if self._pg is not None:
            with contextlib.suppress(Exception):
                await self._pg.close(timeout=2)
        self._pg = self._nc = self._js = None

    async def try_lead(self) -> bool:
        if self._pg is None:
            raise RuntimeError("relay is not connected")
        self.is_leader = bool(await self._pg.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", LOCK_NAME))
        metrics.RELAY_LEADER.set(1 if self.is_leader else 0)
        return self.is_leader

    async def observe_backlog(self) -> tuple[int, float]:
        """Unpublished rows and the age of the oldest, into the gauges (one indexed query; every replica)."""
        if self._pg is None:
            return 0, 0.0
        n, age = await self._pg.fetchrow(
            "SELECT count(*), COALESCE(EXTRACT(EPOCH FROM now() - min(created_at)), 0)::float8 "
            "FROM outbox WHERE published_at IS NULL"
        )
        metrics.RELAY_BACKLOG.set(n)
        metrics.OUTBOX_UNPUBLISHED.set(n)
        metrics.RELAY_LAG.set(age)
        return n, age

    # -- one batch ------------------------------------------------------------------------------
    async def publish_batch(self) -> int:
        """Publish up to `batch` unpublished events in id order; return how many were acked."""
        if self._pg is None or self._js is None:
            raise RuntimeError("relay is not connected")
        failure: Exception | None = None
        done: list[int] = []
        async with self._pg.transaction():
            rows = await self._pg.fetch(
                "SELECT id, event_id, subject, envelope::text AS env FROM outbox "
                "WHERE published_at IS NULL ORDER BY id LIMIT $1 FOR UPDATE SKIP LOCKED",
                self.batch,
            )
            for r in rows:
                try:
                    await self._js.publish(
                        r["subject"],
                        r["env"].encode(),
                        headers={"Nats-Msg-Id": str(r["event_id"])},
                        timeout=5,
                    )
                except Exception as e:  # keep what was acked, retry the rest later
                    failure = e
                    break
                done.append(r["id"])
            if done:
                await self._pg.execute(
                    "UPDATE outbox SET published_at = now() WHERE id = ANY($1::bigint[])", done
                )
        self.published += len(done)
        metrics.RELAY_PUBLISHED.inc(len(done))
        if failure is not None:
            raise failure
        return len(done)

    async def run_once(self) -> int:
        """Connect if needed, take the lead if free, publish everything pending (tests, one-shot use)."""
        if self._pg is None:
            await self.connect()
        if not self.is_leader and not await self.try_lead():
            return 0
        total = 0
        while n := await self.publish_batch():
            total += n
            if n < self.batch:
                break
        return total

    # -- service loop ---------------------------------------------------------------------------
    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        backoff = 0.5
        waiting = False  # a "not ready" warning was logged and nothing worked since
        try:
            while not stop.is_set():
                try:
                    if self._pg is None:
                        await self.connect()
                    if not self.is_leader and not await self.try_lead():
                        await self.observe_backlog()
                        await self._sleep(stop, self.poll_s)
                        continue
                    while not stop.is_set():
                        self._wake.clear()
                        n = await self.publish_batch()
                        await self.observe_backlog()
                        backoff = 0.5
                        if waiting:
                            waiting = False
                            log.info("outbox relay is running")
                        if n >= self.batch:
                            continue
                        await self._sleep(stop, self.poll_s, wake=True)
                except asyncio.CancelledError:
                    raise
                except NOT_READY as e:
                    if not waiting:  # one line, once; the backoff keeps the retries quiet
                        log.warning(
                            "outbox relay waiting for database or NATS (%s: %s); backoff up to %.0fs",
                            type(e).__name__,
                            str(e).splitlines()[0] if str(e) else "",
                            MAX_BACKOFF_S,
                        )
                        waiting = True
                    await self.close()
                    await self._sleep(stop, backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_S)
                except Exception:
                    log.warning("outbox relay error; reconnecting in %.1fs", backoff, exc_info=True)
                    await self.close()
                    await self._sleep(stop, backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_S)
        finally:
            await self.close()

    async def _sleep(self, stop: asyncio.Event, seconds: float, *, wake: bool = False) -> None:
        waiters = [asyncio.ensure_future(stop.wait())]
        if wake:
            waiters.append(asyncio.ensure_future(self._wake.wait()))
        try:
            await asyncio.wait(waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()


def start_in_background(settings: Settings) -> tuple[asyncio.Task, asyncio.Event]:
    """For the API lifespan: run the relay as a task; set the event and await the task to stop it."""
    stop = asyncio.Event()
    relay = Relay(settings)
    return asyncio.create_task(relay.run(stop), name="outbox-relay"), stop
