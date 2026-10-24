"""Row-level security on the run and outbox tables (0004, 0005), with the app filter bypassed (raw SQL).

Same approach as test_isolation.py: connect as the app role with no WHERE tenant_id, and prove that RLS alone
shows and changes only the current tenant's rows. Also the relay role (reads all outbox rows, marks them
published, nothing else) and the owner purge policy (published rows only).
"""

import asyncio
import uuid

import asyncpg
import pytest

from hwplatform.contracts import ArtifactRef, RunStatus, StageRecord, StageStatus

from conftest import APP_URL, OWNER_URL, RELAY_URL

pytestmark = pytest.mark.integration

NEW_TABLES = (
    "design_versions",
    "artifacts",
    "runs",
    "run_stages",
    "run_artifacts",
    "stage_cache",
    "project_state",
    "outbox",
    "consumed_events",
)


async def seed(env):
    """One run with a stage, artifacts, project state, a stage_cache row and a consumed event (owner-less)."""
    r = await env.new_run()
    await env.store.transition(r.run_id, RunStatus.running)
    ref = ArtifactRef("ab" * 32, 3, "text/plain")
    await env.store.upsert_stage(
        r.run_id, StageRecord("check", "check", 0, StageStatus.succeeded, verdict="PASS", result=ref)
    )
    await env.store.set_artifacts(r.run_id, {"REPORT.md": ref})
    await env.store.set_project_state(str(env.project_id), {"board": ref}, r.run_id)
    from sqlalchemy import text

    async with env.db.tx(tenant_id=env.tenant_id) as s:
        await s.execute(
            text(
                "INSERT INTO stage_cache (tenant_id, stage, cache_key, result) VALUES (:t, 'check', :k, '{}')"
            ),
            {"t": env.tenant_id, "k": "k" * 64},
        )
        await s.execute(
            text("INSERT INTO consumed_events (consumer, event_id, tenant_id) VALUES ('notifier', :e, :t)"),
            {"t": env.tenant_id, "e": uuid.uuid4()},
        )
    return r


async def in_tenant(conn, tenant_id):
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))


def test_rls_enabled_forced_with_policy_on_every_new_table(db_ready):
    async def go():
        conn = await asyncpg.connect(OWNER_URL)
        try:
            rows = await conn.fetch(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = ANY($1::text[]) AND relkind = 'r'",
                list(NEW_TABLES),
            )
            assert {r["relname"] for r in rows} == set(NEW_TABLES)
            assert all(r["relrowsecurity"] and r["relforcerowsecurity"] for r in rows)
            pol = await conn.fetch("SELECT tablename FROM pg_policies WHERE policyname = 'tenant_isolation'")
            assert set(NEW_TABLES) <= {r["tablename"] for r in pol}
            owners = await conn.fetch(
                "SELECT DISTINCT tableowner FROM pg_tables WHERE tablename = ANY($1::text[])",
                list(NEW_TABLES),
            )
            assert not {"hwflow_app", "hwflow_relay"} & {r["tableowner"] for r in owners}
            role = await conn.fetchrow(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'hwflow_relay'"
            )
            assert role is not None and not role["rolsuper"] and not role["rolbypassrls"]
        finally:
            await conn.close()

    asyncio.run(go())


def test_raw_sql_sees_only_current_tenant(run_env):
    async def go():
        async with run_env("rls-a") as a, run_env("rls-b") as b:
            ra, rb = await seed(a), await seed(b)
            conn = await asyncpg.connect(APP_URL)
            try:
                for t in NEW_TABLES:  # no context: nothing visible
                    assert await conn.fetchval(f"SELECT count(*) FROM {t}") == 0, t
                async with conn.transaction():
                    await in_tenant(conn, a.tenant_id)
                    for t in NEW_TABLES:  # no WHERE: still only A's rows, and A has some
                        tenants = {
                            r["tenant_id"] for r in await conn.fetch(f"SELECT DISTINCT tenant_id FROM {t}")
                        }
                        assert tenants == {a.tenant_id}, t
                    ids = {str(r["id"]) for r in await conn.fetch("SELECT id FROM runs")}
                    assert ids == {ra.run_id} and rb.run_id not in ids
                    assert (
                        await conn.fetchval(
                            "SELECT count(*) FROM run_stages WHERE run_id = $1", uuid.UUID(rb.run_id)
                        )
                        == 0
                    )
                    assert (
                        await conn.fetchval(
                            "SELECT count(*) FROM outbox WHERE run_id = $1", uuid.UUID(rb.run_id)
                        )
                        == 0
                    )
            finally:
                await conn.close()

    asyncio.run(go())


