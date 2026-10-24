"""The build pipeline as a library: stages, status, report, trace, hooks and the build loop.

The CLI, the workers and the tests call these functions; hwflow.cli only parses arguments."""

from .report import write_report
from .runner import STAGES, BuildOptions, run_build, run_stage
from .stages import (
    isolation_distances,
    layout_auto,
    layout_diagnosis,
    net_stats,
    stage_check,
    stage_fab,
    stage_layout,
    stage_post,
    stage_sch,
    stage_sim,
)
from .status import _design_hash, _inputs_hash, _save_status, _status
from .trace import missing_evidence, trace

__all__ = [
    "STAGES", "BuildOptions", "isolation_distances", "layout_auto",
    "layout_diagnosis", "missing_evidence", "net_stats", "run_build", "run_stage", "stage_check",
    "stage_fab", "stage_layout", "stage_post", "stage_sch", "stage_sim", "trace", "write_report",
    "_design_hash", "_inputs_hash", "_save_status", "_status",
]
