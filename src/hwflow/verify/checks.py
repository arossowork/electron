"""`hwflow check`: the static design checks in the order the report lists them. The netlist and library checks are
core (`design.netlist_findings`), the layout ones are `layout.checks`, the pre-layout proofs `layout.feasibility`, the
budgets and the SPICE element check are sim; `reviewchecks` holds the late-review items (docs/review-early.md)."""

from __future__ import annotations

from ..core import blocks
from ..core import pinchecks
from ..core.design import Design, decoupling_findings, netlist_findings
from ..layout import checks as layout_checks
from ..layout import feasibility
from ..sim import budgets, simrobust, spice
from . import geomchecks, reviewchecks


def check(design: Design, libs=None) -> list[dict]:
    """Return findings: {sev: error|warn|info, code, msg}."""
    f = netlist_findings(design, libs, component_check=spice.element_mismatch)
    f += layout_checks.findings(design, libs)
    f += [{"sev": "error", "code": "layout-check", "msg": m} for m in geomchecks.validate(design)]  # layout_checks:
    f += budgets.check(design)
    if libs is not None:  # layout feasibility proofs from footprints, pin nets and net widths (feasibility.py)
        f += feasibility.check(design, libs)
    f += decoupling_findings(design)
    f += pinchecks.findings(design, libs)  # datasheet limits against net voltages, pin functions against net roles
    if design.raw.get("blocks"):
        f += blocks.findings(design, libs)
    f += simrobust.check_findings(design)
    f += reviewchecks.findings(design, libs)  # late-review items: mains fuse, cost, files, model source
    return f
