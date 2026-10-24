"""Request dependencies: the database, the auth backend and the tenant context of the caller."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth import AuthBackend, AuthError
from ..db import Database
from ..tenancy import TenantContext, membership_of

_bearer = HTTPBearer(auto_error=False)


def unauthorized(detail: str = "not authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_backend(request: Request) -> AuthBackend:
    return request.app.state.auth


async def tenant_context(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Database = Depends(get_db),
    backend: AuthBackend = Depends(get_backend),
) -> TenantContext:
    """Authenticate the bearer token, then confirm the membership in the DB (role, not removed)."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized()
    try:
        p = await backend.authenticate(creds.credentials)
    except AuthError as e:
        raise unauthorized(str(e)) from e
    async with db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as s:
        m = await membership_of(s, p.tenant_id, p.user_id)
    if m is None:
        raise unauthorized("no membership in this tenant")
    return TenantContext(tenant_id=p.tenant_id, user_id=p.user_id, role=m.role)


def require_admin(ctx: TenantContext = Depends(tenant_context)) -> TenantContext:
    if not ctx.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner or admin role required")
    return ctx
