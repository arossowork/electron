"""Auth hardening (#26): rate limits and lockout, no account enumeration, e-mail verification, purge."""

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from hwplatform import purge, security
from hwplatform.mail import Email, LoggingEmailSender

from conftest import APP_URL, OWNER_URL, unique_email

pytestmark = pytest.mark.integration

PW = "a-long-password-123"
# Everything but the one limit under test stays out of the way.
LOOSE = dict(
    rate_login_email_max=10_000,
    rate_login_ip_max=10_000,
    rate_refresh_ip_max=10_000,
    rate_signup_email_max=10_000,
    rate_signup_ip_max=10_000,
    lockout_threshold=10_000,
    lockout_ip_threshold=10_000,
)


def loose(**kw):
    return {"dev_auto_verify": True, **LOOSE, **kw}


def signup(c, email=None, pw=PW):
    email = email or unique_email("h")
    r = c.post("/api/v1/auth/signup", json={"email": email, "password": pw, "tenant_name": "T"})
    return email, r


def login(c, email, pw=PW):
    return c.post("/api/v1/auth/login", json={"email": email, "password": pw})


def sql(url, query, *args, tenant=None):
    async def go():
        conn = await asyncpg.connect(url)
        try:
            async with conn.transaction():
                if tenant:
                    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant))
                if query.lstrip().upper().startswith("SELECT"):
                    return await conn.fetch(query, *args)
                return await conn.execute(query, *args)
        finally:
            await conn.close()

    return asyncio.run(go())


# --- rate limiting and lockout -----------------------------------------------------------------------


def test_nth_plus_one_login_in_the_window_is_rejected(make_client):
    c = make_client(**loose(rate_login_email_max=3))
    email, _ = signup(c)
    for _ in range(3):
        assert login(c, email).status_code == 200
    r = login(c, email)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert login(c, email, "wrong-password-xyz").status_code == 429  # rejected before the password check
    other, _ = signup(c)  # another e-mail from the same IP is not affected by the e-mail limit
    assert login(c, other).status_code == 200


def test_login_window_slides(make_client):
    c = make_client(**loose(rate_login_email_max=2))
    email, _ = signup(c)
    assert login(c, email).status_code == 200 and login(c, email).status_code == 200
    assert login(c, email).status_code == 429
    sql(OWNER_URL, "UPDATE auth_events SET at = at - interval '2 minutes' WHERE action = 'login'")
    assert login(c, email).status_code == 200  # the old attempts left the 60 s window


def test_ip_limit_counts_across_emails(make_client):
    c = make_client(**loose(rate_login_ip_max=3))
    emails = [signup(c)[0] for _ in range(4)]
    assert [login(c, e).status_code for e in emails] == [200, 200, 200, 429]
    c2 = make_client(**loose(rate_login_ip_max=3))  # another IP is independent
    assert login(c2, emails[0]).status_code == 200


