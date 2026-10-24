"""BYOK (#14): endpoints, access control, isolation and rotation against the real database."""

import asyncio
import base64
import logging
import uuid

import asyncpg
import pytest
from sqlalchemy import select

from hwplatform import llm_keys
from hwplatform.crypto import CryptoError, Keyring
from hwplatform.db import Database, make_engine
from hwplatform.models import LlmUsage

from conftest import APP_URL, OWNER_URL, unique_email

pytestmark = pytest.mark.integration

URL = "/api/v1/tenant/llm-keys/anthropic"


def key(tag: str = "ok") -> str:
    return f"sk-ant-api03-{tag}-" + uuid.uuid4().hex + "ABCD"


def with_(settings, **kw):
    from hwplatform.settings import Settings

    return Settings(**{**dict(settings), **kw})


def run(settings, coro_fn):
    async def go():
        db = Database(make_engine(settings.database_url, 1))
        try:
            return await coro_fn(db)
        finally:
            await db.dispose()

    return asyncio.run(go())


def test_put_get_delete_never_returns_the_key(client, account, validator, caplog):
    a = account("byok")
    k = key()
    with caplog.at_level(logging.DEBUG):
        r = client.put(URL, json={"api_key": k}, headers=a.h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "anthropic" and body["last4"] == k[-4:] and body["status"] == "active"
    assert body["rotated_at"] is None and body["last_validated_at"]
    assert validator.seen[-1] == k  # validated before it was stored
    g = client.get(URL, headers=a.h)
    assert g.status_code == 200 and g.json()["last4"] == k[-4:]
    assert set(g.json()) == {
        "provider", "last4", "status", "created_at", "rotated_at", "last_validated_at", "credential_type",
    }  # fmt: skip
    assert g.json()["credential_type"] == "api_key"
    for resp in (r, g):
        assert k not in resp.text
    assert k not in caplog.text
    # replace: one active key per tenant and provider, rotated_at set
    k2 = key("second")
    r2 = client.put(URL, json={"api_key": k2}, headers=a.h)
    assert r2.status_code == 200 and r2.json()["rotated_at"] and r2.json()["last4"] == k2[-4:]
    assert client.get(URL, headers=a.h).json()["last4"] == k2[-4:]
    assert client.delete(URL, headers=a.h).status_code == 204
    assert client.get(URL, headers=a.h).status_code == 404
    assert client.delete(URL, headers=a.h).status_code == 404


def test_database_holds_no_plaintext(client, account):
    a = account("byok-db")
    k = key()
    assert client.put(URL, json={"api_key": k}, headers=a.h).status_code == 200

    async def go():
        conn = await asyncpg.connect(APP_URL)
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", a.tenant_id)
                row = await conn.fetchrow("SELECT * FROM tenant_llm_keys")
            blob = b"".join(v for v in row.values() if isinstance(v, bytes | bytearray))
            assert k.encode() not in blob and k[:12].encode() not in blob
            assert row["key_version"] == 1 and row["last4"] == k[-4:]
        finally:
            await conn.close()

    asyncio.run(go())


def test_invalid_and_unreachable_keys_are_not_stored(client, account):
    a = account("byok-bad")
    assert client.put(URL, json={"api_key": key("rejected")}, headers=a.h).status_code == 422
    assert client.put(URL, json={"api_key": key("down")}, headers=a.h).status_code == 502
    r = client.put(URL, json={"api_key": "not-a-key-at-all"}, headers=a.h)
    assert r.status_code == 422 and "not-a-key-at-all" not in r.text
    other = client.put("/api/v1/tenant/llm-keys/openai", json={"api_key": key()}, headers=a.h)
    assert other.status_code == 404
    assert client.get(URL, headers=a.h).status_code == 404


def test_members_get_403_and_anonymous_401(client, account):
    owner = account("byok-owner")
    member = account("byok-member")
    mh = member.join(owner)
    assert client.get("/api/v1/auth/me", headers=mh).json()["role"] == "member"
    assert client.put(URL, json={"api_key": key()}, headers=owner.h).status_code == 200
    assert client.get(URL, headers=mh).status_code == 403
    assert client.delete(URL, headers=mh).status_code == 403
    assert client.put(URL, json={"api_key": key()}, headers=mh).status_code == 403
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers=owner.h).status_code == 200  # unchanged by the member's attempts


def test_cross_tenant_isolation_api(client, account):
    a, b = account("byok-a"), account("byok-b")
    client.put(URL, json={"api_key": key("a")}, headers=a.h)
    assert client.get(URL, headers=b.h).status_code == 404
    assert client.delete(URL, headers=b.h).status_code == 404
    assert client.get(URL, headers=a.h).status_code == 200


