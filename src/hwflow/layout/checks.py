"""Layout checks of `hwflow check` that need the floorplan, the placer or the board rules: floorplan validation, cut
pads, isolation between fixed parts, and the copper layers a net may use. The netlist checks are
`hwflow.core.design.netlist_findings`; the whole run is `hwflow.verify.checks.check`."""

from __future__ import annotations

import fnmatch

from ..core.floorplan import cut_pad_findings, cuts, validate
from .board import pcb
from .board.routing import pad_layers
from .place import placer


def findings(design, libs=None) -> list[dict]:
    """Findings {sev, code, msg} of the layout part of the design check."""
    f: list[dict] = []

    def add(sev, code, msg):
        f.append({"sev": sev, "code": code, "msg": msg})

    nets = design.nets()
    for msg in validate(design):
        add("error", "floorplan", msg)
    if libs is not None and cuts(design):  # pads of fixed parts within the copper-to-edge clearance of a cut
        try:
            parts = placer.build_parts(design, libs)
        except Exception:  # noqa: BLE001 - a missing footprint is reported above
            parts = {}
        edge = max(float(design.board["clearance_mm"]), pcb.EDGE_CLEARANCE_MM)
        for msg in cut_pad_findings(design, parts, edge):
            add("warn", "cut-pad", msg)
    # board.isolation between_mm: a positive distance; pads of fixed parts of two member nets closer than it
    between = False
    for i, g in enumerate(design.board.get("isolation") or []):
        v = g.get("between_mm") if isinstance(g, dict) else None
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            add("error", "isolation", f"board.isolation[{i}].between_mm must be a positive number (mm)")
        else:
            between = True
    if libs is not None and between:
        try:
            parts = placer.build_parts(design, libs)
            pl = placer.Placer(design, parts, float(design.board["width_mm"]), float(design.board["height_mm"]))
            for msg in pl.fixed_violations():
                if "between_mm" in msg:
                    add("warn", "isolation", f"fixed parts: {msg}")
        except Exception:  # noqa: BLE001 - a missing footprint is reported above
            pass
    # nets.<name>.layers: known copper layers, and an SMD pad of the net on one of them
    copper = pcb.cu_layers(int(design.board["layers"]))
    allowed = pcb.net_layers(design)
    for net, lays in allowed.items():
        pats = design.net_info(net).get("layers")
        pats = [pats] if isinstance(pats, str) else pats
        if not isinstance(pats, (list, tuple)) or not pats:
            add("error", "net-layers", f"nets.{net}.layers must be a list of copper layer names")
            continue
        for p in pats:
            if not any(fnmatch.fnmatchcase(L, str(p)) for L in copper):
                add("error", "net-layers", f"nets.{net}.layers: {p} is not a copper layer of this "
                                           f"{design.board['layers']}-layer board ({', '.join(copper)})")
        if libs is None or not lays:
            continue
        for ref, pin in nets.get(net, []):
            c = design.components[ref]
            if c.get("virtual"):
                continue
            try:
                pads = [p for p in libs.footprint(c.get("footprint"))["pads"] if p["number"] == pin]
            except Exception:  # noqa: BLE001 - reported as footprint-missing
                continue
            for p in pads:
                cu = pad_layers(p["layers"], copper)
                if p["kind"] == "smd" and cu and not set(cu) & set(lays):
                    add("error", "net-layers", f"{ref}.{pin} ({net}) is an SMD pad on {', '.join(cu)} only, not on "
                                               f"the net's layers {', '.join(lays)}")
                    break
    if libs is not None and allowed:  # a disallow rule area that nets.<name>.layers overrides for a net
        for e in pcb.net_layer_exemptions(design, libs)[1]:
            add("warn", "net-layers", f"board.drc_rules rule {e['rule']} keeps {e['net']} out of rule area {e['area']}, "
                                      f"but nets.{e['net']}.layers allows {', '.join(e['layers'])} there: the net's "
                                      f"layers win (the rule does not apply to {e['net']})")
    return f