def test_lockout_after_n_failures_and_it_expires(make_client):
    c = make_client(**loose(lockout_threshold=3))
    email, _ = signup(c)
    for _ in range(3):
        assert login(c, email, "wrong-password-xyz").status_code == 401
    r = login(c, email)  # the right password is refused while locked
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert login(c, email).status_code == 429  # locked requests do not extend the lock
    # an unknown address behaves exactly the same way
    ghost = unique_email("ghost")
    codes = [login(c, ghost, "whatever-password").status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    # the lock ends when the failures age out of the 15 minute window
    sql(OWNER_URL, "UPDATE auth_events SET at = at - interval '1 hour' WHERE kind = 'fail'")
    assert login(c, email).status_code == 200


def test_success_clears_failures(make_client):
    c = make_client(**loose(lockout_threshold=3))
    email, _ = signup(c)
    for _ in range(2):
        login(c, email, "wrong-password-xyz")
    assert login(c, email).status_code == 200
    for _ in range(2):
        assert login(c, email, "wrong-password-xyz").status_code == 401  # still below 3 after the reset
    assert login(c, email).status_code == 200


def test_ip_lockout(make_client):
    c = make_client(**loose(lockout_ip_threshold=3))
    codes = [login(c, unique_email("brute"), "whatever-password").status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]


def test_signup_and_refresh_are_limited(make_client):
    c = make_client(**loose(rate_signup_email_max=2))
    email = unique_email("rs")
    assert [signup(c, email)[1].status_code for _ in range(3)] == [202, 202, 429]
    c = make_client(**loose(rate_signup_ip_max=2))
    assert [signup(c)[1].status_code for _ in range(3)] == [202, 202, 429]
    c = make_client(**loose(rate_refresh_ip_max=2))
    codes = [c.post("/api/v1/auth/refresh", json={"refresh_token": "rt.x.y"}).status_code for _ in range(3)]
    assert codes == [401, 401, 429]


def test_limit_tables_are_global_and_append_only_for_the_app(db_ready):
    rows = sql(
        OWNER_URL,
        "SELECT c.relname, c.relrowsecurity, "
        "EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id') "
        "AS has_tenant "
        "FROM pg_class c WHERE c.relname IN ('auth_events', 'pending_signups') AND c.relkind = 'r'",
    )
    assert len(rows) == 2 and not any(r["relrowsecurity"] or r["has_tenant"] for r in rows)

    async def go():
        conn = await asyncpg.connect(APP_URL)
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("UPDATE auth_events SET kind = 'attempt'")
        finally:
            await conn.close()

    asyncio.run(go())


# --- no account enumeration --------------------------------------------------------------------------


def test_signup_responses_are_identical_for_new_and_existing_emails(make_client, monkeypatch):
    c = make_client(**loose(dev_auto_verify=False))
    box = c.mailbox
    calls = []
    real = security.hash_password
    monkeypatch.setattr(security, "hash_password", lambda p: calls.append(1) or real(p))

    existing = unique_email("exists")
    _, r0 = signup(c, existing)
    token = box.last(existing, "verify").token
    assert c.post("/api/v1/auth/verify", json={"token": token}).status_code == 200
    assert login(c, existing).status_code == 200  # the account exists now
    calls.clear()

    new = unique_email("new")
    n_hits = []
    timings = {"new": [], "existing": []}
    for _ in range(3):
        t = time.perf_counter()
        _, rn = signup(c, unique_email("new"))
        timings["new"].append(time.perf_counter() - t)
        t = time.perf_counter()
        _, re_ = signup(c, existing)
        timings["existing"].append(time.perf_counter() - t)
        n_hits.append(len(calls))
    _, rn = signup(c, new)
    _, re_ = signup(c, existing.upper())
    assert rn.status_code == re_.status_code == r0.status_code == 202
    assert rn.json() == re_.json() == r0.json()
    assert rn.headers["content-type"] == re_.headers["content-type"]
    assert rn.headers["content-length"] == re_.headers["content-length"]
    assert len(calls) == 8  # argon2 ran once per request on both paths
    # roughly equal cost (the argon2 hash dominates); best-of-three keeps this robust on a busy machine
    a, b = min(timings["new"]), min(timings["existing"])
    assert max(a, b) / min(a, b) < 2.0, timings
    # but the inbox owner learns the difference
    assert box.last(existing, "verify_notice") is not None and box.last(new, "verify") is not None


def test_unverified_signup_cannot_log_in_and_tokens_work_once(make_client):
    c = make_client(**loose(dev_auto_verify=False))
    email, r = signup(c)
    assert r.status_code == 202
    assert login(c, email).status_code == 401  # no account until the token is presented
    token = c.mailbox.last(email, "verify").token
    assert c.post("/api/v1/auth/verify", json={"token": "ev.nonsense"}).status_code == 400
    v = c.post("/api/v1/auth/verify", json={"token": token})
    assert v.status_code == 200 and v.json()["access_token"]
    assert (
        c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {v.json()['access_token']}"}).json()[
            "email"
        ]
        == email
    )
    assert c.post("/api/v1/auth/verify", json={"token": token}).status_code == 400  # single use
    assert login(c, email).status_code == 200


