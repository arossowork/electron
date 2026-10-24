from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from ..auth import AuthBackend
from ..auth.local import LocalAuthBackend
from ..auth.ratelimit import AuthGuard
from ..crypto import Keyring, MasterKeyNotConfigured
from ..db import Database
from ..events.sse import NatsEventBus
from ..gateway import TemporalGateway, WorkflowGateway
from ..llm_keys import KeyValidator, anthropic_validator
from ..logredact import install_log_redaction
from ..mail import EmailSender, LoggingEmailSender
from ..observability import http as obs_http
from ..settings import Settings, get_settings
from ..storage import make_s3_client
from . import (
    routes_auth,
    routes_designs,
    routes_events,
    routes_llm_keys,
    routes_projects,
    routes_questions,
    routes_runs,
    routes_tenant,
)

OPENAPI_TAGS = [
    {"name": "auth", "description": "Sign-up, login, tokens."},
    {"name": "tenant", "description": "The current tenant, its members and quota."},
    {"name": "llm-keys", "description": "The tenant's own Claude API key (BYOK)."},
    {"name": "projects", "description": "Projects of a tenant."},
    {
        "name": "design-versions",
        "description": "Immutable, content-addressed snapshots of a project's files.",
    },
    {"name": "runs", "description": "Build and design runs: start, status, cancel, artifacts."},
    {"name": "questions", "description": "Questions a design run asks the person, and their answers."},
    {"name": "events", "description": "Server-sent events: replay from the outbox, then live."},
]


def create_app(
    settings: Settings | None = None,
    auth: AuthBackend | None = None,
    key_validator: KeyValidator | None = None,
    email_sender: EmailSender | None = None,
    gateway: WorkflowGateway | None = None,
    event_bus: NatsEventBus | None = None,
) -> FastAPI:
    """Build the API. `auth` lets an OIDC backend replace the built-in one; `key_validator` replaces the
    call to the Anthropic API that checks a BYOK key (tests inject a fake); `email_sender`
    delivers verification and invitation e-mails (default: log them to stdout, dev only);
    `gateway` starts, cancels and signals workflows (default: Temporal; tests inject a fake); `event_bus` is the
    live side of the SSE streams (default: NATS JetStream)."""
    settings = settings or get_settings()
    install_log_redaction()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database.from_settings(settings)
        app.state.db = db
        app.state.settings = settings
        app.state.mailer = email_sender or LoggingEmailSender()
        app.state.guard = AuthGuard(db, settings)
        app.state.auth = auth or LocalAuthBackend(db, settings, app.state.mailer)
        app.state.key_validator = key_validator or anthropic_validator(settings)
        app.state.gateway = gateway or TemporalGateway(settings)
        app.state.event_bus = event_bus or NatsEventBus(settings.nats_url)
        app.state.s3 = make_s3_client(settings)
        app.state.s3_public = (
            make_s3_client(settings, settings.s3_public_url) if settings.s3_public_url else None
        )
        try:
            app.state.keyring = Keyring.from_settings(settings)
        except MasterKeyNotConfigured:
            app.state.keyring = None  # BYOK endpoints answer 503 until a master key is configured
        relay_task = relay_stop = None
        if settings.relay_enabled:  # ADR 5.4: the relay runs inside the API process, leader-elected
            from ..events.relay import start_in_background

            relay_task, relay_stop = start_in_background(settings)
        try:
            yield
        finally:
            if relay_task is not None and relay_stop is not None:
                relay_stop.set()
                try:
                    await asyncio.wait_for(relay_task, timeout=10)
                except (TimeoutError, asyncio.CancelledError):
                    relay_task.cancel()
            await app.state.event_bus.close()
            await app.state.gateway.close()
            await db.dispose()

    app = FastAPI(
        title="hwflow platform",
        version="0.1.0",
        description="Multi-tenant API of the hwflow platform: projects, design versions, runs, artifacts, events.",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    v1 = APIRouter(prefix="/api/v1")
    if auth is None or auth.issues_tokens:
        v1.include_router(routes_auth.router)
    else:
        v1.add_api_route("/auth/me", routes_auth.me, methods=["GET"], tags=["auth"])
    v1.include_router(routes_tenant.router)
    v1.include_router(routes_projects.router)
    v1.include_router(routes_llm_keys.router)
    v1.include_router(routes_designs.router)
    v1.include_router(routes_runs.router)
    v1.include_router(routes_questions.router)
    v1.include_router(routes_events.router)
    app.include_router(v1)
    obs_http.install(app, metrics_enabled=settings.metrics_enabled)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"ok": True}

    return app
