"""Request dependencies: the database, the auth backend and the tenant context of the caller."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth import AuthBackend, AuthError
from ..auth.ratelimit import AuthGuard, RateLimited
from ..db import Database
from ..observability.context import bind
from ..tenancy import TenantContext, membership_of

_bearer = HTTPBearer(auto_error=False)


def unauthorized(detail: str = "not authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_guard(request: Request) -> AuthGuard:
    return request.app.state.guard


def client_ip(request: Request) -> str:
    """The peer address. Behind a proxy, uvicorn's proxy_headers (forwarded-allow-ips) makes this the
    real client; without it every request would share the proxy's address."""
    return request.client.host if request.client else "unknown"


def rate_limited(e: RateLimited) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "too many attempts, try again later",
        headers={"Retry-After": str(e.retry_after_s)},
    )


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
    bind(tenant_id=p.tenant_id, user_id=p.user_id)  # every later log line of this request carries them
    return TenantContext(tenant_id=p.tenant_id, user_id=p.user_id, role=m.role)


def require_admin(ctx: TenantContext = Depends(tenant_context)) -> TenantContext:
    if not ctx.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner or admin role required")
    return ctx


# --- runs, storage, events (#12) ---------------------------------------------------------------------


def get_gateway(request: Request):
    return request.app.state.gateway


def tenant_store(request: Request, ctx: TenantContext):
    """The S3 ArtifactStore bound to the caller's tenant (no call can name another tenant's prefix)."""
    from ..storage import S3ArtifactStore

    s = request.app.state.settings
    return S3ArtifactStore(request.app.state.s3, s.s3_bucket, ctx.tenant_id)


def download_store(request: Request, ctx: TenantContext):
    """Same store, but on the client that signs URLs for the public endpoint (None when not configured)."""
    from ..storage import S3ArtifactStore

    pub = request.app.state.s3_public
    if pub is None:
        return None
    return S3ArtifactStore(pub, request.app.state.settings.s3_bucket, ctx.tenant_id)