def test_expired_verification_token_and_resend(make_client):
    c = make_client(**loose(dev_auto_verify=False))
    email, _ = signup(c)
    first = c.mailbox.last(email, "verify").token
    sql(
        OWNER_URL,
        "UPDATE pending_signups SET expires_at = now() - interval '1 minute' WHERE email = $1",
        email,
    )
    assert c.post("/api/v1/auth/verify", json={"token": first}).status_code == 400
    email2, _ = signup(c)
    t1 = c.mailbox.last(email2, "verify").token
    signup(c, email2)  # a second sign-up replaces the pending one
    t2 = c.mailbox.last(email2, "verify").token
    assert t1 != t2
    assert c.post("/api/v1/auth/verify", json={"token": t1}).status_code == 400
    assert c.post("/api/v1/auth/verify", json={"token": t2}).status_code == 200


def test_add_member_responses_are_identical(client, account, mailbox):
    owner, member, outsider = account("en-owner"), account("en-member"), account("en-outsider")
    member.join(owner)
    ghost = unique_email("ghost")
    out = {}
    for name, email in (("ghost", ghost), ("outsider", outsider.email), ("member", member.email)):
        r = client.post("/api/v1/tenant/members", json={"email": email}, headers=owner.h)
        out[name] = (r.status_code, r.json(), r.headers["content-length"])
    assert out["ghost"] == out["outsider"] == out["member"] and out["ghost"][0] == 202
    for email in (ghost, outsider.email, member.email):
        assert mailbox.last(email, "invite") is not None
    # an invitation to someone who is already a member gives them nothing new
    tok = mailbox.last(member.email, "invite").token
    assert client.post("/api/v1/auth/accept-invite", json={"token": tok}, headers=member.h).status_code == 400
    # an invitation for an address that signs up later works
    new = unique_email("later")
    client.post("/api/v1/tenant/members", json={"email": new}, headers=owner.h)
    from conftest import Account

    later = Account.__new__(Account)
    later.client, later.email, later.password = client, new, PW
    client.post("/api/v1/auth/signup", json={"email": new, "password": PW, "tenant_name": "L"})
    later.tokens = login(client, new).json()
    later.tenant_id, later.user_id = later.tokens["tenant_id"], later.tokens["user_id"]
    token = mailbox.last(new, "invite").token
    assert (
        client.post("/api/v1/auth/accept-invite", json={"token": token}, headers=later.h).status_code == 200
    )
    assert new in {m["email"] for m in client.get("/api/v1/tenant/members", headers=owner.h).json()}


def test_invitations_are_tenant_isolated_in_sql(client, account, db_ready):
    a, b = account("iv-a"), account("iv-b")
    client.post("/api/v1/tenant/members", json={"email": unique_email("x")}, headers=a.h)
    client.post("/api/v1/tenant/members", json={"email": unique_email("y")}, headers=b.h)

    async def go():
        conn = await asyncpg.connect(APP_URL)
        try:
            assert await conn.fetchval("SELECT count(*) FROM invitations") == 0
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", a.tenant_id)
                rows = await conn.fetch("SELECT tenant_id FROM invitations")
                assert rows and {str(r["tenant_id"]) for r in rows} == {a.tenant_id}
        finally:
            await conn.close()

    asyncio.run(go())


def test_dev_email_sender_logs_redacted(caplog):
    from hwplatform.logredact import install_log_redaction

    install_log_redaction()
    key = "sk-ant-api03-" + "Z9y8X7w6" * 4
    with caplog.at_level(logging.INFO, logger="hwplatform.mail"):
        asyncio.run(LoggingEmailSender().send(Email(to="a@example.com", subject="s", body=f"leak {key}")))
    assert "a@example.com" in caplog.text and key not in caplog.text


