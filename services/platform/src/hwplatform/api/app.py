from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from ..auth import AuthBackend
from ..auth.local import LocalAuthBackend
from ..crypto import Keyring, MasterKeyNotConfigured
from ..db import Database
from ..llm_keys import KeyValidator, anthropic_validator
from ..logredact import install_log_redaction
from ..settings import Settings, get_settings
from . import routes_auth, routes_llm_keys, routes_projects, routes_tenant


def create_app(
    settings: Settings | None = None,
    auth: AuthBackend | None = None,
    key_validator: KeyValidator | None = None,
) -> FastAPI:
    """Build the API. `auth` lets an OIDC backend replace the built-in one; `key_validator` replaces the
    call to the Anthropic API that checks a BYOK key (tests inject a fake)."""
    settings = settings or get_settings()
    install_log_redaction()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database.from_settings(settings)
        app.state.db = db
        app.state.auth = auth or LocalAuthBackend(db, settings)
        app.state.key_validator = key_validator or anthropic_validator(settings)
        try:
            app.state.keyring = Keyring.from_settings(settings)
        except MasterKeyNotConfigured:
            app.state.keyring = None  # BYOK endpoints answer 503 until a master key is configured
        try:
            yield
        finally:
            await db.dispose()

    app = FastAPI(title="hwflow platform", version="0.1.0", lifespan=lifespan)
    v1 = APIRouter(prefix="/api/v1")
    if auth is None or auth.issues_tokens:
        v1.include_router(routes_auth.router)
    else:
        v1.add_api_route("/auth/me", routes_auth.me, methods=["GET"], tags=["auth"])
    v1.include_router(routes_tenant.router)
    v1.include_router(routes_projects.router)
    v1.include_router(routes_llm_keys.router)
    app.include_router(v1)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"ok": True}

    return app
