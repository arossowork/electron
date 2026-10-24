"""Row-level security on `questions` (migration 0006), with the app filter bypassed (raw SQL as the app role)."""

import asyncio
import uuid

import asyncpg
import pytest

from hwplatform.questions import QuestionStore

from conftest import APP_URL, OWNER_URL

pytestmark = pytest.mark.integration

QS = [{"id": "q1", "text": "Voltage?"}]


async def in_tenant(conn, tenant_id):
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))


def test_questions_rls_enabled_forced_with_policy(db_ready):
    async def go():
        conn = await asyncpg.connect(OWNER_URL)
        try:
            row = await conn.fetchrow(
                "SELECT relrowsecurity, relforcerowsecurity, relowner::regrole::text AS owner FROM pg_class "
                "WHERE relname = 'questions'"
            )
            assert row["relrowsecurity"] and row["relforcerowsecurity"] and row["owner"] != "hwflow_app"
            pol = await conn.fetch(
                "SELECT policyname, qual, with_check FROM pg_policies WHERE tablename = 'questions'"
            )
            assert [p["policyname"] for p in pol] == ["tenant_isolation"]
            assert "app_tenant_id()" in pol[0]["qual"] and "app_tenant_id()" in pol[0]["with_check"]
        finally:
            await conn.close()

    asyncio.run(go())


def test_raw_sql_sees_and_changes_only_the_current_tenants_questions(run_env):
    async def go():
        async with run_env("q-a") as a, run_env("q-b") as b:
            ra, rb = await a.new_run(), await b.new_run()
            qa = await QuestionStore(a.db, a.tenant_id).ask(ra.run_id, "agent.parts", QS)
            qb = await QuestionStore(b.db, b.tenant_id).ask(rb.run_id, "agent.parts", QS)
            conn = await asyncpg.connect(APP_URL)
            try:
                assert await conn.fetchval("SELECT count(*) FROM questions") == 0  # no context: nothing
                async with conn.transaction():
                    await in_tenant(conn, a.tenant_id)
                    ids = {str(r["id"]) for r in await conn.fetch("SELECT id FROM questions")}  # no WHERE
                    assert ids == {qa.id}
                    assert (
                        await conn.fetchval("SELECT count(*) FROM questions WHERE id = $1", uuid.UUID(qb.id))
                        == 0
                    )
                    # cannot update the other tenant's row ...
                    tag = await conn.execute(
                        "UPDATE questions SET status = 'cancelled' WHERE id = $1", uuid.UUID(qb.id)
                    )
                    assert tag == "UPDATE 0"
                    # ... nor insert one for it
                    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                        await conn.execute(
                            "INSERT INTO questions (id, tenant_id, run_id, stage, questions) "
                            "VALUES ($1, $2, $3, 's', '[]')",
                            uuid.uuid4(),
                            b.tenant_id,
                            uuid.UUID(rb.run_id),
                        )
                async with conn.transaction():
                    await in_tenant(conn, a.tenant_id)
                    # the app role never deletes (rows go with their run or tenant)
                    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                        await conn.execute("DELETE FROM questions")
                async with conn.transaction():
                    await in_tenant(conn, b.tenant_id)
                    assert {str(r["id"]) for r in await conn.fetch("SELECT id FROM questions")} == {qb.id}
                    assert (
                        await conn.fetchval("SELECT status FROM questions WHERE id = $1", uuid.UUID(qb.id))
                        == "open"
                    )
            finally:
                await conn.close()
            # B's question is intact through the store, and A's store cannot see it
            assert (await QuestionStore(b.db, b.tenant_id).get(qb.id)).status == "open"
            from hwplatform.questions import QuestionNotFound

            with pytest.raises(QuestionNotFound):
                await QuestionStore(a.db, a.tenant_id).get(qb.id)

    asyncio.run(go())


def test_question_status_is_constrained(run_env):
    async def go():
        async with run_env("q-c") as a:
            r = await a.new_run()
            conn = await asyncpg.connect(APP_URL)
            try:
                async with conn.transaction():
                    await in_tenant(conn, a.tenant_id)
                    with pytest.raises(asyncpg.exceptions.CheckViolationError):
                        await conn.execute(
                            "INSERT INTO questions (id, tenant_id, run_id, stage, status, questions) "
                            "VALUES ($1, $2, $3, 's', 'bogus', '[]')",
                            uuid.uuid4(),
                            a.tenant_id,
                            uuid.UUID(r.run_id),
                        )
            finally:
                await conn.close()

    asyncio.run(go())
