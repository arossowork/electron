"""Tenant isolation, both layers.

1. Through the API: a user of tenant A cannot list, get or delete tenant B's projects.
2. In Postgres: with the app-layer filter bypassed (raw SQL, or ORM queries without tenant_id), row-level
   security still returns and changes only the current tenant's rows.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import select

from hwplatform import security

from conftest import APP_URL, JWT_SECRET, OWNER_URL

pytestmark = pytest.mark.integration

TENANT_TABLES = ("tenants", "users", "memberships", "projects", "refresh_tokens")


@pytest.fixture
def two(account):
    a, b = account("iso-a"), account("iso-b")
    return a, b, a.project("alpha"), b.project("bravo")


# --- layer 1: API ------------------------------------------------------------------------------


def test_api_cross_tenant_list_get_delete(client, two):
    a, b, pa, pb = two
    ids_a = {p["id"] for p in client.get("/api/v1/projects", headers=a.h).json()}
    assert pa["id"] in ids_a and pb["id"] not in ids_a
    assert all(p["tenant_id"] == a.tenant_id for p in client.get("/api/v1/projects", headers=a.h).json())
    assert client.get(f"/api/v1/projects/{pb['id']}", headers=a.h).status_code == 404
    assert client.delete(f"/api/v1/projects/{pb['id']}", headers=a.h).status_code == 404
    # b's project survived a's delete attempt
    assert client.get(f"/api/v1/projects/{pb['id']}", headers=b.h).status_code == 200
    # members of b are invisible to a
    emails = {m["email"] for m in client.get("/api/v1/tenant/members", headers=a.h).json()}
    assert b.email not in emails
    # own delete works
    assert client.delete(f"/api/v1/projects/{pa['id']}", headers=a.h).status_code == 204
    assert client.get(f"/api/v1/projects/{pa['id']}", headers=a.h).status_code == 404


def test_api_forged_tenant_claim_needs_membership(client, two):
    """Even a validly signed token naming tenant B is refused when the user has no membership in B."""
    a, b, _, pb = two
    forged = security.issue_access_token(
        JWT_SECRET.encode(),
        user_id=uuid.UUID(a.user_id),
        tenant_id=uuid.UUID(b.tenant_id),
        role="owner",
        ttl_s=60,
        issuer="hwplatform",
    )
    h = {"Authorization": f"Bearer {forged}"}
    assert client.get("/api/v1/projects", headers=h).status_code == 401
    assert client.get(f"/api/v1/projects/{pb['id']}", headers=h).status_code == 401


def test_api_same_slug_in_two_tenants(client, account):
    a, b = account("slug-a"), account("slug-b")
    a.project("Same Name")
    b.project("Same Name")
    r = a.client.post("/api/v1/projects", json={"name": "Same Name"}, headers=a.h)
    assert r.status_code == 409


# --- layer 2: Postgres RLS, app filter bypassed --------------------------------------------------


async def _in_tenant(conn, tenant_id, user_id=None):
    await conn.execute(
        "SELECT set_config('app.tenant_id', $1, true), set_config('app.user_id', $2, true)",
        str(tenant_id),
        str(user_id or ""),
    )


def test_rls_raw_sql_as_app_role(two):
    a, b, pa, pb = two

    async def go():
        conn = await asyncpg.connect(APP_URL)
        try:
            # No tenant context: nothing is visible in any tenant-scoped table.
            for t in TENANT_TABLES:
                assert await conn.fetchval(f"SELECT count(*) FROM {t}") == 0, t
            async with conn.transaction():
                await _in_tenant(conn, a.tenant_id)
                ids = {str(r["id"]) for r in await conn.fetch("SELECT id FROM projects")}  # no WHERE
                assert pa["id"] in ids and pb["id"] not in ids
                assert {str(r["tenant_id"]) for r in await conn.fetch("SELECT tenant_id FROM projects")} == {
                    a.tenant_id
                }
                assert (
                    await conn.fetchval(
                        "SELECT count(*) FROM projects WHERE tenant_id = $1", uuid.UUID(b.tenant_id)
                    )
                    == 0
                )
                assert (
                    await conn.fetchval("SELECT count(*) FROM tenants WHERE id = $1", uuid.UUID(b.tenant_id))
                    == 0
                )
                assert await conn.fetchval("SELECT count(*) FROM users WHERE email = $1", b.email) == 0
                assert (
                    await conn.fetchval(
                        "SELECT count(*) FROM memberships WHERE tenant_id = $1", uuid.UUID(b.tenant_id)
                    )
                    == 0
                )
                assert (
                    await conn.fetchval(
                        "SELECT count(*) FROM refresh_tokens WHERE tenant_id = $1", uuid.UUID(b.tenant_id)
                    )
                    == 0
                )
                # Writes to B's rows silently match nothing.
                assert (
                    await conn.execute(
                        "UPDATE projects SET name = 'pwned' WHERE id = $1", uuid.UUID(pb["id"])
                    )
                    == "UPDATE 0"
                )
                assert (
                    await conn.execute("DELETE FROM projects WHERE id = $1", uuid.UUID(pb["id"]))
                    == "DELETE 0"
                )
            # Inserting a row into B while in A violates the WITH CHECK clause.
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await _in_tenant(conn, a.tenant_id)
                    await conn.execute(
                        "INSERT INTO projects (id, tenant_id, name, slug, created_at) "
                        "VALUES ($1, $2, 'x', 'x', now())",
                        uuid.uuid4(),
                        uuid.UUID(b.tenant_id),
                    )
            # Moving A's row into B is refused too.
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await _in_tenant(conn, a.tenant_id)
                    await conn.execute(
                        "UPDATE projects SET tenant_id = $1 WHERE id = $2",
                        uuid.UUID(b.tenant_id),
                        uuid.UUID(pa["id"]),
                    )
            # The quota is not writable by the app role at all.
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await _in_tenant(conn, a.tenant_id)
                    await conn.execute("UPDATE tenants SET max_concurrent_runs = 99")
            # SET LOCAL does not leak past the transaction.
            assert await conn.fetchval("SELECT count(*) FROM projects") == 0
        finally:
            await conn.close()

    asyncio.run(go())


def test_rls_orm_query_without_tenant_filter(settings, two):
    """The repository layer is bypassed: an ORM SELECT with no tenant_id filter still sees only A."""
    from hwplatform.db import Database, make_engine
    from hwplatform.models import Project, RefreshToken

    a, b, pa, pb = two

    async def go():
        db = Database(make_engine(settings.database_url, 1))
        try:
            async with db.tx(tenant_id=uuid.UUID(a.tenant_id)) as s:
                rows = (await s.execute(select(Project))).scalars().all()
                assert {str(p.id) for p in rows} >= {pa["id"]} and pb["id"] not in {str(p.id) for p in rows}
                assert await s.get(Project, uuid.UUID(pb["id"])) is None
                assert all(
                    str(r.tenant_id) == a.tenant_id for r in (await s.execute(select(RefreshToken))).scalars()
                )
            # A token row for B written while in A's context is refused.
            with pytest.raises(Exception, match="row-level security"):
                async with db.tx(tenant_id=uuid.UUID(a.tenant_id)) as s:
                    s.add(
                        RefreshToken(
                            tenant_id=uuid.UUID(b.tenant_id),
                            user_id=uuid.UUID(b.user_id),
                            token_hash=uuid.uuid4().hex * 2,
                            family_id=uuid.uuid4(),
                            expires_at=datetime.now(UTC) + timedelta(days=1),
                        )
                    )
        finally:
            await db.dispose()

    asyncio.run(go())


def test_rls_applies_to_owner_role_too(two):
    """FORCE ROW LEVEL SECURITY: even the table owner sees nothing without a tenant context."""
    a, *_ = two

    async def go():
        conn = await asyncpg.connect(OWNER_URL)
        try:
            assert await conn.fetchval("SELECT count(*) FROM projects") == 0
            async with conn.transaction():
                await _in_tenant(conn, a.tenant_id)
                assert await conn.fetchval("SELECT count(DISTINCT tenant_id) FROM projects") == 1
        finally:
            await conn.close()

    asyncio.run(go())


def test_rls_is_enabled_and_forced_and_app_role_is_safe(db_ready):
    async def go():
        conn = await asyncpg.connect(OWNER_URL)
        try:
            rows = await conn.fetch(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = ANY($1::text[]) AND relkind = 'r'",
                list(TENANT_TABLES),
            )
            assert {r["relname"] for r in rows} == set(TENANT_TABLES)
            assert all(r["relrowsecurity"] and r["relforcerowsecurity"] for r in rows)
            role = await conn.fetchrow(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'hwflow_app'"
            )
            assert role is not None and not role["rolsuper"] and not role["rolbypassrls"]
            owners = await conn.fetch(
                "SELECT DISTINCT tableowner FROM pg_tables WHERE tablename = ANY($1::text[])",
                list(TENANT_TABLES),
            )
            assert "hwflow_app" not in {r["tableowner"] for r in owners}
        finally:
            await conn.close()

    asyncio.run(go())
