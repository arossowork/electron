"""The relay before the migrations ran: one short warning, exponential backoff to 60 s, recovery by itself."""

import asyncio
import logging
from types import SimpleNamespace

import asyncpg

from hwplatform.events.relay import MAX_BACKOFF_S, Relay


def make() -> Relay:
    s = SimpleNamespace(
        relay_database_url="postgresql://x/y", nats_url="nats://x", relay_poll_s=0.01, relay_batch=10
    )
    return Relay(s)


def test_missing_outbox_table_warns_once_backs_off_and_recovers(caplog):
    r = make()
    sleeps: list[float] = []
    stop = asyncio.Event()
    state = {"calls": 0}

    async def connect():
        state["calls"] += 1
        if state["calls"] <= 9:
            raise asyncpg.UndefinedTableError('relation "outbox" does not exist')
        r._pg = object()
        r._js = object()

    async def try_lead():
        r.is_leader = True
        return True

    async def publish_batch():
        stop.set()  # recovered: stop the loop after one good pass
        return 0

    async def observe_backlog():
        return 0, 0.0

    async def sleep(_stop, seconds, *, wake=False):
        sleeps.append(seconds)

    async def close():
        r._pg = r._js = None

    r.connect, r.try_lead, r.publish_batch, r.observe_backlog, r._sleep, r.close = (
        connect,
        try_lead,
        publish_batch,
        observe_backlog,
        sleep,
        close,
    )
    with caplog.at_level(logging.INFO, logger="hwplatform.relay"):
        asyncio.run(r.run(stop))

    warnings = [x for x in caplog.records if x.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].exc_info is None
    assert "outbox" in warnings[0].getMessage()
    assert len(warnings[0].getMessage()) < 300
    assert sleeps[:4] == [0.5, 1.0, 2.0, 4.0]
    assert max(sleeps) == MAX_BACKOFF_S
    assert any("is running" in x.getMessage() for x in caplog.records)
