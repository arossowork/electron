"""Pin access analysis in the style of TritonRoute: which directions a track of a net's width can leave each pad in.

For every pad with a net, `pin_access` checks ACCESS_DIRS directions on each copper layer of the pad: a track of
the net's width (and, separately, of the board minimum width) starting at the pad centre must leave the pad's
copper and reach a point ACCESS_STEP_MM beyond its edge with its centre line in the configuration space of that
layer: the board outline inset by w/2, minus every foreign pad and hole within ACCESS_RADIUS_MM grown by the pair's
clearance + w/2 (net classes, isolation, between_mm; ACCESS_RADIUS_MM or the pad's reach + 1 mm), minus the rule
areas forbidding tracks to the net grown by w/2 (the same reading as partescape.footprint_pads / _Part.free,
extended from the part's own pads to the whole board). The free-space model is corridor.Model.

An SMD pad that has no access on its layer at any width is reported (`no_access`): its net can only leave by a via
in the pad. The layout flow (asicflow.necked) sends a net with a pad that has no exit at its width, but at least
one at the board minimum, through krt.krt_route's narrow-first `necked` path.

Nothing here is specific to a design: every input is the board's rules and placement (docs/asic-ideas.md, idea 6)."""
from __future__ import annotations

import math
import time

import shapely
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

ACCESS_DIRS = 8
ACCESS_STEP_MM = 0.3
ACCESS_RADIUS_MM = 4.0


def pin_access(design, m, dirs: int = ACCESS_DIRS) -> dict:
    """(ref, pad) -> {net, points: [(layer, x, y)] at the net's width, floor: number of access points at the board
    minimum width, n: number of points at the net width, no_access, dirs: directions tried}, on corridor.Model `m`."""
    t0 = time.time()
    from ..board import pcb
    from .barrier import HOLE_MM
    classes = pcb.net_classes(design)
    track = float(design.board["track_mm"])
    floor = float(m.floor)
    tree = STRtree([p["obst"] for p in m.pads])
    holes = list(m.holes)
    out = {}
    angles = [2 * math.pi * k / dirs for k in range(dirs)]
    for p in m.pads:
        net = p["net"]
        if not net:
            continue
        w = float(classes.get(net, {}).get("width", track))
        own = p["own"]
        c = own.centroid
        minx, miny, maxx, maxy = own.bounds
        reach = math.hypot(maxx - minx, maxy - miny) / 2 + ACCESS_STEP_MM + w
        win = Point(c.x, c.y).buffer(max(ACCESS_RADIUS_MM, reach + 1.0))
        near = [int(i) for i in tree.query(win)]
        pts, nfloor = [], 0
        tht = len(p["layers"]) > 1
        for L in p["layers"]:
            ko = [g for g in m.keepouts(net, L) if g.intersects(win)]
            res = {}
            for ww in (w, floor) if floor < w - 1e-9 else (w,):
                blk = []
                for i in near:
                    q = m.pads[i]
                    if q["net"] == net or L not in q["layers"]:
                        continue
                    blk.append(q["obst"].buffer(m.clearance(net, q["net"], q["ref"]) + ww / 2, quad_segs=4))
                blk += [h.buffer(HOLE_MM + ww / 2, quad_segs=4) for h in holes if h.intersects(win)]
                blk += [g.buffer(ww / 2, quad_segs=4) for g in ko]
                room = m.outline.buffer(-ww / 2).intersection(win)
                C = room.difference(unary_union(blk)) if blk else room
                shapely.prepare(C)
                okd = []
                for a in angles:
                    ex, ey = c.x + reach * math.cos(a), c.y + reach * math.sin(a)
                    seg = LineString([(c.x, c.y), (ex, ey)]).difference(own)
                    if seg.is_empty or C.covers(seg):
                        okd.append((L, ex, ey))
                res[ww] = okd
            pts += res[w]
            nfloor += len(res.get(floor, res[w]))
        out[(p["ref"], p["number"])] = {"net": net, "points": pts, "n": len(pts), "floor": nfloor,
                                        "no_access": nfloor == 0 and not tht, "dirs": dirs * len(p["layers"])}
    pin_access.last_s = round(time.time() - t0, 2)
    return out


pin_access.last_s = 0.0


def summary(acc: dict, top: int = 20) -> list[dict]:
    """Pads ordered by fewest access points at the net width (a fraction of the directions tried)."""
    rows = [dict(ref=k[0], pad=k[1], **{x: v[x] for x in ("net", "n", "floor", "no_access", "dirs")})
            for k, v in acc.items()]
    rows.sort(key=lambda r: (r["n"] / max(r["dirs"], 1), r["floor"]))
    return rows[:top]
