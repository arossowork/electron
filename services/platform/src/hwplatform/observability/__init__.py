"""Light observability (issue #18): JSON logs with a propagated context, Prometheus metrics, an optional OTel
trace id. No collector or backend is required: `kubectl logs | jq` and `/metrics` are the M1 tools
(docs/runbook.md). Import-light on purpose: `context`, `logs` and `metrics` use the stdlib and prometheus_client only.
"""

from __future__ import annotations

from .context import bind, bound, current, export, reset
from .logs import configure_logging

__all__ = ["bind", "bound", "configure_logging", "current", "export", "reset"]
