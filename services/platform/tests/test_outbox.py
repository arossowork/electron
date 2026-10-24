"""Transactional outbox (ADR 0001, 5.3; issue #11): the event commits with the state change or not at all."""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from hwplatform.contracts import RunStatus
from hwplatform.events import Event
from hwplatform.events.outbox import emit

pytestmark = pytest.mark.integration


class Boom(Exception):
    pass


async def run_state(env, rid):
    async with env.db.tx(tenant_id=env.tenant_id) as s:
        row = (
            await s.execute(
                text("SELECT status::text AS st, event_seq FROM runs WHERE id = :i"), {"i": uuid.UUID(rid)}
            )
        ).one()
    return row.st, row.event_seq


def test_rollback_leaves_no_event_and_no_state_change(run_env):
    async def go():
        async with run_env("ob-rb") as env:
            r = await env.new_run()
            before = await env.events(r.run_id)
            assert len(before) == 1  # run.queued
            # a state change plus its event in one transaction, then the transaction fails
            with pytest.raises(Boom):
                async with env.store._tx() as s:
                    await s.execute(
                        text("UPDATE runs SET status = 'running' WHERE id = :i"), {"i": uuid.UUID(r.run_id)}
                    )
                    await emit(s, env.tenant_id, r.run_id, "run.started", f"runs/{r.run_id}", {})
                    assert len(await env.events(r.run_id)) == 1  # not visible outside before commit
                    raise Boom
            assert await run_state(env, r.run_id) == ("queued", 1)  # state AND event_seq rolled back
            assert len(await env.events(r.run_id)) == 1
            # the same through the store: an event that cannot be written aborts the transition
            with pytest.raises(TypeError):
                await env.store.transition(r.run_id, RunStatus.running, event_data={"bad": object()})
            assert await run_state(env, r.run_id) == ("queued", 1)
            assert len(await env.events(r.run_id)) == 1
            # and then a good transition works with the next seq: no gap was left behind
            await env.store.transition(r.run_id, RunStatus.running)
            assert [e["seq"] for e in await env.events(r.run_id)] == [1, 2]

    asyncio.run(go())


def test_commit_writes_state_and_event_together(run_env):
    async def go():
        async with run_env("ob-ok") as env:
            r = await env.new_run()
            await env.store.transition(r.run_id, RunStatus.running)
            assert await run_state(env, r.run_id) == ("running", 2)
            ev = await env.events(r.run_id)
            assert [e["type"] for e in ev] == ["run.queued", "run.started"]
            assert all(e["published_at"] is None for e in ev)  # the relay publishes later

    asyncio.run(go())


def test_emit_without_run_has_no_seq_and_unknown_run_fails(run_env):
    async def go():
        async with run_env("ob-norun") as env:
            async with env.store._tx() as s:
                ev = await emit(s, env.tenant_id, None, "design_version.created", "projects/x", {"a": 1})
            assert ev.seq is None and ev.runid is None
            rows = [e for e in await env.events() if e["type"] == "design_version.created"]
            assert len(rows) == 1 and rows[0]["seq"] is None
            with pytest.raises(LookupError):
                async with env.store._tx() as s:
                    await emit(s, env.tenant_id, uuid.uuid4(), "run.queued", "runs/x", {})

    asyncio.run(go())


def test_event_catalogue_is_enforced():
    with pytest.raises(ValueError):
        Event(type="run.exploded", tenantid=str(uuid.uuid4()), subject="runs/x")
    ev = Event(
        type="stage.completed",
        tenantid=str(uuid.uuid4()),
        subject="runs/r/stages/check",
        runid=str(uuid.uuid4()),
        seq=3,
        data={"k": 1},
    )
    env = ev.to_envelope()
    assert env["specversion"] == "1.0" and env["datacontenttype"] == "application/json"
    assert env["dataschema"] == "hwflow.events/stage.completed/1" and env["seq"] == 3
    assert env["time"].endswith("Z") and ev.nats_subject == f"hwflow.{ev.tenantid}.stage.completed"
    assert "." not in ev.tenantid  # one NATS subject token


def test_purge_removes_old_published_rows_only(run_env, settings, relay_ready):
    import asyncpg

    from hwplatform.purge import purge_all

    from conftest import OWNER_URL, RELAY_URL, sa_url

    async def go():
        async with run_env("ob-purge") as env:
            r = await env.new_run()
            await env.store.transition(r.run_id, RunStatus.running)  # events 1 (queued) and 2 (started)
            relay = await asyncpg.connect(RELAY_URL)
            try:
                await relay.execute(
                    "UPDATE outbox SET published_at = now() - interval '2 days' WHERE run_id = $1 AND seq = 1",
                    uuid.UUID(r.run_id),
                )
            finally:
                await relay.close()
            result = await purge_all(
                settings.model_copy(update={"outbox_purge_after_s": 24 * 3600}), url=sa_url(OWNER_URL)
            )
            assert result["outbox"] >= 1
            assert [e["seq"] for e in await env.events(r.run_id)] == [2]  # the unpublished event survives

    asyncio.run(go())