def test_cannot_write_into_or_change_another_tenant(run_env):
    async def go():
        async with run_env("rlsw-a") as a, run_env("rlsw-b") as b:
            await seed(a)
            rb = await seed(b)
            conn = await asyncpg.connect(APP_URL)
            try:
                async with conn.transaction():
                    await in_tenant(conn, a.tenant_id)
                    # UPDATE / DELETE of B's rows touch nothing
                    assert (
                        await conn.execute(
                            "UPDATE runs SET status = 'cancelled' WHERE id = $1", uuid.UUID(rb.run_id)
                        )
                    ) == "UPDATE 0"
                    assert (
                        await conn.execute(
                            "UPDATE run_stages SET status = 'failed' WHERE run_id = $1", uuid.UUID(rb.run_id)
                        )
                    ) == "UPDATE 0"
                for sql, args in (
                    (
                        "INSERT INTO artifacts (tenant_id, sha256, size, media_type) VALUES ($1, $2, 1, 'x')",
                        (b.tenant_id, "c" * 64),
                    ),
                    (
                        "INSERT INTO outbox (event_id, tenant_id, type, subject, envelope) VALUES ($1, $2, 'run.queued', 's', '{}')",
                        (uuid.uuid4(), b.tenant_id),
                    ),
                    (
                        "INSERT INTO stage_cache (tenant_id, stage, cache_key, result) VALUES ($1, 's', $2, '{}')",
                        (b.tenant_id, "d" * 64),
                    ),
                    (
                        "INSERT INTO consumed_events (consumer, event_id, tenant_id) VALUES ('c', $1, $2)",
                        (uuid.uuid4(), b.tenant_id),
                    ),
                ):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        async with conn.transaction():
                            await in_tenant(conn, a.tenant_id)
                            await conn.execute(sql, *args)
                # B's run is unchanged
                assert (await b.store.get(rb.run_id)).status == RunStatus.running
            finally:
                await conn.close()

    asyncio.run(go())


def test_app_role_cannot_delete_events_or_rewrite_published_at_of_runs_it_does_not_own(run_env):
    """The app inserts and reads outbox rows; it never updates or deletes them (the relay marks, purge deletes)."""

    async def go():
        async with run_env("rls-ob") as a:
            await seed(a)
            conn = await asyncpg.connect(APP_URL)
            try:
                for sql in (
                    "DELETE FROM outbox",
                    "UPDATE outbox SET published_at = now()",
                    "DELETE FROM runs",
                ):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        async with conn.transaction():
                            await in_tenant(conn, a.tenant_id)
                            await conn.execute(sql)
            finally:
                await conn.close()

    asyncio.run(go())


def test_relay_role_reads_all_outbox_rows_and_may_only_mark_published(run_env, relay_ready):
    async def go():
        async with run_env("rl-a") as a, run_env("rl-b") as b:
            await seed(a)
            await seed(b)
            conn = await asyncpg.connect(RELAY_URL)
            try:
                tenants = {r["tenant_id"] for r in await conn.fetch("SELECT DISTINCT tenant_id FROM outbox")}
                assert {a.tenant_id, b.tenant_id} <= tenants  # no tenant context needed, every tenant visible
                # it reads nothing else
                for t in ("runs", "projects", "users", "tenant_llm_keys", "consumed_events"):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await conn.fetch(f"SELECT 1 FROM {t} LIMIT 1")
                # marks published, but cannot touch the payload, insert or delete
                assert (
                    await conn.execute(
                        "UPDATE outbox SET published_at = now() WHERE tenant_id = $1", a.tenant_id
                    )
                ).startswith("UPDATE ")
                for sql in (
                    "UPDATE outbox SET envelope = '{}' WHERE tenant_id = $1",
                    "UPDATE outbox SET type = 'x' WHERE tenant_id = $1",
                    "DELETE FROM outbox WHERE tenant_id = $1",
                ):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await conn.execute(sql, a.tenant_id)
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.execute(
                        "INSERT INTO outbox (event_id, tenant_id, type, subject, envelope) VALUES ($1, $2, 'run.queued', 's', '{}')",
                        uuid.uuid4(),
                        a.tenant_id,
                    )
            finally:
                await conn.close()

    asyncio.run(go())


def test_owner_purge_policy_deletes_published_rows_only(run_env, relay_ready):
    async def go():
        async with run_env("purge") as a:
            await seed(a)
            owner = await asyncpg.connect(OWNER_URL)
            relay = await asyncpg.connect(RELAY_URL)
            try:
                await relay.execute(
                    "UPDATE outbox SET published_at = now() - interval '40 days' WHERE tenant_id = $1 AND seq <= 2",
                    a.tenant_id,
                )
                pending = await relay.fetchval(
                    "SELECT count(*) FROM outbox WHERE tenant_id = $1 AND published_at IS NULL", a.tenant_id
                )
                assert pending > 0
                deleted = await owner.execute("DELETE FROM outbox WHERE tenant_id = $1", a.tenant_id)
                assert deleted == "DELETE 2"  # the published ones only
                assert (
                    await relay.fetchval(
                        "SELECT count(*) FROM outbox WHERE tenant_id = $1 AND published_at IS NULL",
                        a.tenant_id,
                    )
                    == pending
                )
            finally:
                await owner.close()
                await relay.close()

    asyncio.run(go())
