"""Deterministic scorecard."""

from __future__ import annotations


from hwflow.core.design import Design
from hwflow.pipeline.status import _fresh_status


def score(d: Design) -> dict:
    """Deterministic scorecard used by evals."""
    s = _fresh_status(d)
    cost, missing = d.cost()
    g = lambda k, f, dflt=None: f(s[k]) if k in s and "exception" not in s[k] else dflt  # noqa: E731
    return {
        "check_errors": g("check", lambda c: c["errors"]),
        "erc_errors": g("sch", lambda c: c["errors"]),
        "sim_pass_ratio": g("sim", lambda c: c["passed"] / max(c["total"], 1)),
        "sim_measures": g("sim", lambda c: c["total"]),
        "derating_issues": g("sim", lambda c: len(c["derating_issues"])),
        "generic_models": g("sim", lambda c: len(c.get("generic_models", []))),
        "mc_min_yield": g("sim", lambda c: min((m["yield"] for m in c.get("monte_carlo") or []), default=None)),
        "drc_errors": g("layout", lambda c: c["drc_errors"]),
        "unrouted": g("layout", lambda c: c["unrouted"]),
        "post_pass_ratio": g("post", lambda c: sum(x["pass_post"] for x in c["deltas"]) / max(len(c["deltas"]), 1)),
        "post_warnings": g("post", lambda c: sum(1 for x in c["checks"] if x["sev"] == "warn")),
        "area_mm2": g("post", lambda c: c["metrics"]["area_mm2"]),
        "vias": g("post", lambda c: c["metrics"]["vias"]),
        "cost_usd": round(cost, 4), "unpriced_lines": len(missing),
        "parts": len([c for c in d.components.values() if not c.get("virtual")]),
        "req_total": g("trace", lambda c: c["total"]), "req_unverified": g("trace", lambda c: c["unverified"]),
        "req_failing": g("trace", lambda c: c["failing"]),
        "req_evidence_missing": g("trace", lambda c: len(c.get("evidence_missing", []))),
        "req_evidence_only": g("trace", lambda c: len(c.get("evidence_only", []))),
    }
