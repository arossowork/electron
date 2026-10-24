from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .. import invites
from ..db import Database
from ..models import Role
from ..tenancy import MembershipRepo, TenantContext, TenantRepo
from .deps import get_db, require_admin, tenant_context
from .schemas import AcceptedOut, MemberIn, MemberOut, TenantOut

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


@router.post("/members", status_code=status.HTTP_202_ACCEPTED, response_model=AcceptedOut)
async def add_member(
    body: MemberIn,
    request: Request,
    ctx: TenantContext = Depends(require_admin),
    db: Database = Depends(get_db),
) -> AcceptedOut:
    """Invite an e-mail address to the current tenant. Always 202 with the same body, whether or not the
    address has an account or is a member already; the invitation token is sent by e-mail and accepted
    with POST /auth/accept-invite by the person who owns the address."""
    role = Role(body.role)
    if role is Role.admin and ctx.role is not Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only the owner can add an admin")
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        tenant = await TenantRepo(s, ctx).current()
    await invites.create_invitation(
        db,
        request.app.state.mailer,
        request.app.state.settings,
        ctx,
        body.email,
        role,
        tenant.name if tenant else "hwflow",
    )
    return AcceptedOut(detail="if the address can be invited, an invitation e-mail was sent")
