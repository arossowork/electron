"""Pre-layout feasibility proofs run by `hwflow check`: checks that need only footprints, pin nets, net widths and the
board settings, and prove that no placement or router can succeed (docs/layout-diagnosis.md). Each registered module
exposes `findings(design, libs, parts=None, board=None) -> list[dict]` with {check, back_to, refs, nets, need_mm,
have_mm, msg}; `hwflow check` reports each finding as an error with the code of its registry entry. To add a check,
add one line to FEASIBILITY_CHECKS."""

from __future__ import annotations

import importlib

# (check code in `hwflow check`, module with findings(design, libs, parts=None, board=None))
FEASIBILITY_CHECKS: list[tuple[str, str]] = [
    ("escape", "hwflow.partescape"),   # pad escape of wide nets, walled-in pads, perimeter of a part
    ("barrier", "hwflow.barrier"),     # nets cut off by keep-outs or isolation, channels narrower than the net
    ("cut", "hwflow.cutcap"),          # nets crossing a line or ring against the free copper along it
    ("corridor", "hwflow.corridor"),   # between_mm, conductive isolation and net layers close a net's corridor
    ("padiso", "hwflow.padiso"),       # pads of a region's nets closer than isolation_mm to a conductive keep-out
]


def findings(design, libs=None, parts=None, board=None) -> list[dict]:
    """The findings of every registered check, each with its `code`."""
    if libs is None:
        from .libs import Libs
        libs = Libs(design=design)
    out = []
    for code, mod in FEASIBILITY_CHECKS:
        for f in importlib.import_module(mod).findings(design, libs, parts=parts, board=board):
            out.append({**f, "code": code})
    return out


def check(design, libs=None) -> list[dict]:
    """design.check findings: an error per finding; a check that crashes is a warning, not a lost check run."""
    if libs is None:
        from .libs import Libs
        libs = Libs(design=design)
    out = []
    for code, mod in FEASIBILITY_CHECKS:
        try:
            fs = importlib.import_module(mod).findings(design, libs)
        except Exception as e:  # noqa: BLE001
            out.append({"sev": "warn", "code": code, "msg": f"feasibility check {mod} failed: {type(e).__name__}: {e}"})
            continue
        out += [{"sev": "error", "code": code, "msg": f["msg"]} for f in fs]
    return out
