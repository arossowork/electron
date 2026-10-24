"""What hwharness plugs into the engine (entry point `hwflow.hooks`, see hwflow.pipeline.hooks)."""

from __future__ import annotations

import json

from hwflow.pipeline import hooks
from hwflow.pipeline.status import _status

from .evals.scorer import score
from .protocol import approval_findings, approve

SCORE_HISTORY_KEYS = ("check_errors", "erc_errors", "sim_pass_ratio", "sim_measures", "mc_min_yield", "drc_errors",
                      "unrouted", "post_pass_ratio", "area_mm2", "vias", "cost_usd", "req_total", "req_unverified",
                      "req_failing")


def _approve_args(p) -> None:
    p.add_argument("project")
    p.add_argument("--note", default="", help="who accepted it and where (e.g. round 3 q1)")


def _cmd_approve(d, a) -> int:
    x = approve(d, a.note)
    print(f"approved: {x['layers']} layers, {x['width_mm']} x {x['height_mm']} mm, BOM ${x['cost_usd']:.2f} "
          f"(build/approved.json)")
    return 0


def _cmd_status(d, a) -> int:
    print(json.dumps(_status(d).get("score", {}), indent=2))
    return 0


def register() -> None:
    hooks.register_check_findings(approval_findings)
    hooks.register_check_state(lambda d, findings: {"approval": [x for x in findings if x["code"] == "approval"]})
    hooks.register_report_extra("score", score, SCORE_HISTORY_KEYS)
    hooks.register_command("approve", "record layers, board size and BOM cost as accepted by the product person",
                           _approve_args, _cmd_approve)
    hooks.register_command("status", "print the score card of the last report", lambda p: p.add_argument("project"),
                           _cmd_status)
