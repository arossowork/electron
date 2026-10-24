"""Unit tests: passwords, access tokens, refresh tokens, storage keys. No database."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from hwplatform import security, storage

KEY = b"k" * 32
ISS = "hwplatform"


def test_password_hash_roundtrip():
    h = security.hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert security.verify_password(h, "correct horse battery")
    assert not security.verify_password(h, "wrong")
    assert not security.needs_rehash(h)


def test_password_unknown_user_and_garbage_hash():
    assert not security.verify_password(None, "anything")
    assert not security.verify_password("not-a-hash", "anything")


def _token(**kw):
    args = dict(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="owner", ttl_s=60, issuer=ISS)
    args.update(kw)
    return security.issue_access_token(KEY, **args), args


def test_access_token_roundtrip():
    tok, a = _token()
    c = security.decode_access_token(tok, KEY, issuer=ISS)
    assert c.user_id == a["user_id"] and c.tenant_id == a["tenant_id"] and c.role == "owner"


def test_access_token_expired():
    tok, _ = _token(now=datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(security.TokenError):
        security.decode_access_token(tok, KEY, issuer=ISS)


def test_access_token_wrong_key_or_issuer():
    tok, _ = _token()
    with pytest.raises(security.TokenError):
        security.decode_access_token(tok, b"x" * 32, issuer=ISS)
    with pytest.raises(security.TokenError):
        security.decode_access_token(tok, KEY, issuer="someone-else")


def test_access_token_alg_none_and_wrong_type_rejected():
    claims = {
        "sub": str(uuid.uuid4()),
        "tid": str(uuid.uuid4()),
        "typ": "access",
        "iss": ISS,
        "iat": 0,
        "exp": 4102444800,
    }
    unsigned = jwt.encode(claims, None, algorithm="none")
    with pytest.raises(security.TokenError):
        security.decode_access_token(unsigned, KEY, issuer=ISS)
    refresh_like = jwt.encode({**claims, "typ": "refresh"}, KEY, algorithm="HS256")
    with pytest.raises(security.TokenError):
        security.decode_access_token(refresh_like, KEY, issuer=ISS)


def test_refresh_token_format():
    tid = uuid.uuid4()
    t = security.new_refresh_token(tid)
    assert security.parse_refresh_token(t) == tid
    assert security.new_refresh_token(tid) != t
    assert len(security.hash_token(t)) == 64
    for bad in ("", "rt.nothex.secret", f"xx.{tid.hex}.{'a' * 43}", f"rt.{tid.hex}.short"):
        with pytest.raises(security.TokenError):
            security.parse_refresh_token(bad)


def test_storage_prefix():
    tid = uuid.uuid4()
    assert storage.tenant_prefix(tid) == f"tenants/{tid}/"
    k = storage.tenant_key(tid, "projects", "p1", "gerbers.zip")
    assert k == f"tenants/{tid}/projects/p1/gerbers.zip"
    assert storage.key_in_tenant(tid, k)
    assert not storage.key_in_tenant(uuid.uuid4(), k)
    assert not storage.key_in_tenant(tid, f"tenants/{tid}/../{uuid.uuid4()}/x")
    for bad in ("..", "a/b", "", "/abs"):
        with pytest.raises(ValueError):
            storage.tenant_key(tid, bad)
