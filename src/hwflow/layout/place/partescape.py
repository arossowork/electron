"""Escape proofs of a single part: can the copper of every pin leave the footprint at all? Footprint pads, pin nets
and net widths only, so it runs before placement (`hwflow check`) and gives the same answer after it.

Every check is a proof of impossibility: a lower bound on the copper a part needs against an upper bound on the room
there is. When in doubt it does not fire. The bounds lean the same way everywhere:

- Obstacles (foreign pads) are taken no larger than their copper: a round-rect or oval is drawn with its rounded
  ends, a custom pad by the circle inscribed in its anchor, shapely buffers are inscribed polygons. More free room.
- The pad a track starts from is taken no smaller than its copper (its bounding box). More free room.
- Clearance is the smallest one in force for the part: the board clearance, the clearance of its nets, and every
  custom DRC rule that lowers it (for this part or with a condition we do not parse). More free room.
- A track centre line must start inside its pad (KiCad joins a track to a pad at its end point) and keep
  width/2 + clearance from every foreign pad: the free room for the centre line of a track of width w is the plane
  minus the foreign pads grown by w/2 + clearance (configuration space). Same-net pads are no obstacle.
- Only the part's own pads are obstacles: neighbouring parts, tracks, the board edge and keep-outs are left out.

Checks:

1. pad_escape: a pad between foreign pads in a row (`pad_escape`, the formula `diagnose.escape` has always used)
   whose net is wider than the board track and that no track of the net's width leaves at all, neither out of the
   part nor to a via between the pads (configuration space above). Every such pad needs a neck. Two or more on one
   part (`ESCAPE_PINS`) is the diagnosis's package verdict, now before any layout. Nets at the board track
   width are left out (when in doubt, do not fire: a signal pin may be entered at the board minimum width).
2. part_escape, inner pads: a pad that not even a track of the board minimum width (`min_track_mm`, else
   routing.NECK_FLOOR) leaves on its layer is walled in (a QFN/BGA inner pad, a thermal pad without a same-net ring
   pin). Its net can only leave through a via in the pocket around it. Via sites: via centres at via_dia/2 +
   clearance from every foreign pad, not inside an SMD pad (via-in-pad is not allowed unless the component declares
   `thermal_vias` for that pad; a through-hole pad of the same number is its own via). Vias of different nets keep
   via_dia + clearance between centres, so discs of that diameter around the centres do not overlap: the number of
   vias in a region V is at most area(V grown by half that) / area(one disc) (packing bound, an upper bound).
   Demand: one via per net per pocket (a lower bound). Demand above the bound in a group of pockets close enough to
   share via room -> finding.
3. part_escape, perimeter: every net that leaves the part crosses the convex hull C of its pads. Copper of a net
   crossing C at width >= w covers at least w of C (a disc of radius w/2 around the crossing point of its centre
   line is copper), two nets keep the clearance between them, so n nets on the top layer need n x (w_min +
   clearance) of perimeter. Nets with a through-hole pad, and up to the via bound of the hull's inside, can cross on
   other layers; the rest must cross on the pad layer. Demand above the perimeter -> finding. The sum over the whole
   perimeter, not per side, since a net may leave around a corner.
"""

from __future__ import annotations

import math
import re

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

ESCAPE_PINS = 2  # pads on one part that need a neck before the package itself is the problem (the diagnosis's verdict)
QS = 8          # buffer segments per quarter circle (inscribed polygons: obstacles come out slightly small)
EPS = 1e-6


# ------------------------------------------------------------------ the pad-row formula (diagnose.escape)

