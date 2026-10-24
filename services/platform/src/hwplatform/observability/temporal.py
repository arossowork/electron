"""Worker side: one Temporal activity interceptor for every activity of every queue (local ones included).

It binds tenant_id / run_id / workflow_id (from the `ctx: RunCtx` that every activity input carries and from
activity.info()), activity, stage and attempt for the duration of the attempt, counts started / completed /
failed, times stage attempts, tracks the slots in use, and logs a failed attempt with the error class.
Tracing is optional: with `otel_enabled` the SDK's own `TracingInterceptor` is added (needs
opentelemetry-api/sdk and an exporter configured by the deployment; none ships in M1).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput, Interceptor

from . import context, metrics

log = logging.getLogger("hwplatform.activity")


def error_class(e: BaseException) -> str:
    """The domain error type of an ApplicationError (ToolMissing, WorkerLost, ...), else the Python class."""
    if isinstance(e, ApplicationError) and e.type:
        return e.type
    return type(e).__name__


def activity_fields(args: Any) -> dict[str, Any]:
    info = activity.info()
    first = args[0] if args else None
    ctx = getattr(first, "ctx", None)
    out: dict[str, Any] = {
        "workflow_id": info.workflow_id,
        "activity": info.activity_type,
        "attempt": info.attempt,
        "queue": info.task_queue,
    }
    if ctx is not None:
        out.update(tenant_id=getattr(ctx, "tenant_id", None), run_id=getattr(ctx, "run_id", None))
    stage = getattr(first, "stage", None)
    if isinstance(stage, str):
        out["stage"] = stage
    return out


class _Inbound(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        f = activity_fields(input.args)
        queue, name, stage = f.pop("queue"), f["activity"], f.get("stage", "")
        labels = (queue, name, stage)
        token = context.bind(**f)
        metrics.ACT_STARTED.labels(*labels).inc()
        metrics.SLOTS_IN_USE.labels(queue).inc()
        t0 = time.perf_counter()
        outcome = "completed"
        try:
            log.info("activity started")
            result = await self.next.execute_activity(input)
            metrics.ACT_COMPLETED.labels(*labels).inc()
            return result
        except BaseException as e:
            cancelled = isinstance(e, CancelledError) or type(e).__name__ == "CancelledError"
            outcome = "cancelled" if cancelled else "failed"
            cls = error_class(e)
            metrics.ACT_FAILED.labels(*labels, cls).inc()
            log.log(
                logging.WARNING if cancelled else logging.ERROR,
                "activity %s: %s",
                outcome,
                str(e)[:500],
                exc_info=None if cancelled else e,
                extra={"error_class": cls, "outcome": outcome},
            )
            raise
        finally:
            dt = time.perf_counter() - t0
            metrics.SLOTS_IN_USE.labels(queue).dec()
            if stage:
                metrics.STAGE_DURATION.labels(queue, stage, outcome).observe(dt)
            if outcome == "completed":
                log.info("activity completed", extra={"duration_ms": round(dt * 1000, 1)})
            context.reset(token)


class ObservabilityInterceptor(Interceptor):
    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return _Inbound(next)


def interceptors(otel_enabled: bool = False) -> list[Interceptor]:
    out: list[Interceptor] = [ObservabilityInterceptor()]
    if otel_enabled:
        from temporalio.contrib.opentelemetry import TracingInterceptor

        out.append(TracingInterceptor())
    return out
