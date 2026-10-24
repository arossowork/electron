"""Outbox relay -> NATS JetStream (ADR 0001, 5.4; issue #11): exactly-once effect from at-least-once delivery."""

import asyncio
import json
import uuid

import asyncpg
import nats
import pytest

from hwplatform.contracts import RunStatus
from hwplatform.events import STREAM
from hwplatform.events.relay import Relay, ensure_stream, stream_config

from conftest import NATS_URL, RELAY_URL

pytestmark = pytest.mark.integration


def make_relay(settings, **kw) -> Relay:
    return Relay(settings, dsn=RELAY_URL, nats_url=NATS_URL, **kw)


@pytest.fixture
def relay_settings(settings):
    return settings.model_copy(update={"relay_poll_s": 0.2, "relay_batch": 100})


async def stream_counts(tenant_id) -> dict[str, int]:
    """JetStream message count per subject of one tenant (design_version.created left out: it has no run)."""
    nc = await nats.connect(NATS_URL, connect_timeout=3)
    try:
        info = await nc.jetstream().stream_info(STREAM, subjects_filter=f"hwflow.{tenant_id}.>")
        return {
            k: v for k, v in (info.state.subjects or {}).items() if not k.endswith(".design_version.created")
        }
    finally:
        await nc.close()


async def fetch_messages(tenant_id, n: int):
    nc = await nats.connect(NATS_URL, connect_timeout=3)
    try:
        js = nc.jetstream()
        sub = await js.pull_subscribe(f"hwflow.{tenant_id}.>", stream=STREAM)
        msgs = await sub.fetch(n + 1, timeout=5)  # +1: the design_version.created event of the run's version
        for m in msgs:
            await m.ack()
        return [m for m in msgs if not m.subject.endswith(".design_version.created")]
    finally:
        await nc.close()


async def lifecycle(env, n_stages: int = 0):
    r = await env.new_run()
    await env.store.transition(r.run_id, RunStatus.running)
    for i in range(n_stages):
        await env.store.append_event(r.run_id, "stage.progress", {"key": "layout_attempt.1", "line": f"l{i}"})
    await env.store.transition(r.run_id, RunStatus.succeeded, verdict="PASS")
    return r.run_id


async def unpublished(env) -> int:
    return sum(1 for e in await env.events() if e["published_at"] is None)


def test_stream_config_matches_adr_and_ensure_is_idempotent(relay_ready):
    async def go():
        nc = await nats.connect(NATS_URL, connect_timeout=3)
        try:
            js = nc.jetstream()
            await ensure_stream(js)
            await ensure_stream(js)
            cfg = (await js.stream_info(STREAM)).config
            assert cfg.subjects == ["hwflow.>"]
            assert (cfg.storage, cfg.retention, cfg.discard) == ("file", "limits", "old")
            assert cfg.max_age == 7 * 24 * 3600 and cfg.max_bytes == 512 * 1024 * 1024
            assert cfg.duplicate_window == 2 * 3600 and cfg.num_replicas == 1
            assert stream_config().name == STREAM
        finally:
            await nc.close()

    asyncio.run(go())


def test_delivers_each_event_once_with_msg_id_and_subject(run_env, relay_settings, relay_ready):
    async def go():
        async with run_env("relay-once") as env:
            rid = await lifecycle(env, n_stages=2)
            rows = await env.events(rid)
            assert len(rows) == 5
            relay = make_relay(relay_settings)
            try:
                await relay.run_once()
                assert await unpublished(env) == 0
                again = await relay.run_once()  # nothing left: no second delivery
                assert again == 0
            finally:
                await relay.close()
            counts = await stream_counts(env.tenant_id)
            assert counts == {
                f"hwflow.{env.tenant_id}.run.queued": 1,
                f"hwflow.{env.tenant_id}.run.started": 1,
                f"hwflow.{env.tenant_id}.stage.progress": 2,
                f"hwflow.{env.tenant_id}.run.completed": 1,
            }
            msgs = await fetch_messages(env.tenant_id, 5)
            by_id = {m.headers["Nats-Msg-Id"]: json.loads(m.data) for m in msgs}
            assert set(by_id) == {str(r["event_id"]) for r in rows}
            for env_ in by_id.values():
                assert env_["tenantid"] == str(env.tenant_id) and env_["runid"] == rid
            assert sorted(e["seq"] for e in by_id.values()) == [1, 2, 3, 4, 5]

    asyncio.run(go())


def test_duplicate_publish_is_dropped_by_msg_id(run_env, relay_settings, relay_ready):
    """A crash between the JetStream ack and the UPDATE republishes; the stream dedups on Nats-Msg-Id."""

    async def go():
        async with run_env("relay-dup") as env:
            rid = await lifecycle(env)
            relay = make_relay(relay_settings)
            try:
                await relay.run_once()
                before = await stream_counts(env.tenant_id)
                assert sum(before.values()) == 3
                # simulate the crash: the rows are published but the UPDATE never happened
                conn = await asyncpg.connect(RELAY_URL)
                try:
                    await conn.execute(
                        "UPDATE outbox SET published_at = NULL WHERE run_id = $1", uuid.UUID(rid)
                    )
                finally:
                    await conn.close()
                assert await unpublished(env) == 3
                await relay.run_once()  # publishes all three again
                assert relay.published >= 6
                assert await unpublished(env) == 0
            finally:
                await relay.close()
            assert await stream_counts(env.tenant_id) == before  # no duplicates in the stream

    asyncio.run(go())


