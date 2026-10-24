from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from ..auth import AuthError
from ..auth.local import LocalAuthBackend, SignupConflict, TokenPair
from ..db import Database
from ..models import Membership, Tenant, User
from ..tenancy import TenantContext
from .deps import get_backend, get_db, tenant_context, unauthorized
from .schemas import LoginIn, MeOut, RefreshIn, SignupIn, TenantRef, TokenOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _local(backend=Depends(get_backend)) -> LocalAuthBackend:
    if not isinstance(backend, LocalAuthBackend):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "token endpoints are served by the identity provider")
    return backend


def _out(p: TokenPair) -> TokenOut:
    return TokenOut(
        access_token=p.access_token,
        refresh_token=p.refresh_token,
        expires_in=p.expires_in,
        tenant_id=p.tenant_id,
        user_id=p.user_id,
    )


@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=TokenOut)
async def signup(body: SignupIn, auth: LocalAuthBackend = Depends(_local)) -> TokenOut:
    """Create a user, a new tenant and an owner membership; returns a logged-in session."""
    try:
        return _out(await auth.signup(body.email, body.password, body.tenant_name, body.display_name))
    except SignupConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "e-mail already registered") from e


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, auth: LocalAuthBackend = Depends(_local)) -> TokenOut:
    try:
        return _out(await auth.login(body.email, body.password, body.tenant_id))
    except AuthError as e:
        raise unauthorized("invalid credentials") from e


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn, auth: LocalAuthBackend = Depends(_local)) -> TokenOut:
    """Rotate: the presented refresh token is spent; reusing it later revokes the whole session."""
    try:
        return _out(await auth.refresh(body.refresh_token))
    except AuthError as e:
        raise unauthorized("invalid refresh token") from e


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshIn, auth: LocalAuthBackend = Depends(_local)) -> Response:
    await auth.logout(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MeOut)
async def me(ctx: TenantContext = Depends(tenant_context), db: Database = Depends(get_db)) -> MeOut:
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        user = (await s.execute(select(User).where(User.id == ctx.user_id))).scalar_one()
        q = (
            select(Membership, Tenant)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(Membership.user_id == ctx.user_id)
            .order_by(Membership.created_at)
        )
        rows = (await s.execute(q)).all()
    return MeOut(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        tenant_id=ctx.tenant_id,
        role=ctx.role.value,
        tenants=[TenantRef(id=t.id, name=t.name, role=m.role.value) for m, t in rows],
    )
