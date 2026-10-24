from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from ..auth.local import normalize_email
from ..db import Database
from ..models import Role, User
from ..tenancy import MembershipRepo, TenantContext, TenantRepo
from .deps import get_db, require_admin, tenant_context
from .schemas import MemberIn, MemberOut, TenantOut

router = APIRouter(prefix="/tenant", tags=["tenant"])


@router.get("", response_model=TenantOut)
async def get_tenant(ctx: TenantContext = Depends(tenant_context), db: Database = Depends(get_db)):
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        t = await TenantRepo(s, ctx).current()
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")
    return t


@router.get("/members", response_model=list[MemberOut])
async def list_members(ctx: TenantContext = Depends(tenant_context), db: Database = Depends(get_db)):
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        rows = await MembershipRepo(s, ctx).list_members()
    return [
        MemberOut(
            user_id=u.id,
            email=u.email,
            display_name=u.display_name,
            role=m.role.value,
            created_at=m.created_at,
        )
        for m, u in rows
    ]


@router.post("/members", status_code=status.HTTP_201_CREATED, response_model=MemberOut)
async def add_member(
    body: MemberIn, ctx: TenantContext = Depends(require_admin), db: Database = Depends(get_db)
):
    """Add an existing user (by e-mail) to the current tenant. No invitation e-mail is sent yet."""
    role = Role(body.role)
    if role is Role.admin and ctx.role is not Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only the owner can add an admin")
    email = normalize_email(body.email)
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id, lookup_email=email) as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "no user with this e-mail; they must sign up first"
            )
        repo = MembershipRepo(s, ctx)
        if await repo.get_for_user(user.id) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "already a member")
        m = await repo.add(user.id, role)
        return MemberOut(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=m.role.value,
            created_at=m.created_at,
        )