def test_restart_loses_nothing_and_duplicates_nothing(run_env, relay_settings, relay_ready):
    async def go():
        async with run_env("relay-restart") as env:
            r1 = await lifecycle(env)
            first = make_relay(relay_settings)
            try:
                await first.run_once()
            finally:
                await first.close()  # "the process dies"
            r2 = await lifecycle(env, n_stages=3)  # events written while no relay was running
            r3 = await lifecycle(env)
            assert (
                await unpublished(env) == 11
            )  # r2 and r3 (6 + 3 events + 2 design versions) wait in the outbox
            second = make_relay(relay_settings)
            try:
                await second.run_once()
            finally:
                await second.close()
            assert await unpublished(env) == 0
            counts = await stream_counts(env.tenant_id)
            assert sum(counts.values()) == len([e for e in await env.events() if e["run_id"]]) == 3 + 6 + 3
            assert counts[f"hwflow.{env.tenant_id}.run.completed"] == 3
            assert {r1, r2, r3}  # three runs, all delivered

    asyncio.run(go())


def test_publish_failure_keeps_acked_rows_and_retries_the_rest(run_env, relay_settings, relay_ready):
    async def go():
        async with run_env("relay-fail") as env:
            await lifecycle(env)
            relay = make_relay(relay_settings)
            try:
                await relay.connect()
                await relay.try_lead()
                real = relay._js.publish
                calls = {"n": 0}

                async def flaky(subject, payload, **kw):
                    calls["n"] += 1
                    if calls["n"] == 3:
                        raise TimeoutError("nats went away")
                    return await real(subject, payload, **kw)

                relay._js.publish = flaky
                with pytest.raises(TimeoutError):
                    await relay.publish_batch()
                assert (
                    await unpublished(env) == 2
                )  # two acked rows were marked; the failed one and the rest stay
                relay._js.publish = real
                await relay.publish_batch()
                assert await unpublished(env) == 0
            finally:
                await relay.close()
            assert sum((await stream_counts(env.tenant_id)).values()) == 3

    asyncio.run(go())


def test_only_one_relay_leads(run_env, relay_settings, relay_ready):
    async def go():
        async with run_env("relay-lead") as env:
            await lifecycle(env)
            a, b = make_relay(relay_settings), make_relay(relay_settings)
            try:
                await a.run_once()
                assert a.is_leader
                assert await b.run_once() == 0 and not b.is_leader  # b did not publish
                assert await unpublished(env) == 0
                await a.close()  # leader dies: the session lock goes with its connection
                await lifecycle(env)
                assert await b.run_once() == 4 and b.is_leader  # 3 run events + design_version.created
            finally:
                await a.close()
                await b.close()
            assert sum((await stream_counts(env.tenant_id)).values()) == 6

    asyncio.run(go())


def test_service_loop_wakes_on_notify(run_env, settings, relay_ready):
    """With a 30 s poll interval an event is still delivered within seconds: LISTEN/NOTIFY wakes the loop."""

    async def go():
        async with run_env("relay-loop") as env:
            relay = make_relay(settings.model_copy(update={"relay_poll_s": 30.0}))
            stop = asyncio.Event()
            task = asyncio.create_task(relay.run(stop))
            try:
                for _ in range(100):  # wait until it leads and idles
                    if relay.is_leader:
                        break
                    await asyncio.sleep(0.1)
                assert relay.is_leader
                await asyncio.sleep(0.3)
                await lifecycle(env)
                for _ in range(100):
                    if await unpublished(env) == 0:
                        break
                    await asyncio.sleep(0.1)
                assert await unpublished(env) == 0
            finally:
                stop.set()
                await asyncio.wait_for(task, timeout=10)
            assert sum((await stream_counts(env.tenant_id)).values()) == 3

    asyncio.run(go())


def test_relay_runs_inside_the_api_process(settings, account, relay_ready):
    """create_app starts the relay from its lifespan hook and stops it on shutdown (ADR 5.4)."""
    from fastapi.testclient import TestClient

    from hwplatform.api.app import create_app

    s = settings.model_copy(
        update={
            "relay_enabled": True,
            "relay_database_url": RELAY_URL.replace("postgresql://", "postgresql+asyncpg://"),
            "nats_url": NATS_URL,
            "relay_poll_s": 0.2,
        }
    )

    async def seed_and_wait(a):
        from hwplatform.db import Database
        from hwplatform.runs import PgRunStore

        db = Database.from_settings(settings)
        try:
            store = PgRunStore(db, a.tenant_id)
            pid = uuid.UUID(a.project("p")["id"])
            from conftest import RunEnv

            env = RunEnv(db, store, uuid.UUID(a.tenant_id), pid)
            await lifecycle(env)
            for _ in range(100):
                if await unpublished(env) == 0:
                    return env
                await asyncio.sleep(0.1)
            return env
        finally:
            await db.dispose()

    a = account("relay-api")
    with TestClient(create_app(s)) as c:
        assert c.get("/healthz").json() == {"ok": True}
        env = asyncio.run(seed_and_wait(a))
    assert asyncio.run(stream_counts(env.tenant_id))[f"hwflow.{env.tenant_id}.run.completed"] == 1
