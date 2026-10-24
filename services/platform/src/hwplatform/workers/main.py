"""`hwplatform-worker --queues hwflow-engine,hwflow-sim,hwflow-layout,hwflow-verify` (ADR 0001, 4.2 and 8).

One process polls every queue it is given, one Temporal Worker per queue, with the slots of the reduced
stack (`contracts.SLOTS`: the engine queue runs 4 workflow tasks and 2 activities, every tool queue 1
activity, so one layout at a time). The engine queue hosts the workflows and the local record_*
activities. The agent queue is polled from #15 on (its activities do not exist yet), so it is not in the
default list.

Workflows run in the Temporal sandbox; only the stdlib-only engine modules (hwflow.buildplan,
hwflow.artifacts, hwflow.ids, hwflow.runs) and hwplatform.contracts are passed through. SIGTERM / SIGINT:
stop polling, let running activities finish their cancel (the stage children are killed), then exit.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from datetime import timedelta

from ..contracts import Q_AGENT, QUEUES, SLOTS
from ..observability import configure_logging
from ..settings import Settings, get_settings

# prometheus_client is imported lazily below: the Temporal sandbox re-imports modules that import this one.
log = logging.getLogger("hwplatform.worker")

DEFAULT_QUEUES = tuple(q for q in QUEUES if q != Q_AGENT)
PASSTHROUGH = ("hwflow.buildplan", "hwflow.artifacts", "hwflow.ids", "hwflow.runs", "hwplatform.contracts")


def sandbox_runner():
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules(*PASSTHROUGH)
    )


def build_workers(
    client, deps, queues=DEFAULT_QUEUES, *, slots=SLOTS, workflows=None, activities=None, otel=False, **kw
):
    """One temporalio Worker per queue. `workflows` / `activities` override what is registered, `kw` goes to
    every
    Worker (tests)."""
    from temporalio.worker import Worker

    from ..activities import activities_for
    from ..contracts import Q_ENGINE
    from ..observability import metrics
    from ..observability.temporal import interceptors
    from ..workflows import WORKFLOWS

    kw.setdefault("graceful_shutdown_timeout", timedelta(seconds=15))
    kw.setdefault("interceptors", interceptors(otel))  # log context + activity metrics (#18)
    out = []
    for q in queues:
        s = slots.get(q, {"activities": 1})
        acts = activities(q) if activities is not None else activities_for(q, deps)
        wfs = (workflows if workflows is not None else WORKFLOWS) if q == Q_ENGINE else []
        if not acts and not wfs:
            continue
        metrics.SLOTS_MAX.labels(q).set(s.get("activities", 1))
        out.append(
            Worker(
                client,
                task_queue=q,
                workflows=wfs,
                activities=acts,
                max_concurrent_activities=s.get("activities", 1),
                max_concurrent_local_activities=8,
                max_concurrent_workflow_tasks=s.get("workflows", 1) if wfs else None,
                workflow_runner=sandbox_runner(),
                **kw,
            )
        )
    return out


class Health:
    """GET /livez: 200 while the event loop answers (independent of Temporal and Postgres). GET /readyz: 200
    only
    when the Temporal client is connected and every poller runs, else 503. A minimal HTTP/1.0 server on
    asyncio."""

    def __init__(self):
        self.client = None
        self.tasks: list[asyncio.Task] = []
        self.stopping = False

    def ready(self) -> bool:
        return (
            self.client is not None
            and bool(self.tasks)
            and not self.stopping
            and all(not t.done() for t in self.tasks)
        )

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        ctype = "text/plain"
        try:
            line = (await asyncio.wait_for(reader.readline(), 5)).decode("latin-1").split()
            path = line[1] if len(line) > 1 else ""
            if path.startswith("/livez"):
                code, body = 200, "ok"
            elif path.startswith("/readyz"):
                code, body = (200, "ready") if self.ready() else (503, "not ready")
            elif path.startswith("/metrics"):
                from ..observability import metrics

                code, (body, ctype) = 200, metrics.render()
            else:
                code, body = 404, "not found"
            if isinstance(body, str):
                body = body.encode()
            reason = {200: "OK", 503: "Service Unavailable", 404: "Not Found"}[code]
            writer.write(
                f"HTTP/1.0 {code} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}"
                f"\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except (TimeoutError, ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def serve(self, host: str, port: int):
        return await asyncio.start_server(self.handle, host, port)


def temporal_runtime(settings: Settings):
    """The SDK's core metrics (task latencies, poller and slot counts) in Prometheus format on their own port,
    `temporal_metrics_bind`; None = the default runtime without an endpoint. One Runtime per process."""
    global _runtime
    if not settings.temporal_metrics_bind:
        return None
    if _runtime is None:
        from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig

        _runtime = Runtime(
            telemetry=TelemetryConfig(metrics=PrometheusConfig(bind_address=settings.temporal_metrics_bind))
        )
    return _runtime


_runtime = None


async def connect(settings: Settings, stop: asyncio.Event):
    """The Temporal client, retrying every 5 s until it connects (or the worker is told to stop)."""
    from temporalio.client import Client

    while not stop.is_set():
        try:
            return await Client.connect(
                settings.temporal_address,
                namespace=settings.temporal_namespace,
                runtime=temporal_runtime(settings),
            )
        except Exception as e:  # noqa: BLE001 - Temporal not up yet: /readyz says 503 meanwhile
            log.warning(
                "cannot reach Temporal at %s (%s); retrying", settings.temporal_address, type(e).__name__
            )
            try:
                await asyncio.wait_for(stop.wait(), 5)
            except TimeoutError:
                pass
    return None


async def run(queues=DEFAULT_QUEUES, settings: Settings | None = None) -> None:
    """Serve /livez and /readyz, connect, poll `queues` until SIGTERM/SIGINT, then shut down gracefully: no
    new tasks, running activities get `worker_shutdown_grace_s` to finish, then they are cancelled (stage
    children
    killed, the activity retried elsewhere)."""
    from ..activities import Deps

    settings = settings or get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    health = Health()
    server = await health.serve(settings.worker_health_host, settings.worker_health_port)
    deps = Deps(settings)
    workers = []
    try:
        client = await connect(settings, stop)
        if client is None:
            return
        health.client = client
        workers = build_workers(
            client,
            deps,
            queues,
            otel=settings.otel_enabled,
            graceful_shutdown_timeout=timedelta(seconds=settings.worker_shutdown_grace_s),
        )
        log.info("worker polling %s on %s", ", ".join(queues), settings.temporal_address)
        health.tasks = [asyncio.create_task(w.run()) for w in workers]
        stopper = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait([*health.tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
        stopper.cancel()
        for t in done:
            if t is not stopper and t.exception():
                raise t.exception()
    finally:
        health.stopping = True
        log.info("worker shutting down (grace %.0f s)", settings.worker_shutdown_grace_s)
        await asyncio.gather(*(w.shutdown() for w in workers), return_exceptions=True)
        server.close()
        await deps.close()


def queues_from(arg: str | None, settings: Settings) -> tuple[str, ...]:
    raw = arg or settings.worker_queues or ",".join(DEFAULT_QUEUES)
    return tuple(q.strip() for q in raw.split(",") if q.strip())


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="hwplatform-worker", description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--queues",
        default=None,
        help=f"comma-separated task queues (default: HWPLATFORM_WORKER_QUEUES, else "
        f"{','.join(DEFAULT_QUEUES)})",
    )
    a = p.parse_args(argv)
    settings = get_settings()
    queues = queues_from(a.queues, settings)
    unknown = sorted(set(queues) - set(QUEUES))
    if unknown:
        p.error(f"unknown queues: {', '.join(unknown)} (known: {', '.join(QUEUES)})")
    configure_logging("worker", level=settings.log_level, fmt=settings.log_format)
    asyncio.run(run(queues, settings))


if __name__ == "__main__":
    main()
