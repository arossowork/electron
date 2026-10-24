"""BYOK service layer (#14): store, validate, read and delete a tenant's LLM API key; usage accounting.

The plaintext key exists only inside `set_key` (from the request, to the validator and the sealer) and
inside `get_tenant_llm_key` (for the agent worker). It is never returned by the API and never logged.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from .crypto import Keyring, Sealed
from .db import Database
from .models import LlmUsage, TenantLlmKey
from .settings import Settings

PROVIDERS = ("anthropic",)
_KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{16,400}$")


class KeyFormatError(Exception):
    pass


class KeyRejected(Exception):
    """The provider says the key is not valid."""


class ValidatorUnavailable(Exception):
    """The provider was unreachable or answered oddly: the key is neither accepted nor rejected."""


class KeyConflict(Exception):
    pass


# A validator takes the plaintext key and returns normally if the provider accepts it.
KeyValidator = Callable[[str], Awaitable[None]]


def check_format(api_key: str) -> str:
    key = api_key.strip()
    if not _KEY_RE.match(key):
        raise KeyFormatError("not an Anthropic API key (expected sk-ant-...)")
    return key


def anthropic_validator(settings: Settings, client: httpx.AsyncClient | None = None) -> KeyValidator:
    """Cheap, free validation: list models. 401/403 = rejected; any other non-200 = unavailable."""

    async def validate(api_key: str) -> None:
        headers = {"x-api-key": api_key, "anthropic-version": settings.anthropic_version}
        url = settings.anthropic_api_base.rstrip("/") + "/v1/models"
        try:
            if client is not None:
                r = await client.get(url, headers=headers, params={"limit": 1})
            else:
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.get(url, headers=headers, params={"limit": 1})
        except httpx.HTTPError as e:
            raise ValidatorUnavailable(f"cannot reach the Anthropic API ({type(e).__name__})") from None
        if r.status_code in (401, 403):
            raise KeyRejected("the Anthropic API rejected this key")
        if r.status_code != 200:
            raise ValidatorUnavailable(f"the Anthropic API answered {r.status_code}")

    return validate


@dataclass(frozen=True)
class KeyStatus:
    provider: str
    last4: str
    status: str
    created_at: datetime
    rotated_at: datetime | None
    last_validated_at: datetime | None
    credential_type: str = "api_key"


def _status(row: TenantLlmKey) -> KeyStatus:
    return KeyStatus(
        row.provider,
        row.last4,
        row.status,
        row.created_at,
        row.rotated_at,
        row.last_validated_at,
        row.credential_type or "api_key",
    )


async def _row(s, tenant_id: uuid.UUID, provider: str) -> TenantLlmKey | None:
    q = select(TenantLlmKey).where(TenantLlmKey.tenant_id == tenant_id, TenantLlmKey.provider == provider)
    return (await s.execute(q)).scalar_one_or_none()


async def set_key(
    db: Database,
    keyring: Keyring,
    validator: KeyValidator,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    provider: str,
    api_key: str,
    *,
    credential_type: str = "api_key",
    fmt: Callable[[str], str] = check_format,
) -> KeyStatus:
    """Validate with the provider, then store (replacing the active credential of this tenant and provider;
    one per tenant and provider, of either type). `fmt` and `validator` come from the credential type's
    adapter (hwplatform.credentials)."""
    key = fmt(api_key)
    await validator(key)  # outside the transaction: no connection is held during the network call
    sealed = keyring.encrypt(key, tenant_id, provider)
    now = datetime.now(UTC)
    try:
        async with db.tx(tenant_id=tenant_id, user_id=user_id) as s:
            row = (
                await s.execute(
                    select(TenantLlmKey)
                    .where(TenantLlmKey.tenant_id == tenant_id, TenantLlmKey.provider == provider)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                row = TenantLlmKey(tenant_id=tenant_id, provider=provider, created_at=now)
                s.add(row)
            else:
                row.rotated_at = now
            row.ciphertext, row.nonce = sealed.ciphertext, sealed.nonce
            row.wrapped_dek, row.key_version = sealed.wrapped_dek, sealed.key_version
            row.last4, row.status, row.last_validated_at = key[-4:], "active", now
            row.credential_type = credential_type
            await s.flush()
            return _status(row)
    except IntegrityError as e:  # two first-time PUTs raced on the unique (tenant, provider)
        raise KeyConflict("concurrent update, retry") from e


async def key_status(
    db: Database, tenant_id: uuid.UUID, user_id: uuid.UUID, provider: str
) -> KeyStatus | None:
    async with db.tx(tenant_id=tenant_id, user_id=user_id) as s:
        row = await _row(s, tenant_id, provider)
        return _status(row) if row else None


async def delete_key(db: Database, tenant_id: uuid.UUID, user_id: uuid.UUID, provider: str) -> bool:
    async with db.tx(tenant_id=tenant_id, user_id=user_id) as s:
        q = delete(TenantLlmKey).where(TenantLlmKey.tenant_id == tenant_id, TenantLlmKey.provider == provider)
        return (await s.execute(q)).rowcount > 0


async def mark_invalid(db: Database, tenant_id: uuid.UUID, provider: str = "anthropic") -> None:
    """For the worker: the provider answered 401 for this key. The row stays, so the owner sees why."""
    async with db.tx(tenant_id=tenant_id) as s:
        row = await _row(s, tenant_id, provider)
        if row is not None:
            row.status = "invalid"


async def get_tenant_credential(
    db: Database, keyring: Keyring, tenant_id: uuid.UUID, provider: str = "anthropic"
) -> tuple[str, str] | None:
    """Internal: (credential_type, plaintext) of the tenant's active credential, or None. As
    get_tenant_llm_key; hwplatform.credentials decides whether the tenant may use that type."""
    async with db.tx(tenant_id=tenant_id) as s:
        row = await _row(s, tenant_id, provider)
        if row is None or row.status != "active":
            return None
        sealed = Sealed(row.ciphertext, row.nonce, row.wrapped_dek, row.key_version)
        ctype = row.credential_type or "api_key"
    return ctype, keyring.decrypt(sealed, tenant_id, provider)


async def get_tenant_llm_key(
    db: Database, keyring: Keyring, tenant_id: uuid.UUID, provider: str = "anthropic"
) -> str | None:
    """Internal: the plaintext key for the agent worker (#15), or None when the tenant has no usable key.

    Never expose the result through an API. Raises CryptoError when the stored row does not decrypt for
    this tenant (corruption, a row copied across tenants, or a master key missing from the keyring).
    """
    async with db.tx(tenant_id=tenant_id) as s:
        row = await _row(s, tenant_id, provider)
        if row is None or row.status != "active":
            return None
        sealed = Sealed(row.ciphertext, row.nonce, row.wrapped_dek, row.key_version)
    return keyring.decrypt(sealed, tenant_id, provider)


# --- usage -------------------------------------------------------------------------------------------


def _field(usage: Any, name: str) -> int:
    v = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    return int(v or 0)


async def record_usage(
    db: Database, tenant_id: uuid.UUID, model: str, usage: Any, run_id: uuid.UUID | None = None
) -> None:
    """Record the `usage` object of an Anthropic Messages response (dict or SDK object)."""
    async with db.tx(tenant_id=tenant_id) as s:
        s.add(
            LlmUsage(
                tenant_id=tenant_id,
                run_id=run_id,
                model=model,
                input_tokens=_field(usage, "input_tokens"),
                output_tokens=_field(usage, "output_tokens"),
                cache_creation_input_tokens=_field(usage, "cache_creation_input_tokens"),
                cache_read_input_tokens=_field(usage, "cache_read_input_tokens"),
            )
        )