def pad_escape(fp: dict, classes: dict, clearance: float, default_w: float) -> list[dict]:
    """Pads of footprint `fp` whose net class is wider than the widest straight escape. A pad inside a row (foreign
    pads on both sides along the row) leaves across the row: the track is limited by the nearest foreign pad on
    either side, 2 x (distance to that pad's edge along the row - clearance). A pad at the end of a row can leave
    along the row and is not limited. `fp`: {"pads": [{number, net, x, y, w, h, kind}]} with w, h after the pad
    rotation (pcb.read_board pads, or `footprint_pads` below)."""
    pads = [p for p in fp["pads"] if p.get("net") and p.get("kind") != "np_thru_hole"]
    out = []
    for p in pads:
        others = [q for q in pads if q is not p]
        if not others:
            continue
        near = min(others, key=lambda q: math.hypot(q["x"] - p["x"], q["y"] - p["y"]))
        ax = abs(near["x"] - p["x"]) >= abs(near["y"] - p["y"])  # the row runs along x

        def along(q):
            return (q["x"] - p["x"]) if ax else (q["y"] - p["y"])

        row = [q for q in others if (abs(q["y"] - p["y"]) if ax else abs(q["x"] - p["x"])) < (p["h"] if ax else p["w"]) / 2]
        foreign = [q for q in row if q["net"] != p["net"]]
        if not (any(along(q) > 0 for q in foreign) and any(along(q) < 0 for q in foreign)):
            continue
        gap = min(abs(along(q)) - (q["w"] if ax else q["h"]) / 2 for q in foreign)
        w_esc = max(2 * (gap - clearance), 0.0)
        w = float(classes.get(p["net"], {}).get("width", default_w))
        if w > w_esc + 1e-6:
            pitch = min(abs(along(q)) for q in row)
            half = (p["w"] if ax else p["h"]) / 2
            out.append({"pad": p["number"], "net": p["net"], "width": round(w, 3), "escape": round(w_esc, 3),
                        "pitch": round(pitch, 3),
                        "pitch_needed": round(w / 2 + clearance + half, 2)})  # this pad's width, the same clearance
    return out


# ------------------------------------------------------------------ footprint pads with nets and geometry

def _cu(layers) -> list[str]:
    return [str(L) for L in layers if str(L).endswith(".Cu")]


def footprint_pads(c, fpd: dict) -> list[dict]:
    """Copper pads of component `c` from the library footprint `fpd` (libs.footprint), in footprint coordinates:
    number, net (None: no-connect or no pin), x, y, w, h (bounding box after the pad rotation), kind, side ("tht"
    or the copper layer of an SMD pad), `geom` (copper, never larger than the pad) and `big` (never smaller)."""
    pins = c.pins
    out = []
    for p in fpd["pads"]:
        cu = _cu(p["layers"])
        if not cu or p["kind"] == "np_thru_hole":
            continue
        net = pins.get(p["number"]) if p["number"] else None
        if net is not None and net.upper() in ("NC", "~"):
            net = None
        w, h, s = p["w"], p["h"], p["shape"]
        if s == "circle":
            g = Point(0, 0).buffer(w / 2, quad_segs=QS)
        elif s == "oval":
            r = min(w, h) / 2
            g = (LineString([(-(w / 2 - r), 0), (w / 2 - r, 0)]) if w >= h else
                 LineString([(0, -(h / 2 - r)), (0, h / 2 - r)])).buffer(r, quad_segs=QS) if abs(w - h) > EPS \
                else Point(0, 0).buffer(r, quad_segs=QS)
        elif s == "roundrect":
            r = min(float(p.get("rratio", 0.25)), 0.5) * min(w, h)
            g = box(-w / 2 + r, -h / 2 + r, w / 2 - r, h / 2 - r).buffer(r, quad_segs=QS) if r > EPS else \
                box(-w / 2, -h / 2, w / 2, h / 2)
        elif s == "custom":
            g = Point(0, 0).buffer(min(w, h) / 2, quad_segs=QS)  # inside the anchor, whatever its shape
        else:  # rect (trapezoids and chamfers are rare; drawn as their rectangle)
            g = box(-w / 2, -h / 2, w / 2, h / 2)
        big = Polygon(p["hull"]) if s == "custom" and p.get("hull") and len(p["hull"]) >= 3 else box(-w / 2, -h / 2, w / 2, h / 2)
        place = (lambda q: affinity.translate(affinity.rotate(q, -p.get("rot", 0.0), origin=(0, 0)), p["x"], p["y"]))
        g, big = place(g), place(big).envelope
        x0, y0, x1, y1 = big.bounds
        tht = p["kind"] == "thru_hole" or "*.Cu" in cu or "F&B.Cu" in cu
        out.append({"number": p["number"], "net": net, "x": (x0 + x1) / 2, "y": (y0 + y1) / 2, "w": x1 - x0,
                    "h": y1 - y0, "kind": p["kind"], "side": "tht" if tht else cu[0], "geom": g, "big": big})
    return out


