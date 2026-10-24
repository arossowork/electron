"""The stage child process and its parent: context in, output lines out, peak memory back.

Child (`hwplatform.activities.child`): `setup_child(job)` configures JSON logging on stdout and binds the context the
worker exported into the job file; `write_rusage_at_exit(path)` records the peak RSS of the child and of the tools it ran.
Parent (`run_stage_activity`): every line the child prints is forwarded, JSON lines as they are (they carry the
context already), other lines (engine prints, tool output) wrapped in a log record of logger hwplatform.stage.child
with the activity's context, so `jq 'select(.run_id==...)'` finds all of it.
"""

from __future__ import annotations

import atexit
import json
import logging
import resource
import sys
from pathlib import Path

from ..logredact import redact
from . import context
from .logs import configure_logging

log = logging.getLogger("hwplatform.stage.child")


def setup_child(job: dict, *, level: str = "INFO", fmt: str = "json") -> None:
    configure_logging("stage-child", level=level, fmt=fmt)
    inp = job.get("input", {})
    context.bind(
        **{
            **job.get("log_context", {}),
            "tenant_id": job.get("tenant_id"),
            "run_id": inp.get("run_id"),
            "stage": inp.get("stage"),
        }
    )


def peak_rss_bytes() -> int:
    """Largest resident set of this process or of any child it waited for (ru_maxrss is KiB on Linux)."""
    kib = max(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    )
    return kib * 1024 if sys.platform != "darwin" else kib


def write_rusage_at_exit(path: Path) -> None:
    def write() -> None:
        try:
            path.write_text(json.dumps({"peak_rss_bytes": peak_rss_bytes()}))
        except OSError:
            pass

    atexit.register(write)


def read_rusage(path: Path) -> int:
    try:
        return int(json.loads(path.read_text()).get("peak_rss_bytes", 0))
    except (OSError, ValueError, AttributeError):
        return 0


def forward_line(line: str) -> None:
    if line.startswith("{") and line.endswith("}"):
        try:
            if isinstance(json.loads(line), dict):
                sys.stdout.write(redact(line) + "\n")
                sys.stdout.flush()
                return
        except ValueError:
            pass
    log.info(line[:2000])
