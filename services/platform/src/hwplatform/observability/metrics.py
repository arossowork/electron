"""Prometheus series of the platform (prometheus_client, default registry: process_* and python_gc_* come with it).

API process (GET /metrics on the API port):
  hwp_http_requests_total{method,route,status}, hwp_http_request_duration_seconds{method,route},
  hwp_sse_connections, hwp_relay_leader, hwp_relay_published_total, hwp_relay_backlog (events not yet acked by
  JetStream), hwp_outbox_unpublished_rows (same rows, kept as its own name for the alert), hwp_relay_lag_seconds
  (age of the oldest unpublished row).
Worker process (GET /metrics on the health port):
  hwp_activities_started_total / _completed_total / _failed_total{queue,activity,stage[,error_class]},
  hwp_stage_duration_seconds{queue,stage,outcome}, hwp_stage_child_peak_rss_bytes{stage},
  hwp_activity_slots_in_use{queue}, hwp_activity_slots_max{queue}.
The relay also runs as its own process (hwplatform-relay), so its series are defined here for both.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

HTTP_REQUESTS = Counter("hwp_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_DURATION = Histogram(
    "hwp_http_request_duration_seconds",
    "HTTP request latency (the time to the response head for streams)",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
SSE_CONNECTIONS = Gauge("hwp_sse_connections", "Open server-sent-event streams")

RELAY_LEADER = Gauge("hwp_relay_leader", "1 while this process holds the outbox relay lock")
RELAY_PUBLISHED = Counter("hwp_relay_published_total", "Outbox events acked by JetStream")
RELAY_BACKLOG = Gauge("hwp_relay_backlog", "Outbox rows not yet published to JetStream")
OUTBOX_UNPUBLISHED = Gauge("hwp_outbox_unpublished_rows", "Outbox rows with published_at IS NULL")
RELAY_LAG = Gauge("hwp_relay_lag_seconds", "Age of the oldest unpublished outbox row (0 when none)")

ACT_STARTED = Counter(
    "hwp_activities_started_total", "Activity attempts started", ["queue", "activity", "stage"]
)
ACT_COMPLETED = Counter(
    "hwp_activities_completed_total", "Activity attempts that returned", ["queue", "activity", "stage"]
)
ACT_FAILED = Counter(
    "hwp_activities_failed_total",
    "Activity attempts that raised (error_class: the ApplicationError type or the exception class)",
    ["queue", "activity", "stage", "error_class"],
)
STAGE_DURATION = Histogram(
    "hwp_stage_duration_seconds",
    "Wall time of a stage activity attempt",
    ["queue", "stage", "outcome"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)
CHILD_RSS = Histogram(
    "hwp_stage_child_peak_rss_bytes",
    "Peak resident set of a stage child process (and the tools it ran)",
    ["stage"],
    buckets=tuple(m * 1024 * 1024 for m in (64, 128, 256, 384, 512, 768, 1024, 1536, 2048, 4096)),
)
SLOTS_IN_USE = Gauge("hwp_activity_slots_in_use", "Activities running now", ["queue"])
SLOTS_MAX = Gauge("hwp_activity_slots_max", "Activity slots of the worker on this queue", ["queue"])


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