def _clearance(design, ref: str, nets) -> float:
    """The smallest clearance in force at part `ref` (a lower bound on what DRC demands between its pads and
    tracks): board, net clearances, and custom rules with a clearance minimum for this part or for a condition
    that is not about one footprint."""
    from ..board import pcb
    b = design.board
    vals = [float(b["clearance_mm"])]
    classes = pcb.net_classes(design)
    vals += [classes[n]["clearance"] for n in nets if n in classes]
    for rule in b.get("drc_rules") or []:
        rule = str(rule)
        m = re.findall(r"constraint\s+clearance\s+\(min\s+([\d.]+)\s*mm", rule)
        if not m:
            continue
        refs = re.findall(r"memberOfFootprint\('([^']+)'\)", rule)
        if refs and ref not in refs and not any(any(ch in r for ch in "*?") for r in refs):
            continue
        vals += [float(x) for x in m]
    return min(vals)


# ------------------------------------------------------------------ configuration space

class _Part:
    """Free room of one footprint for track centre lines and via centres, cached per net and width."""

    def __init__(self, pads: list[dict], clr: float, via_dia: float, via_ok: set[str]):
        self.pads, self.clr, self.vr = pads, clr, via_dia / 2
        self.s = via_dia + clr  # centre distance of two vias of different nets
        self.via_ok = via_ok   # pad numbers that may hold vias (thermal_vias)
        x0, y0, x1, y1 = unary_union([p["big"] for p in pads]).bounds
        m = 2.0 + via_dia + 2 * clr
        self.frame = box(x0 - m, y0 - m, x1 + m, y1 + m)
        self._cache: dict = {}

    def foreign(self, p: dict) -> list:
        """Copper a track of p's net must keep clear of on p's layer: other nets' pads on that layer and through-hole
        pads (for a through-hole pad: the other through-hole pads only, the layer with the fewest obstacles)."""
        net = p["net"]
        return [q["geom"] for q in self.pads if (q["net"] is None or q["net"] != net) and
                (q["side"] == "tht" or (p["side"] != "tht" and q["side"] == p["side"]))]

    def free(self, p: dict, grow: float):
        key = (p["net"], p["side"], round(grow, 4))
        if key not in self._cache:
            obst = self.foreign(p)
            self._cache[key] = self.frame.difference(unary_union(obst).buffer(grow, quad_segs=QS)) if obst else self.frame
        return self._cache[key]

    def comps(self, p: dict, w: float) -> tuple[bool, list]:
        """(the centre line of a track of width w leaves p and reaches outside the part, the bounded components it
        reaches)."""
        F = self.free(p, w / 2 + self.clr)
        out = False
        pockets = []
        for g in getattr(F, "geoms", [F]):
            if g.is_empty or not g.intersects(p["big"]):
                continue
            if g.exterior.distance(self.frame.exterior) < EPS:
                out = True
            else:
                pockets.append(g)
        return out, pockets

    def no_via(self, p: dict):
        """SMD pads on p's layer: a via centre inside one is a via in pad (not allowed, unless declared)."""
        return unary_union([q["geom"] for q in self.pads if q["side"] == p["side"] and q["side"] != "tht"
                            and q["number"] not in self.via_ok])

    def via_room(self, p: dict, region):
        """Via centres in `region` for p's net: via_dia/2 + clearance from foreign pads, not in a pad."""
        if region is None or region.is_empty:
            return region
        V = region.intersection(self.free(p, self.vr + self.clr))
        nv = self.no_via(p)
        return V.difference(nv) if not nv.is_empty else V

    def max_vias(self, V) -> int:
        """Packing bound: vias of different nets with centres in V."""
        if V is None or V.is_empty:
            return 0
        disc = Point(0, 0).buffer(self.s / 2, quad_segs=QS).area
        return int(math.floor(V.buffer(self.s / 2, quad_segs=QS).area / disc + 0.05)) or 1  # a point holds one

    def leaves(self, p: dict, w: float) -> bool:
        """A track of width w leaves pad p: out of the part, or to a via site, or p holds its own via."""
        if p["side"] == "tht" or p["number"] in self.via_ok:
            return True
        out, pockets = self.comps(p, w)
        if out:
            return True
        return any(not self.via_room(p, g).is_empty for g in pockets)

    def largest_via(self, p: dict, pockets: list) -> float:
        """Diameter of the largest via whose centre lies in the pockets outside every SMD pad, clear of foreign pads
        (sampled on a 0.01 mm grid; for the message only)."""
        obst = unary_union(self.foreign(p))
        nv = self.no_via(p)
        best = 0.0
        for g in pockets:
            region = g.difference(nv) if not nv.is_empty else g
            if region.is_empty:
                continue
            x0, y0, x1, y1 = region.bounds
            xs, ys = [a.ravel() for a in np.meshgrid(np.arange(x0, x1 + 0.01, 0.01), np.arange(y0, y1 + 0.01, 0.01))]
            inside = shapely.contains_xy(region, xs, ys)
            if not inside.any():
                continue
            d = shapely.distance(obst, shapely.points(xs[inside], ys[inside]))
            best = max(best, float(d.max()))
        return max(2 * (best - self.clr), 0.0)


