"""Events (#11, ADR 0001 section 5): CloudEvents 1.0 envelope, type catalogue, transactional outbox, relay."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..ids import uuid7

# ADR 5.2, catalogue v1. Emitting a type that is not listed here is a bug.
EVENT_TYPES = frozenset(
    {
        "run.queued",
        "run.started",
        "run.waiting",
        "run.resumed",
        "run.completed",
        "run.failed",
        "run.cancelled",
        "stage.started",
        "stage.progress",
        "stage.completed",
        "stage.failed",
        "stage.skipped",
        "question.asked",
        "question.answered",
        "question.expired",
        "artifact.created",
        "design_version.created",
    }
)
SCHEMA_VERSION = 1
DEFAULT_SOURCE = "hwplatform/worker"
STREAM = "HWFLOW_EVENTS"


def rfc3339(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def nats_subject(tenant_id: uuid.UUID | str, type: str) -> str:
    """hwflow.<tenant_id>.<type>; the UUID has dashes but no dots, so it is one subject token."""
    return f"hwflow.{uuid.UUID(str(tenant_id))}.{type}"


@dataclass(frozen=True)
class Event:
    type: str
    tenantid: str
    subject: str  # CloudEvents subject, e.g. "runs/<run_id>/stages/<key>"
    data: dict = field(default_factory=dict)
    runid: str | None = None
    seq: int | None = None  # per run, from 1, no gaps; None for events outside a run
    id: str = field(default_factory=lambda: str(uuid7()))
    source: str = DEFAULT_SOURCE
    time: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {self.type!r} (catalogue: ADR 0001, 5.2)")

    @property
    def nats_subject(self) -> str:
        return nats_subject(self.tenantid, self.type)

    def to_envelope(self) -> dict:
        env = {
            "specversion": "1.0",
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "subject": self.subject,
            "time": rfc3339(self.time),
            "datacontenttype": "application/json",
            "dataschema": f"hwflow.events/{self.type}/{SCHEMA_VERSION}",
            "tenantid": self.tenantid,
        }
        if self.runid is not None:
            env["runid"] = self.runid
        if self.seq is not None:
            env["seq"] = self.seq
        env["data"] = self.data
        return env
