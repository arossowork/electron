"""Tenant context and the query layer.

Isolation is enforced twice. The repositories here add `tenant_id = ctx.tenant_id` to every query
(layer 1); the Postgres policies filter the same rows again from app.tenant_id (layer 2), so a query
that forgets its filter still sees only the current tenant.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Membership, Project, Role, Tenant, User


@dataclass(frozen=True)
class TenantContext:
    """Who is calling, for which tenant, with which role (read from the DB on every request)."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role in (Role.owner, Role.admin)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str, max_len: int = 60) -> str:
    s = _SLUG_RE.sub("-", name.lower()).strip("-")[:max_len].strip("-")
    return s or "x"


async def membership_of(s: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Membership | None:
    """The membership that turns an authenticated principal into a TenantContext."""
    q = select(Membership).where(Membership.tenant_id == tenant_id, Membership.user_id == user_id)
    return (await s.execute(q)).scalar_one_or_none()


class TenantRepo:
    def __init__(self, s: AsyncSession, ctx: TenantContext):
        self.s, self.ctx = s, ctx

    async def current(self) -> Tenant | None:
        return await self.s.get(Tenant, self.ctx.tenant_id)


class MembershipRepo:
    def __init__(self, s: AsyncSession, ctx: TenantContext):
        self.s, self.ctx = s, ctx

    async def list_members(self) -> list[tuple[Membership, User]]:
        q = (
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.tenant_id == self.ctx.tenant_id)
            .order_by(Membership.created_at)
        )
        return [(m, u) for m, u in (await self.s.execute(q)).all()]

    async def get_for_user(self, user_id: uuid.UUID) -> Membership | None:
        q = select(Membership).where(
            Membership.tenant_id == self.ctx.tenant_id, Membership.user_id == user_id
        )
        return (await self.s.execute(q)).scalar_one_or_none()

    async def add(self, user_id: uuid.UUID, role: Role) -> Membership:
        m = Membership(tenant_id=self.ctx.tenant_id, user_id=user_id, role=role)
        self.s.add(m)
        await self.s.flush()
        return m


class ProjectRepo:
    def __init__(self, s: AsyncSession, ctx: TenantContext):
        self.s, self.ctx = s, ctx

    async def list(self) -> list[Project]:
        q = select(Project).where(Project.tenant_id == self.ctx.tenant_id).order_by(Project.created_at)
        return list((await self.s.execute(q)).scalars())

    async def get(self, project_id: uuid.UUID) -> Project | None:
        q = select(Project).where(Project.tenant_id == self.ctx.tenant_id, Project.id == project_id)
        return (await self.s.execute(q)).scalar_one_or_none()

    async def create(self, name: str, slug: str) -> Project:
        p = Project(tenant_id=self.ctx.tenant_id, name=name, slug=slug, created_by=self.ctx.user_id)
        self.s.add(p)
        await self.s.flush()
        return p

    async def delete(self, project_id: uuid.UUID) -> bool:
        q = delete(Project).where(Project.tenant_id == self.ctx.tenant_id, Project.id == project_id)
        return (await self.s.execute(q)).rowcount > 0