def test_rls_direct_sql_on_key_and_usage_tables(client, account, settings):
    a, b = account("rls-a"), account("rls-b")
    for acc in (a, b):
        assert client.put(URL, json={"api_key": key()}, headers=acc.h).status_code == 200

    async def seed(db):
        for acc in (a, b):
            await llm_keys.record_usage(
                db, uuid.UUID(acc.tenant_id), "claude-x", {"input_tokens": 1, "output_tokens": 1}
            )

    run(settings, seed)

    async def go():
        conn = await asyncpg.connect(APP_URL)
        try:
            for t in ("tenant_llm_keys", "llm_usage"):
                assert await conn.fetchval(f"SELECT count(*) FROM {t}") == 0  # no context, no rows
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", a.tenant_id)
                for t in ("tenant_llm_keys", "llm_usage"):
                    rows = await conn.fetch(f"SELECT tenant_id FROM {t}")  # no WHERE
                    assert rows and {str(r["tenant_id"]) for r in rows} == {a.tenant_id}, t
                assert await conn.execute("UPDATE tenant_llm_keys SET last4 = 'xxxx'") == "UPDATE 1"
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", a.tenant_id)
                    await conn.execute(
                        "INSERT INTO llm_usage (id, tenant_id, model) VALUES ($1, $2, 'm')",
                        uuid.uuid4(),
                        uuid.UUID(b.tenant_id),
                    )
            # usage rows are append-only for the app role
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", a.tenant_id)
                    await conn.execute("DELETE FROM llm_usage")
        finally:
            await conn.close()
        # The owner role sees usage only with a context, and keys only through the rotation policy.
        conn = await asyncpg.connect(OWNER_URL)
        try:
            assert await conn.fetchval("SELECT count(*) FROM llm_usage") == 0
            assert await conn.execute("DELETE FROM tenant_llm_keys") == "DELETE 0"  # no delete policy
            assert await conn.fetchval("SELECT count(*) FROM tenant_llm_keys") >= 2  # rotation policy
        finally:
            await conn.close()

    asyncio.run(go())


def test_get_tenant_llm_key_and_wrong_tenant_row(client, account, settings):
    a, b = account("svc-a"), account("svc-b")
    ka = key("svc")
    client.put(URL, json={"api_key": ka}, headers=a.h)
    ring = Keyring.from_settings(settings)

    async def go(db):
        ta, tb = uuid.UUID(a.tenant_id), uuid.UUID(b.tenant_id)
        assert await llm_keys.get_tenant_llm_key(db, ring, ta) == ka
        assert await llm_keys.get_tenant_llm_key(db, ring, tb) is None  # B has no key; never A's
        async with db.tx(tenant_id=ta) as s:
            row = await llm_keys._row(s, ta, "anthropic")
            sealed = llm_keys.Sealed(row.ciphertext, row.nonce, row.wrapped_dek, row.key_version)
        with pytest.raises(CryptoError):  # A's sealed row placed under B does not decrypt for B
            ring.decrypt(sealed, tb, "anthropic")
        await llm_keys.mark_invalid(db, ta)
        assert await llm_keys.get_tenant_llm_key(db, ring, ta) is None

    run(settings, go)
    assert client.get(URL, headers=a.h).json()["status"] == "invalid"


def test_record_usage(client, account, settings):
    a = account("usage")
    run_id = uuid.uuid4()

    class Usage:  # SDK-style object
        input_tokens = 10
        output_tokens = 5
        cache_creation_input_tokens = None
        cache_read_input_tokens = 7

    async def go(db):
        t = uuid.UUID(a.tenant_id)
        await llm_keys.record_usage(db, t, "claude-sonnet-x", Usage(), run_id=run_id)
        await llm_keys.record_usage(db, t, "claude-sonnet-x", {"input_tokens": 3, "output_tokens": 2})
        async with db.tx(tenant_id=t) as s:
            return list((await s.execute(select(LlmUsage).order_by(LlmUsage.at))).scalars())

    rows = run(settings, go)
    got = [
        (r.input_tokens, r.output_tokens, r.cache_creation_input_tokens, r.cache_read_input_tokens)
        for r in rows
    ]
    assert got == [(10, 5, 0, 7), (3, 2, 0, 0)]
    assert rows[0].run_id == run_id and rows[1].run_id is None


def test_rotate_master_command(client, account, settings):
    from hwplatform.rotation import rotate_all

    a = account("rot")
    ka = key("rot")
    assert client.put(URL, json={"api_key": ka}, headers=a.h).status_code == 200
    new = base64.b64encode(b"N" * 32).decode()
    s2 = with_(settings, master_keys=f"2:{new}")  # keyring: v1 (MASTER_KEY) + v2
    n, newest = asyncio.run(rotate_all(s2, url=settings.migration_database_url))
    assert newest == 2 and n >= 1
    again = asyncio.run(rotate_all(s2, url=settings.migration_database_url))
    assert again == (0, 2)  # idempotent
    # The tenant's key still decrypts with the new master key alone.
    only_new = with_(settings, master_key="", master_keys=f"2:{new}")
    ring = Keyring.from_settings(only_new)

    async def go(db):
        return await llm_keys.get_tenant_llm_key(db, ring, uuid.UUID(a.tenant_id))

    assert run(settings, go) == ka


def test_unconfigured_master_key_gives_503(settings, validator):
    from fastapi.testclient import TestClient

    from hwplatform.api.app import create_app

    s = with_(settings, master_key="")
    with TestClient(create_app(s, key_validator=validator)) as c:
        em, pw = unique_email("nomk"), "a-long-password-123"
        c.post("/api/v1/auth/signup", json={"email": em, "password": pw, "tenant_name": "T"})
        r = c.post("/api/v1/auth/login", json={"email": em, "password": pw})
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        assert c.put(URL, json={"api_key": key()}, headers=h).status_code == 503
