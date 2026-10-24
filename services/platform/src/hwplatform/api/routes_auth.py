from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select

from .. import invites
from ..auth import AuthError
from ..auth.local import LocalAuthBackend, NotAMember, TokenPair, normalize_email
from ..auth.ratelimit import AuthGuard, RateLimited
from ..db import Database
from ..models import Membership, Tenant, User
from ..tenancy import TenantContext
from .deps import (
    _bearer,
    client_ip,
    get_backend,
    get_db,
    get_guard,
    rate_limited,
    tenant_context,
    unauthorized,
)
from .schemas import (
    AcceptedOut,
    AcceptInviteIn,
    AcceptInviteOut,
    ErrorOut,
    LoginIn,
    MeOut,
    RefreshIn,
    SignupIn,
    SwitchTenantIn,
    TenantRef,
    TokenOut,
    VerifyIn,
)

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


SIGNUP_ACCEPTED = AcceptedOut(detail="check your e-mail to confirm the sign-up")


@router.post("/signup", status_code=status.HTTP_202_ACCEPTED, response_model=AcceptedOut)
async def signup(
    body: SignupIn,
    request: Request,
    auth: LocalAuthBackend = Depends(_local),
    guard: AuthGuard = Depends(get_guard),
) -> AcceptedOut:
    """Start a sign-up. Always 202 with the same body, whether or not the e-mail is registered: a new
    address gets a verification token by e-mail (POST /auth/verify creates the account), a registered
    one gets a notice. Dev: HWPLATFORM_DEV_AUTO_VERIFY=true creates the account at once."""
    try:
        await guard.hit("signup", ip=client_ip(request), email=normalize_email(body.email))
    except RateLimited as e:
        raise rate_limited(e) from e
    await auth.signup(body.email, body.password, body.tenant_name, body.display_name)
    return SIGNUP_ACCEPTED


@router.post("/verify", response_model=TokenOut)
async def verify(
    body: VerifyIn,
    request: Request,
    auth: LocalAuthBackend = Depends(_local),
    guard: AuthGuard = Depends(get_guard),
) -> TokenOut:
    """Complete a sign-up with the e-mailed token; creates user, tenant and owner membership."""
    try:
        await guard.hit("verify", ip=client_ip(request))
    except RateLimited as e:
        raise rate_limited(e) from e
    try:
        return _out(await auth.verify_signup(body.token))
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired token") from e


@router.post("/login", response_model=TokenOut)
async def login(
    body: LoginIn,
    request: Request,
    auth: LocalAuthBackend = Depends(_local),
    guard: AuthGuard = Depends(get_guard),
) -> TokenOut:
    ip, email = client_ip(request), normalize_email(body.email)
    try:
        await guard.hit("login", ip=ip, email=email)
        await guard.check_lockout(ip=ip, email=email)
    except RateLimited as e:
        raise rate_limited(e) from e
    try:
        out = _out(await auth.login(body.email, body.password, body.tenant_id))
    except AuthError as e:
        await guard.record_failure(ip=ip, email=email)
        raise unauthorized("invalid credentials") from e
    await guard.clear_failures(email=email)
    return out


@router.post("/refresh", response_model=TokenOut)
async def refresh(
    body: RefreshIn,
    request: Request,
    auth: LocalAuthBackend = Depends(_local),
    guard: AuthGuard = Depends(get_guard),
) -> TokenOut:
    """Rotate: the presented refresh token is spent; reusing it later revokes the whole session."""
    try:
        await guard.hit("refresh", ip=client_ip(request))
    except RateLimited as e:
        raise rate_limited(e) from e
    try:
        return _out(await auth.refresh(body.refresh_token))
    except AuthError as e:
        raise unauthorized("invalid refresh token") from e


@router.post(
    "/switch-tenant",
    response_model=TokenOut,
    summary="Open a session in another of the user's tenants",
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}, 429: {"model": ErrorOut}},
)
async def switch_tenant(
    body: SwitchTenantIn,
    request: Request,
    auth: LocalAuthBackend = Depends(_local),
    guard: AuthGuard = Depends(get_guard),
) -> TokenOut:
    """Rotate like /auth/refresh, but the new tokens are for `tenant_id` (the user must be a member), so the
    UI needs no password. The presented refresh token is spent; the new one starts a session in that tenant."""
    try:
        await guard.hit("switch", ip=client_ip(request))
    except RateLimited as e:
        raise rate_limited(e) from e
    try:
        return _out(await auth.switch_tenant(body.refresh_token, body.tenant_id))
    except NotAMember as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not a member of that tenant") from e
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


@router.post("/accept-invite", response_model=AcceptInviteOut)
async def accept_invite(
    body: AcceptInviteIn,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Database = Depends(get_db),
    backend=Depends(get_backend),
) -> AcceptInviteOut:
    """Join the tenant of an invitation. The caller must be logged in as the invited e-mail address."""
    if creds is None:
        raise unauthorized()
    try:
        p = await backend.authenticate(creds.credentials)
    except AuthError as e:
        raise unauthorized(str(e)) from e
    try:
        tenant_id, role = await invites.accept_invitation(db, p.user_id, body.token)
    except invites.InviteError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired invitation") from e
    return AcceptInviteOut(tenant_id=tenant_id, role=role.value)
