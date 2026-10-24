"""Master-key rotation: re-wrap the DEK of every stored key under the newest master key.

Runs as the schema owner (the only role with the cross-tenant policy on tenant_llm_keys). Only the
wrapped DEK and key_version change; the ciphertext and the plaintext keys are untouched.
"""

from __future__ import annotations

from sqlalchemy import select

from .crypto import Keyring
from .db import Database, make_engine
from .models import TenantLlmKey
from .settings import Settings


async def rotate_all(settings: Settings, url: str | None = None) -> tuple[int, int]:
    """Returns (rows re-wrapped, newest version)."""
    ring = Keyring.from_settings(settings)
    db = Database(make_engine(url or settings.migration_database_url, 1))
    n = 0
    try:
        async with db.tx() as s:
            q = select(TenantLlmKey).where(TenantLlmKey.key_version != ring.newest).with_for_update()
            for row in (await s.execute(q)).scalars():
                row.wrapped_dek, row.key_version = ring.rewrap(
                    row.wrapped_dek, row.key_version, row.tenant_id, row.provider
                )
                n += 1
    finally:
        await db.dispose()
    return n, ring.newest
