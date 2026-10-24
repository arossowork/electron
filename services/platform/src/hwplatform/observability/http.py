"""API side: request_id middleware (pure ASGI, so the contextvars set in dependencies stay visible), one access
log line per request with the route template, the HTTP metrics, and GET /metrics.

X-Request-ID is accepted when it is a short token of [A-Za-z0-9._:-] (else a new one is generated), bound for the
whole request (every log line carries it, the response echoes it) and the client may pass it on to correlate.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import FastAPI, Response

from . import context, metrics

log = logging.getLogger("hwplatform.access")
HEADER = b"x-request-id"
_VALID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
QUIET_ROUTES = frozenset({"/healthz", "/metrics"})  # debug level: probes would drown the log


def new_request_id(incoming: str | None) -> str:
    return incoming if incoming and _VALID.match(incoming) else uuid.uuid4().hex


def route_template(scope) -> str:
    """The route as declared (`/api/v1/runs/{run_id}`), never the concrete path: the label stays low-cardinality.
    The route object keeps the prefix of nested routers out of its `path`, so the template is built from the real
    path by replacing each path parameter's value with its name."""
    if scope.get("route") is None:
        return "unmatched"
    params = {str(v): k for k, v in (scope.get("path_params") or {}).items()}
    return "/".join("{" + params[seg] + "}" if seg in params else seg for seg in scope["path"].split("/"))


class ObservabilityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = next((v.decode("latin-1") for k, v in scope["headers"] if k == HEADER), None)
        rid = new_request_id(incoming)
        token = context.bind(request_id=rid)
        t0 = time.perf_counter()
        status = 500
        counted = False

        def route() -> str:
            return route_template(scope)

        async def send_wrapped(message):
            nonlocal status, counted
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", []), (HEADER, rid.encode())]}
                if not counted:
                    counted = True
                    rt, dt = route(), time.perf_counter() - t0
                    metrics.HTTP_REQUESTS.labels(scope["method"], rt, str(status)).inc()
                    metrics.HTTP_DURATION.labels(scope["method"], rt).observe(dt)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapped)
        except Exception as e:
            if not counted:
                metrics.HTTP_REQUESTS.labels(scope["method"], route(), "500").inc()
            log.error(
                "request failed",
                exc_info=True,
                extra={
                    "method": scope["method"],
                    "route": route(),
                    "status": 500,
                    "error_class": type(e).__name__,
                },
            )
            raise
        else:
            rt = route()
            rid_param = (scope.get("path_params") or {}).get("run_id")
            if rid_param:
                context.bind(run_id=rid_param)
            level = logging.DEBUG if rt in QUIET_ROUTES else logging.INFO
            log.log(
                level,
                "request",
                extra={
                    "method": scope["method"],
                    "route": rt,
                    "path": scope["path"],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                },
            )
        finally:
            context.reset(token)


def install(app: FastAPI, *, metrics_enabled: bool = True) -> None:
    app.add_middleware(ObservabilityMiddleware)
    if metrics_enabled:

        @app.get("/metrics", include_in_schema=False)
        async def prometheus() -> Response:
            body, ctype = metrics.render()
            return Response(body, media_type=ctype)
