"""Annular-ring repair: plated through-hole pads whose copper ring is under the board's minimum annular width.

KiCad's `annular_width` test compares the copper ring around a plated hole (pad or via footprint) with the board
setup's minimum (`min_ring`, the `min_via_annular_width` that pcb.write_project writes). A footprint drawn with a
thinner ring (a module's 3.1 mm pad on a 3.0 mm mounting hole, a 0.84 mm "via" footprint on a 0.7 mm drill, a
1.2 mm oval on a 1.016 mm drill) fails that test wherever it is placed. The fab minimum holds for every part hwflow
writes, so `plan` grows such a pad's copper before placement: the drill and the pad shape stay, the copper grows
symmetrically until the ring is `min_ring + RING_MARGIN` on both axes (the drill offset counted).

A pad grows only when the grown copper keeps:
- the net-class clearance (the larger of the two nets' classes, the board clearance for a pad without a net) to the
  other pads of its footprint, and the hole clearance to the footprint's non-plated holes;
- for a part at a given position (`placement: {x, y}`, which the placer does not move): the copper-to-edge distance
  to the board outline, the clearance to the pads of the other parts at given positions, and the rule areas that
  forbid pads (board.keepouts, region copper keep-outs).
The placer and the router see a moved part's grown copper (placer.build_parts) and keep their own clearances to it.
A pad that cannot grow is left as it is and listed (`left`, with the reason). A pad without a net whose copper is no
larger than its drill is exempt by pcb.ringless_hole_rules and is not touched, nor is a hole inside a larger pad
of the same number (a mounting hole's ring of stitching holes: KiCad measures their ring to the large pad);
custom-shaped pads are listed, not grown.

The grown pads are written into the board (pcb.build_board), the DSN export and the placer's footprint model, and
listed in status.json (`layout.pad_ring_repair`) and REPORT.md: they are a footprint change the product person
should see. No LLM, nothing specific to a design."""
from __future__ import annotations

import math
import re

from shapely.geometry import Point

from ...core import sexpr

RING_MARGIN = 0.01  # mm over the minimum ring: KiCad's comparison is exact, a ring at the minimum may round below it
_CACHE = "_pad_ring_plan"


def min_ring(design) -> float:
    """The board's minimum annular width, as pcb.write_project writes it (min_via_annular_width)."""
    b = design.board
    return min(0.1, round((float(b["via_dia_mm"]) - float(b["via_drill_mm"])) / 2, 4))


def _drill(p: list) -> tuple[float, float, float, float] | None:
    """(drill width, drill height, offset x, offset y) of a pad node, in the pad frame; None without a drill."""
    d = sexpr.find(p, "drill")
    if not d:
        return None
    nums = [float(x) for x in d[1:] if not isinstance(x, list) and re.match(r"^[\d.]+$", str(x))]
    if not nums:
        return None
    dw, dh = (nums[0], nums[1]) if len(nums) > 1 else (nums[0], nums[0])
    o = sexpr.find(d, "offset")
    ox, oy = (float(o[1]), float(o[2])) if o and len(o) >= 3 else (0.0, 0.0)
    return dw, dh, ox, oy


def ring(w: float, h: float, drill: tuple) -> float:
    """The copper ring around the hole: the smaller of the two axes, less the drill offset."""
    dw, dh, ox, oy = drill
    return min((w - dw) / 2 - abs(ox), (h - dh) / 2 - abs(oy))


def grown_size(shape: str, w: float, h: float, drill: tuple, need: float) -> tuple[float, float]:
    """The pad size whose ring is `need` on both axes (never smaller than it is); a circle stays a circle."""
    dw, dh, ox, oy = drill
    nw = max(w, dw + 2 * (need + abs(ox)))
    nh = max(h, dh + 2 * (need + abs(oy)))
    if shape == "circle":
        nw = nh = max(nw, nh)
    return _up(nw), _up(nh)


def _up(v: float) -> float:
    """`v` rounded up to 0.1 um (a float sum such as 3 + 0.22 lands just under it): the ring stays at `need`."""
    return math.ceil(round(v * 1e4, 6)) / 1e4


def _shape(p: dict, w: float, h: float, x: float, y: float, rot: float, rr: float):
    """The copper of a footprint pad (footprint frame, or board frame with x, y, rot given there), as pcb.pad_polygon
    draws it."""
    from .pcb import pad_polygon
    a = rot % 360
    if abs(a / 90 - round(a / 90)) < 1e-9:
        if round(a / 90) % 2:
            w, h = h, w
        a = 0.0
    return pad_polygon({"x": x, "y": y, "w": w, "h": h, "shape": p["shape"], "angle": a, "rratio": rr})