# --- purge -------------------------------------------------------------------------------------------


def test_purge_removes_only_dead_rows(client, account, make_client, settings):
    a = account("purge")
    toks = [login(client, a.email).json()["refresh_token"] for _ in range(4)]
    keep, expired, revoked_old, revoked_new = (security.hash_token(t) for t in toks)
    tid = a.tenant_id
    q = "UPDATE refresh_tokens SET {} WHERE token_hash = $1"
    sql(OWNER_URL, q.format("expires_at = now() - interval '1 day'"), expired, tenant=tid)
    sql(OWNER_URL, q.format("revoked_at = now() - interval '10 days'"), revoked_old, tenant=tid)
    sql(OWNER_URL, q.format("revoked_at = now() - interval '1 hour'"), revoked_new, tenant=tid)

    # rate-limit rows: one old, one fresh; pending sign-ups and invitations: live and dead
    old_ev, new_ev = uuid.uuid4(), uuid.uuid4()
    ins = (
        "INSERT INTO auth_events (id, action, scope, subject, kind, at) "
        "VALUES ($1, 'login', 'ip', 'purge-test', 'attempt', {})"
    )
    sql(OWNER_URL, ins.format("now() - interval '3 days'"), old_ev)
    sql(OWNER_URL, ins.format("now()"), new_ev)
    c = make_client(**loose(dev_auto_verify=False))
    live_p, dead_p = signup(c)[0], signup(c)[0]
    sql(
        OWNER_URL,
        "UPDATE pending_signups SET expires_at = now() - interval '1 minute' WHERE email = $1",
        dead_p,
    )
    for em in ("live-inv", "dead-inv"):
        client.post("/api/v1/tenant/members", json={"email": f"{em}-{tid[:6]}@example.com"}, headers=a.h)
    sql(
        OWNER_URL,
        "UPDATE invitations SET expires_at = now() - interval '1 day' WHERE email LIKE 'dead-inv%'",
        tenant=tid,
    )

    result = asyncio.run(purge.purge_all(settings, url=settings.migration_database_url))
    assert result["refresh_tokens"] >= 2 and result["auth_events"] >= 1
    assert result["pending_signups"] >= 1 and result["invitations"] >= 1

    left = {r["token_hash"] for r in sql(OWNER_URL, "SELECT token_hash FROM refresh_tokens", tenant=tid)}
    assert keep in left and revoked_new in left  # live and recently revoked stay
    assert expired not in left and revoked_old not in left
    ev = {r["id"] for r in sql(OWNER_URL, "SELECT id FROM auth_events WHERE subject = 'purge-test'")}
    assert ev == {new_ev}
    pend = {r["email"] for r in sql(OWNER_URL, "SELECT email FROM pending_signups")}
    assert live_p in pend and dead_p not in pend
    inv = {r["email"] for r in sql(OWNER_URL, "SELECT email FROM invitations", tenant=tid)}
    assert any(e.startswith("live-inv") for e in inv) and not any(e.startswith("dead-inv") for e in inv)
    # the surviving session still works
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": toks[0]}).status_code == 200
    # a second run finds nothing more of this
    again = asyncio.run(purge.purge_all(settings, url=settings.migration_database_url, now=datetime.now(UTC)))
    assert again["pending_signups"] == 0


def test_owner_role_cannot_delete_live_tokens(client, account):
    a = account("purge-live")
    assert (
        client.post("/api/v1/auth/login", json={"email": a.email, "password": a.password}).status_code == 200
    )
    n = sql(OWNER_URL, "DELETE FROM refresh_tokens WHERE tenant_id = $1", uuid.UUID(a.tenant_id))
    assert n == "DELETE 0"  # the purge policy only reaches expired or revoked rows