# ------------------------------------------------------------------ the checks

def part_findings(design, ref: str, fpd: dict) -> list[dict]:
    """pad_escape and part_escape findings of component `ref` with library footprint `fpd`."""
    from ..board import pcb
    from ..board import routing
    c = design.components[ref]
    pads = footprint_pads(c, fpd)
    netted = [p for p in pads if p["net"]]
    if len(pads) < 3 or not netted:
        return []
    b = design.board
    classes = pcb.net_classes(design)
    track = float(b["track_mm"])
    floor = float(b.get("min_track_mm", routing.NECK_FLOOR))
    via_dia = float(b["via_dia_mm"])
    clr = _clearance(design, ref, {p["net"] for p in netted})
    tv = c.get("thermal_vias") or {}
    via_ok = {str(tv.get("pad"))} if tv else set()
    P = _Part(pads, clr, via_dia, via_ok)
    fp_id = c.get("footprint")
    conns = design.nets()
    out = []

    # 1. pad escape of wide nets, confirmed in configuration space
    esc = pad_escape({"pads": netted}, classes, clr, track)
    hard = []
    for e in esc:
        if e["width"] <= track + 1e-6:
            continue  # a neck to the board minimum at a fine-pitch pin is routine
        mine = [p for p in netted if p["number"] == e["pad"] and p["net"] == e["net"]]
        if mine and not any(P.leaves(p, e["width"]) for p in mine):
            hard.append(e)
    if len(hard) >= ESCAPE_PINS:
        nets = sorted({e["net"] for e in hard})
        listed = ", ".join(f"{e['pad']} ({e['net']} {e['width']} mm)" for e in hard)
        worst = max(hard, key=lambda e: e["width"] - e["escape"])
        out.append({
            "check": "pad_escape", "back_to": "parts", "refs": [ref], "nets": nets,
            "need_mm": worst["width"], "have_mm": worst["escape"],
            "msg": f"{ref} ({fp_id}, pitch {worst['pitch']} mm): pads "
                   f"{listed} sit between foreign pads "
                   f"where at most {worst['escape']} mm leaves a pad ({clr:g} mm clearance) and no track of the net's "
                   f"width reaches out of the part or to a via, so {len(hard)} wide nets would neck at one part. "
                   f"Options: a package with pitch >= {max(e['pitch_needed'] for e in hard)} mm (parts), or narrower "
                   f"{', '.join(nets)} (circuit: width_mm/current_a)."})

    # 2. inner pads: walled in at the minimum width, they need a via in their pocket
    items = {}  # (net, pocket key) -> {net, pads, pocket, V, p}
    for p in netted:
        if p["side"] == "tht" or p["number"] in via_ok:
            continue
        if any(q["number"] == p["number"] and (q["side"] == "tht") for q in pads):
            continue  # a through-hole pad of the same number is its own via
        out_, pockets = P.comps(p, floor)
        if out_:
            continue
        inside = [q for q in netted if q["net"] == p["net"] and any(q["big"].intersects(g) for g in pockets)]
        external = any(r != ref for r, _ in conns.get(p["net"], []))
        if not external and len(inside) == sum(q["net"] == p["net"] for q in netted):
            continue  # every pad of the net is in this pocket: nothing has to leave it
        region = unary_union(pockets) if pockets else Polygon()
        key = (p["net"], tuple(round(v, 3) for v in region.bounds) if not region.is_empty else ("pad", p["number"]))
        it = items.setdefault(key, {"net": p["net"], "pads": [], "pockets": pockets, "p": p,
                                    "V": P.via_room(p, region) if not region.is_empty else Polygon()})
        it["pads"].append(p["number"])
    items = list(items.values())
    group = list(range(len(items)))

    def root(i):
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b_ = items[i]["V"], items[j]["V"]
            if not a.is_empty and not b_.is_empty and a.buffer(P.s / 2).intersects(b_.buffer(P.s / 2)):
                group[root(i)] = root(j)
    clusters: dict[int, list] = {}
    for i, it in enumerate(items):
        clusters.setdefault(root(i), []).append(it)
    short = []  # groups of pockets that share via room and hold fewer vias than they need nets
    for cl in clusters.values():
        nets = sorted({it["net"] for it in cl})
        Vs = [it["V"] for it in cl if not it["V"].is_empty]
        sites = P.max_vias(unary_union(Vs)) if Vs else 0
        if len(nets) > sites:
            short.append((cl, nets, sites))
    if short:  # one finding per part: the groups' needs and rooms add up (each group alone is already short)
        cl = [it for c_, _, _ in short for it in c_]
        nets = sorted({n for _, ns, _ in short for n in ns})
        need = sum(len(ns) for _, ns, _ in short)
        sites = sum(s_ for _, _, s_ in short)
        pads_ = sorted({n for it in cl for n in it["pads"]}, key=lambda x: (len(x), x))
        have = max(P.largest_via(it["p"], it["pockets"]) for it in cl)
        side = cl[0]["p"]["side"]
        out.append({
            "check": "part_escape", "back_to": "parts", "refs": [ref], "nets": nets,
            "need_mm": round(via_dia, 3), "have_mm": round(have, 3),
            "msg": f"{ref} ({fp_id}): pad{'s' if len(pads_) > 1 else ''} {', '.join(pads_)} ({', '.join(nets)}) "
                   f"{'are' if len(pads_) > 1 else 'is'} walled in by foreign pads: not even a {floor:g} mm track leaves "
                   f"on {side} ({clr:g} mm clearance), so each net needs a via between the pads, and there is room for "
                   f"{sites} via{'s' if sites != 1 else ''} where {need} {'are' if need > 1 else 'is'} needed "
                   f"(the largest via that fits is {have:.2f} mm, the board via {via_dia:g} mm; via-in-pad is not "
                   f"allowed). Options: a package with more pitch or fewer inner pins (parts), `thermal_vias` on {ref} "
                   f"for a thermal pad, or a pin assignment that puts these nets on a same-net neighbour."})

    # 3. perimeter: nets that leave the part cross the hull of its pads
    ext = sorted({p["net"] for p in netted if any(r != ref for r, _ in conns.get(p["net"], []))})
    down = {p["net"] for p in netted if p["side"] == "tht" or p["number"] in via_ok}
    top = [n for n in ext if n not in down]
    hull = unary_union([p["geom"] for p in pads]).convex_hull
    perim = hull.length
    if len(top) >= 2 and len(top) * (floor + clr) > perim:
        U = []
        for n in top:
            p = next(q for q in netted if q["net"] == n)
            V = P.via_room(p, hull)
            if V is not None and not V.is_empty:
                U.append(V)
        sites = P.max_vias(unary_union(U)) if U else 0
        k = len(top) - sites
        need = k * (floor + clr)
        if k >= 2 and need > perim + 1e-6:
            out.append({
                "check": "part_escape", "back_to": "parts", "refs": [ref], "nets": top,
                "need_mm": round(need, 3), "have_mm": round(perim, 3),
                "msg": f"{ref} ({fp_id}): {len(top)} nets leave the part on its pad layer and at most {sites} can "
                       f"go down through a via inside it; the other {k} need {k} x ({floor:g} mm track + {clr:g} mm "
                       f"clearance) = {need:.2f} mm of the {perim:.2f} mm around its pads. Options: a larger package "
                       f"or one with fewer pins (parts)."})
    return out


def findings(design, libs, parts=None, board=None) -> list[dict]:
    """Escape findings of every part. Placement does not matter (only a part's own pads are obstacles), so `parts`
    and `board` only narrow the parts looked at to those placed."""
    out = []
    refs = [r for r, c in design.components.items() if not c.get("virtual") and c.get("footprint")]
    if parts is not None:
        refs = [r for r in refs if r in parts]
    elif board is not None:
        refs = [r for r in refs if r in board.get("footprints", {})]
    for ref in refs:
        try:
            fpd = libs.footprint(design.components[ref].get("footprint"))
        except Exception:  # noqa: BLE001  (a missing footprint is reported by design.check)
            continue
        if len(fpd["pads"]) < 3:
            continue
        out += part_findings(design, ref, fpd)
    return out
