"""One JSON object per line on stdout (stdlib only): ts, level, logger, msg + the bound context + extras.

`configure_logging(service)` replaces the root handlers, so uvicorn, nats, temporalio and the engine log the same way.
The record factory of `logredact` already redacts msg/args/exc_text; the formatter redacts the finished line once
more, so a key in an `extra` field or a context value cannot leak either. `HWPLATFORM_LOG_FORMAT=text` gives
plain lines for a terminal.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

from ..logredact import install_log_redaction, redact
from . import context

# attributes of a bare LogRecord: everything else on a record came from `extra=`
_STD = frozenset(
    logging.LogRecord("x", 0, "x", 0, "", (), None).__dict__.keys() | {"message", "asctime", "taskName"}
)


def otel_ids() -> dict[str, str]:
    """trace_id / span_id of the active OpenTelemetry span; {} when the API is not installed or no span is active."""
    try:
        from opentelemetry import trace
    except ImportError:
        return {}
    try:
        sc = trace.get_current_span().get_span_context()
        if sc.is_valid:
            return {"trace_id": f"{sc.trace_id:032x}", "span_id": f"{sc.span_id:016x}"}
    except Exception:  # noqa: BLE001, S110 - a log line must never fail
        pass
    return {}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str = ""):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        ms = int(record.msecs)
        doc: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{ms:03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if self.service:
            doc["service"] = self.service
        doc.update(context.current())
        doc.update(otel_ids())
        for k, v in record.__dict__.items():
            if k not in _STD and not k.startswith("_"):
                doc[k] = v
        if record.exc_info and record.exc_info[0] is not None:
            doc.setdefault("error_class", record.exc_info[0].__name__)
            doc["exc"] = record.exc_text or self.formatException(record.exc_info)
        return redact(json.dumps(doc, default=str, ensure_ascii=False))


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = context.current()
        tags = " ".join(f"{k}={ctx[k]}" for k in ("tenant_id", "run_id", "stage", "request_id") if k in ctx)
        base = (
            f"{self.formatTime(record, '%H:%M:%S')} {record.levelname} {record.name}: {record.getMessage()}"
        )
        out = f"{base} [{tags}]" if tags else base
        if record.exc_info:
            out += "\n" + self.formatException(record.exc_info)
        return redact(out)


def configure_logging(service: str, *, level: str = "INFO", fmt: str = "json", stream=None) -> None:
    install_log_redaction()
    h = logging.StreamHandler(stream or sys.stdout)
    h.setFormatter(JsonFormatter(service) if fmt == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error"):  # uvicorn must not bring its own handlers
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    # one access line per request comes from the HTTP middleware (it knows tenant_id and the route template)
    logging.getLogger("uvicorn.access").disabled = True
