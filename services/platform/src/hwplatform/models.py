"""Tables of the platform. Every tenant-scoped row carries tenant_id; RLS policies are in the migrations.

Defaults are computed in Python, not by the server, so an INSERT never needs RETURNING (a RETURNING row
must also pass the SELECT policy) and no attribute is lazily refreshed in async code.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Role(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    member = "member"


Tz = DateTime(timezone=True)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    # Quota for the run orchestrator (#11/#12): runs of this tenant that may execute at once.
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, default=2)
    created_at: Mapped[datetime] = mapped_column(Tz, default=utcnow)


class User(Base):
    """A global identity. Users are not tenant-scoped; they join tenants through memberships."""

    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lower-cased
    display_name: Mapped[str] = mapped_column(String(200), default="")
    # NULL when the identity comes from an external IdP (OIDC, M2).
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(Tz, default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[Role] = mapped_column(Enum(Role, name="membership_role"))
    created_at: Mapped[datetime] = mapped_column(Tz, default=utcnow)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("tenant_id", "slug"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(Tz, default=utcnow)


class RefreshToken(Base):
    """Opaque refresh tokens, stored as SHA-256 hashes. One family per login; rotation keeps the family.

    Presenting a token that was already rotated revokes the whole family (reuse detection).
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_family", "family_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(Tz, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(Tz)
    revoked_at: Mapped[datetime | None] = mapped_column(Tz, nullable=True)


class TenantLlmKey(Base):
    """A tenant's own LLM API key (BYOK, #14), envelope-encrypted: see crypto.py. One row per provider."""

    __tablename__ = "tenant_llm_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "provider"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="anthropic")
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)  # AES-256-GCM(DEK, api key), tag included
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary)  # nonce || AES-256-GCM(KEK, DEK)
    key_version: Mapped[int] = mapped_column(Integer)  # master key (KEK) version that wrapped the DEK
    last4: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | invalid
    created_at: Mapped[datetime] = mapped_column(Tz, default=utcnow)
    rotated_at: Mapped[datetime | None] = mapped_column(Tz, nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(Tz, nullable=True)


class LlmUsage(Base):
    """Tokens a tenant's key was charged, from the `usage` object of an API response."""

    __tablename__ = "llm_usage"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)  # FK once runs exist (#12)
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    at: Mapped[datetime] = mapped_column(Tz, default=utcnow)


class AuthEvent(Base):
    """Rate-limit and lockout bookkeeping (#26). Global, NOT tenant-scoped, so no RLS on it: the app role
    may only insert, read and delete rows, and only auth/ratelimit.py touches the table.

    kind 'attempt' = a request was made; 'fail' = a login failed. `subject` is the SHA-256 of the
    normalised e-mail or of the client IP (scope 'email' | 'ip').
    """

    __tablename__ = "auth_events"
    __table_args__ = (
        Index("ix_auth_events_lookup", "action", "scope", "subject", "kind", "at"),
        Index("ix_auth_events_at", "at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    action: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(8))
    subject: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(8))
    at: Mapped[datetime] = mapped_column(Tz, default=utcnow)


class PendingSignup(Base):
    """A sign-up waiting for its e-mail token. Global (the user and tenant do not exist yet)."""

    __tablename__ = "pending_signups"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    tenant_name: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(Tz, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(Tz)


class Invitation(Base):
    """An invitation to join a tenant, accepted by the invited user once logged in. Tenant-scoped, RLS."""

    __tablename__ = "invitations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[Role] = mapped_column(Enum(Role, name="membership_role", create_type=False))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(Tz, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(Tz)
    accepted_at: Mapped[datetime | None] = mapped_column(Tz, nullable=True)
