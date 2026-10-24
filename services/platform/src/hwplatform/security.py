"""Password hashing (argon2id), access tokens (JWT) and opaque refresh tokens."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ph = PasswordHasher()  # argon2id, library defaults (RFC 9106 low-memory profile)
# Verified against when the e-mail is unknown, so a login costs the same either way.
_DUMMY_HASH = _ph.hash("not-a-real-password-" + secrets.token_hex(8))


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _ph.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _ph.check_needs_rehash(password_hash)


class TokenError(Exception):
    pass


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    expires_at: datetime


def issue_access_token(
    key: bytes,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: str,
    ttl_s: int,
    issuer: str,
    algorithm: str = "HS256",
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "role": role,  # informational; the API re-reads the membership on every request
        "typ": "access",
        "iss": issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_s)).timestamp()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(claims, key, algorithm=algorithm)


def decode_access_token(token: str, key: bytes, *, issuer: str, algorithm: str = "HS256") -> AccessClaims:
    try:
        c = jwt.decode(
            token,
            key,
            algorithms=[algorithm],  # pinned: never trust the header's alg
            issuer=issuer,
            options={"require": ["sub", "tid", "exp", "iat", "iss", "typ"]},
        )
        if c.get("typ") != "access":
            raise TokenError("not an access token")
        return AccessClaims(
            user_id=uuid.UUID(c["sub"]),
            tenant_id=uuid.UUID(c["tid"]),
            role=str(c.get("role", "")),
            expires_at=datetime.fromtimestamp(c["exp"], UTC),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as e:
        raise TokenError(str(e)) from e


# Refresh tokens: "rt.<tenant uuid hex>.<secret>". The tenant id is not secret; it tells the API
# which RLS tenant context to open before it looks the hash up.
_RT_PREFIX = "rt"


def new_refresh_token(tenant_id: uuid.UUID) -> str:
    return f"{_RT_PREFIX}.{tenant_id.hex}.{secrets.token_urlsafe(32)}"


def parse_refresh_token(token: str) -> uuid.UUID:
    try:
        prefix, tid, secret = token.split(".", 2)
        if prefix != _RT_PREFIX or len(secret) < 32:
            raise ValueError
        return uuid.UUID(hex=tid)
    except ValueError as e:
        raise TokenError("malformed refresh token") from e


def hash_token(token: str) -> str:
    # The token has 256 bits of entropy, so an unsalted fast hash is enough to make a DB leak useless.
    return hashlib.sha256(token.encode()).hexdigest()
