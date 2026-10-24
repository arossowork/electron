"""Engine and transactions with a tenant context.

Every transaction the API opens goes through Database.tx(). It sets the RLS variables with
set_config(..., is_local => true), which is SET LOCAL: the value lives until COMMIT or ROLLBACK, so a
pooled connection never carries one request's tenant into the next.

RLS variables (read by the policies in migrations/versions/0001_tenancy.py):
  app.tenant_id     current tenant; tenant-scoped rows are visible only for it
  app.user_id       current user; lets a user see its own row and its own memberships across tenants
  app.lookup_email  one e-mail address whose users row may be read (login, add member by e-mail)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .settings import Settings


def make_engine(url: str, pool_size: int = 5) -> AsyncEngine:
    return create_async_engine(url, pool_size=pool_size, pool_pre_ping=True)


_SET = text(
    "SELECT set_config('app.tenant_id', :tenant, true),"
    " set_config('app.user_id', :user, true),"
    " set_config('app.lookup_email', :email, true)"
)


class Database:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_settings(cls, s: Settings) -> Database:
        return cls(make_engine(s.database_url, s.db_pool_size))

    @asynccontextmanager
    async def tx(
        self,
        *,
        tenant_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        lookup_email: str | None = None,
    ) -> AsyncIterator[AsyncSession]:
        """One transaction with the given RLS context; commits on success, rolls back on error."""
        async with self.sessions() as session, session.begin():
            await session.execute(
                _SET,
                {
                    "tenant": str(tenant_id) if tenant_id else "",
                    "user": str(user_id) if user_id else "",
                    "email": lookup_email or "",
                },
            )
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