def _pads(tree: list) -> list[dict]:
    """The pads of a footprint tree, in file order (the order libs.footprint and pcb._place_footprint use)."""
    out = []
    for i, p in enumerate(sexpr.find_all(tree, "pad")):
        at, size = sexpr.find(p, "at"), sexpr.find(p, "size")
        if not at or not size:
            continue
        d = _drill(p)
        rot = sexpr.at_angle(at)
        x, y = float(at[1]), float(at[2])
        if d and (d[2] or d[3]):  # the copper sits at the drill offset, turned with the pad
            c, s = math.cos(math.radians(-rot)), math.sin(math.radians(-rot))
            x, y = x + d[2] * c - d[3] * s, y + d[2] * s + d[3] * c
        out.append({"idx": i, "number": str(p[1]), "kind": str(p[2]), "shape": str(p[3]), "x": x, "y": y,
                    "rot": rot, "w": float(size[1]), "h": float(size[2]), "drill": d,
                    "hx": float(at[1]), "hy": float(at[2]),
                    "rr": float(sexpr.value(p, "roundrect_rratio", 0.25)),
                    "layers": [str(v) for v in (sexpr.find(p, "layers") or [])[1:]]})
    return out


def _to_board(c, x: float, y: float) -> tuple[float, float]:
    from ...core.geometry import rot
    pl = c.get("placement") or {}
    dx, dy = rot(x, y, float(pl.get("rot", 0)))
    return float(pl["x"]) + dx, float(pl["y"]) + dy


