"""Auth flow through the API: signup, login, me, refresh rotation and reuse detection, logout, members."""

import pytest

from conftest import Account, unique_email

pytestmark = pytest.mark.integration


def test_signup_creates_tenant_and_owner(client, account):
    a = account("owner")
    me = client.get("/api/v1/auth/me", headers=a.h).json()
    assert me["email"] == a.email and me["role"] == "owner" and me["tenant_id"] == a.tenant_id
    assert [t["id"] for t in me["tenants"]] == [a.tenant_id]
    t = client.get("/api/v1/tenant", headers=a.h).json()
    assert t["id"] == a.tenant_id and t["max_concurrent_runs"] == 2
    assert t["slug"].startswith("tenant-owner-")


def test_signup_weak_password(client):
    r = client.post(
        "/api/v1/auth/signup", json={"email": unique_email("weak"), "password": "short", "tenant_name": "X"}
    )
    assert r.status_code == 422


def test_login(client, account):
    a = account("login")
    r = client.post("/api/v1/auth/login", json={"email": a.email, "password": a.password})
    assert r.status_code == 200 and r.json()["tenant_id"] == a.tenant_id
    assert (
        client.post("/api/v1/auth/login", json={"email": a.email, "password": "wrong-password"}).status_code
        == 401
    )
    assert (
        client.post("/api/v1/auth/login", json={"email": unique_email("nobody"), "password": "x"}).status_code
        == 401
    )


def test_no_or_bad_token(client):
    assert client.get("/api/v1/projects").status_code == 401
    assert client.get("/api/v1/projects", headers={"Authorization": "Bearer abc"}).status_code == 401


def test_refresh_rotates_and_detects_reuse(client, account):
    a = account("refresh")
    rt1 = a.tokens["refresh_token"]
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": rt1})
    assert r.status_code == 200
    rt2 = r.json()["refresh_token"]
    assert rt2 != rt1
    assert (
        client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
        ).status_code
        == 200
    )
    # rt1 was spent: presenting it again is reuse, which revokes the whole family, rt2 included.
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": rt1}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": rt2}).status_code == 401


def test_logout_revokes_session(client, account):
    a = account("logout")
    rt = a.tokens["refresh_token"]
    assert client.post("/api/v1/auth/logout", json={"refresh_token": rt}).status_code == 204
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": rt}).status_code == 401
    assert client.post("/api/v1/auth/logout", json={"refresh_token": "garbage"}).status_code == 204


def test_members_invite_accept_list_and_roles(client, account):
    a, c = account("team-owner"), account("team-member")
    ch = c.join(a)  # invitation e-mail -> accept -> login in a's tenant
    emails = {m["email"] for m in client.get("/api/v1/tenant/members", headers=a.h).json()}
    assert emails == {a.email, c.email}
    me = client.get("/api/v1/auth/me", headers=ch).json()
    assert me["role"] == "member" and {t["id"] for t in me["tenants"]} == {a.tenant_id, c.tenant_id}
    pa = a.project("shared")
    assert client.get(f"/api/v1/projects/{pa['id']}", headers=ch).status_code == 200
    r = client.post("/api/v1/tenant/members", json={"email": unique_email("x")}, headers=ch)
    assert r.status_code == 403
    # only the owner invites admins
    r = client.post("/api/v1/tenant/members", json={"email": unique_email("x"), "role": "admin"}, headers=ch)
    assert r.status_code == 403


def test_invitation_token_is_bound_to_the_invited_address(client, account, mailbox):
    a, c, other = account("inv-owner"), account("inv-invitee"), account("inv-other")
    assert client.post("/api/v1/tenant/members", json={"email": c.email}, headers=a.h).status_code == 202
    token = mailbox.last(c.email, "invite").token
    # someone else holding the token cannot use it
    assert (
        client.post("/api/v1/auth/accept-invite", json={"token": token}, headers=other.h).status_code == 400
    )
    assert client.post("/api/v1/auth/accept-invite", json={"token": token}, headers=c.h).status_code == 200
    # one use only
    assert client.post("/api/v1/auth/accept-invite", json={"token": token}, headers=c.h).status_code == 400
    assert (
        client.post("/api/v1/auth/accept-invite", json={"token": "inv.x.y"}, headers=c.h).status_code == 400
    )
    assert client.post("/api/v1/auth/accept-invite", json={"token": token}).status_code == 401


def test_login_into_foreign_tenant_refused(client, account):
    a, b = account("lf-a"), account("lf-b")
    r = client.post(
        "/api/v1/auth/login", json={"email": a.email, "password": a.password, "tenant_id": b.tenant_id}
    )
    assert r.status_code == 401


def test_account_helper_is_isolated(client):
    # Two signups never share a tenant.
    assert Account(client, "i1").tenant_id != Account(client, "i2").tenant_id