def plan(design, libs) -> dict:
    """{grown: {ref: {pad index: (w, h)}}, rows: [{ref, pad, shape, old, new, drill, ring_old, ring_new}],
    left: [{ref, pad, ring, why}], min_ring}. Computed once per design object."""
    got = design.__dict__.get(_CACHE)
    if got is not None:
        return got
    from . import keepout, pcb
    from ...core import floorplan
    b = design.board
    need = min_ring(design) + RING_MARGIN
    classes = pcb.net_classes(design)
    nets = set(design.nets())
    base = float(b["clearance_mm"])
    hole_cl = min(0.25, base)

    def clear(n1, n2) -> float:
        cl = [base] + [float(classes.get(n, {}).get("clearance", base)) for n in (n1, n2) if n]
        return max(cl)
    fixed, trees = {}, {}
    for ref, c in design.components.items():
        if c.get("virtual") or not c.get("footprint"):
            continue
        try:
            trees[ref] = libs.footprint_tree(c.get("footprint"))
        except Exception:  # noqa: BLE001 - a missing footprint is reported by check, not here
            continue
        pl = c.get("placement") or {}
        if "x" in pl and "y" in pl:
            fixed[ref] = True
    edge = None
    areas = []
    if fixed:
        edge = floorplan.outline(design).buffer(-pcb.edge_mm(design), join_style="mitre")
        zt = ["kicad_pcb"] + pcb.board_keepout_zones(design) + pcb.region_keepout_zones(design)
        areas = [a["poly"] for a in keepout.rule_areas(zt, ("pads",)) if "pads" in a["forbids"]]
    pads = {ref: _pads(t) for ref, t in trees.items()}
    out = {"grown": {}, "rows": [], "left": [], "min_ring": min_ring(design)}
    board_cu: list = []  # (ref, net, polygon) of the pads of the parts at given positions, board frame
    for ref in fixed:
        c = design.components[ref]
        for p in pads[ref]:
            if any(L.endswith(".Cu") for L in p["layers"]):
                x, y = _to_board(c, p["x"], p["y"])
                rot = p["rot"] + float((c.get("placement") or {}).get("rot", 0))
                board_cu.append((ref, p["idx"], c.pins.get(p["number"]),
                                 _shape(p, p["w"], p["h"], x, y, rot, p["rr"])))
    for ref in sorted(pads):
        c = design.components[ref]
        mine = pads[ref]
        for p in mine:
            if p["kind"] != "thru_hole" or not p["drill"]:
                continue
            r0 = ring(p["w"], p["h"], p["drill"])
            if r0 >= min_ring(design) - 1e-9:
                continue
            net = c.pins.get(p["number"])
            net = net if net in nets else None
            if net is None and min(p["w"], p["h"]) <= p["drill"][0] + 1e-6:
                continue  # ringless by construction: pcb.ringless_hole_rules exempts it
            hole = Point(p["hx"], p["hy"]).buffer(max(p["drill"][0], p["drill"][1]) / 2 + min_ring(design))
            if any(q is not p and p["number"] and q["number"] == p["number"]
                   and _shape(q, q["w"], q["h"], q["x"], q["y"], q["rot"], q["rr"]).contains(hole) for q in mine):
                continue  # the hole sits in a larger pad of the same number: KiCad measures the ring to that copper
            row = {"ref": ref, "pad": p["number"], "ring": round(r0, 4)}
            if p["shape"] not in ("circle", "oval", "rect", "roundrect"):
                out["left"].append(dict(row, why=f"{p['shape']} pad: not grown"))
                continue
            w, h = grown_size(p["shape"], p["w"], p["h"], p["drill"], need)
            g = _shape(p, w, h, p["x"], p["y"], p["rot"], p["rr"])
            why = None
            for q in mine:
                if q is p or not any(L.endswith(".Cu") for L in q["layers"]):
                    continue
                qn = c.pins.get(q["number"])
                qn = qn if qn in nets else None
                if (net is not None and qn == net) or (p["number"] and q["number"] == p["number"]):
                    continue  # the same net, or the same pad number (one connection: KiCad gives both one net)
                qw, qh = out["grown"].get(ref, {}).get(q["idx"], (q["w"], q["h"]))
                gap = g.distance(_shape(q, qw, qh, q["x"], q["y"], q["rot"], q["rr"]))
                if q["kind"] == "np_thru_hole":
                    if gap < hole_cl - 1e-6:
                        why = f"{hole_cl:g} mm to the hole {q['number'] or '(unnumbered)'} of its footprint"
                        break
                elif gap < clear(net, qn) - 1e-6:
                    why = f"{clear(net, qn):g} mm clearance to pad {q['number'] or '(unnumbered)'} of its footprint"
                    break
            if why is None and ref in fixed:
                x, y = _to_board(c, p["x"], p["y"])
                rot = p["rot"] + float((c.get("placement") or {}).get("rot", 0))
                gb = _shape(p, w, h, x, y, rot, p["rr"])
                if edge is not None and not edge.contains(gb):
                    why = f"{pcb.edge_mm(design):g} mm copper to the board edge"
                elif any(a.intersects(gb) for a in areas):
                    why = "a rule area that forbids pads"
                else:
                    for r2, _, n2, poly in board_cu:
                        if r2 == ref or (net is not None and n2 == net):
                            continue
                        if gb.distance(poly) < clear(net, n2 if n2 in nets else None) - 1e-6:
                            why = f"{clear(net, n2):g} mm clearance to {r2}"
                            break
            if why is not None:
                out["left"].append(dict(row, why=why))
                continue
            out["grown"].setdefault(ref, {})[p["idx"]] = (w, h)
            out["rows"].append({"ref": ref, "pad": p["number"], "shape": p["shape"], "old": [p["w"], p["h"]],
                                "new": [w, h], "drill": round(p["drill"][0], 4), "ring_old": round(r0, 4),
                                "ring_new": round(ring(w, h, p["drill"]), 4)})
    design.__dict__[_CACHE] = out
    return out


def apply_pads(design, libs, ref: str, pads: list[dict]) -> list[dict]:
    """libs.footprint pads of `ref` with the grown sizes of `plan` (copies; the rest as they are)."""
    g = plan(design, libs)["grown"].get(ref)
    if not g:
        return pads
    return [dict(p, w=g[i][0], h=g[i][1]) if i in g else p for i, p in enumerate(pads)]


def summary(rep: dict) -> list[str]:
    """REPORT.md lines: one per grown pad, one per pad left under the minimum."""
    lines = []
    for r in rep.get("rows", []):
        lines.append(f"{r['ref']} pad {r['pad'] or '(unnumbered)'}: {r['old'][0]:g} x {r['old'][1]:g} -> "
                     f"{r['new'][0]:g} x {r['new'][1]:g} mm on a {r['drill']:g} mm drill (ring {r['ring_old']:g} -> "
                     f"{r['ring_new']:g} mm, minimum {rep['min_ring']:g} mm)")
    for r in rep.get("left", []):
        lines.append(f"{r['ref']} pad {r['pad'] or '(unnumbered)'}: ring {r['ring']:g} mm under the minimum "
                     f"{rep['min_ring']:g} mm, not grown ({r['why']})")
    return lines
