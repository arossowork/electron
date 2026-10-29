"""Seed route: copper for a net the corridor proof connects but the router leaves open (krt.krt_route, after the
neck-down fallback).

The router keeps routing.ISO_SLACK more than the rule from every isolation keep-out and moves on a 0.1 mm grid, so a
corridor the proof finds a little over the floor width can stay closed to it. The seed route works in the proof's
own free space instead (corridor.Model: clearances, isolation, between_mm, conductive keep-outs, net layers), with
the exact spacings plus MARGIN_MM, and adds the copper of the board's other nets (tracks and vias) as obstacles:

1. room: per allowed layer, the centre-line room of a floor-width track (routing.neck_floor) and of a full-width one
   (the net's class width): the copper area minus every obstacle grown by its spacing, the track half width and
   MARGIN_MM (+ CHORD_MM for the polygon chords of the buffers). Obstacle pads count with their full outline (a
   custom pad with its hull), so the room is never larger than the copper rules allow. A via site is where the via
   disc keeps the same spacings on every layer of the net.
2. path: per edge of the minimum spanning tree of the net's pads, the cheapest raster path (GRID_MM, 8 neighbours,
   scipy Dijkstra) through the floor-width room from a cell inside one pad to a cell inside the other. A millimetre
   where only a narrow track fits costs NARROW_COST, so the narrow stretch is as short as the geometry permits; a via
   costs VIA_COST_MM. The path is then pulled straight (any-angle segments inside the floor-width room, never more
   narrow length than the raster path they replace).
3. widths: every piece (at most PIECE_MM) gets the widest of {class width, class width - STEP_MM, ..., floor} its
   exact distances to the obstacles allow (with MARGIN_MM). Accepted when every piece keeps all spacings exactly
   (checked again on the merged segments) and no continuous narrow stretch is longer than routing.NECK_MAX_MM.

The copper goes into the board as tracks of the net; later passes route around it, and the final KiCad DRC judges
it: a DRC error that touches seed copper removes it again (drop_on_drc). Not a design field: the flow does it for
every constrained net (corridor.constrained) left open, HWFLOW_SEEDROUTE=0 switches it off.

Interface: seed(design, libs, src, dst, nets) -> {net: {ok, msg, ...}}; record/load the seed copper of a run
(RECORD next to the routed board); drop_on_drc(seeds, tracks, vias, drc).
"""

from __future__ import annotations

import json
import math
import uuid
from pathlib import Path

import numpy as np
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree

from ..place import barrier, corridor
from ..board import routing
from ...core import sexpr
from ...core.sexpr import Q

MARGIN_MM = 0.01       # every piece of seed copper keeps its exact spacings plus this
CHORD_MM = 0.005       # the room keeps this more, so a buffer chord never takes a centre line under MARGIN_MM
QS = 32                # quad segments of the buffers (chord 0.03 % of the radius: 0.002 mm at 6 mm)
GRID_MM = 0.05         # raster of the path search
MAX_CELLS = 1_500_000  # cells per layer; a larger window gets a coarser raster
RASTER_TOL = 0.005     # a raster cell counts as free this far inside the room (a diagonal step stays in it)
NARROW_COST = 50.0     # cost of a millimetre where only a narrow track fits, against 1 where the full width fits
VIA_COST_MM = 2.0
WINDOW_MM = 8.0        # first search window: the pads' bounding box grown by this (then the whole board)
STEP_MM = 0.05         # width steps between the class width and the floor
PIECE_MM = 0.1         # widths are fitted per piece of at most this length
RECORD = "seed_routes.json"
EPS = 1e-6


def _line(a, b):
    return LineString([a, b]) if a != b else Point(a)


# ------------------------------------------------------------------ obstacles and room

def _obstacles(model, net, layer, board, placed, what="tracks") -> list[tuple]:
    """[(geometry, spacing, label)] that `net` copper on `layer` keeps its spacing from: keep-out areas (and the
    conductive keep-outs of corridor.Model), other nets' pads (full outline), holes, the board's tracks and vias of
    other nets, and seed copper placed before (`placed`: [(net, layer or None, geometry)])."""
    out = [(g, 0.0, "keep-out") for g in model.keepouts(net, layer, what)]
    for p in model.pads:
        if p["net"] != net and layer in p["layers"]:
            out.append((p["own"], model.clearance(net, p["net"], p["ref"]), f"{p['ref']}.{p['number']}"))
    out += [(h, barrier.HOLE_MM, "hole") for h in model.holes]
    for s in board["segments"]:
        if s["net"] != net and s["layer"] == layer:
            g = _line((s["x1"], s["y1"]), (s["x2"], s["y2"])).buffer(s["w"] / 2, quad_segs=QS)
            out.append((g, model.clearance(net, s["net"]), f"track {s['net']}"))
    for v in board["vias"]:
        if v["net"] != net:
            out.append((Point(v["x"], v["y"]).buffer(model.via_r, quad_segs=QS), model.clearance(net, v["net"]),
                        f"via {v['net']}"))
    out += [(g, model.clearance(net, n), f"seed {n}") for n, L, g in placed if n != net and L in (layer, None)]
    return out


def _room(model, obst, win, grow):
    """Where a centre line may lie inside window `win`: the copper area shrunk by `grow`, minus every obstacle grown
    by its spacing + `grow`."""
    area = model.outline.intersection(win).buffer(-grow, quad_segs=QS)
    reach = win.buffer(max([s for _, s, _ in obst] + [0.0]) + grow)
    cut = [g.buffer(s + grow, quad_segs=QS) for g, s, _ in obst if g.intersects(reach)]
    return area.difference(unary_union(cut)) if cut else area


class _Fit:
    """Exact distances from a centre line to the obstacles of one layer."""

    def __init__(self, model, obst):
        self.obst = obst
        self.tree = STRtree([g for g, _, _ in obst]) if obst else None
        self.smax = max([s for _, s, _ in obst] + [0.0])
        self.outline = model.outline
        self.edge = model.outline.boundary

    def slack(self, g, hw):
        """min over obstacles of (distance - spacing - hw) for geometry `g` (a centre line or a via centre), and the
        label of the closest; -inf outside the copper area."""
        if not self.outline.contains(g):
            return -math.inf, "board edge"
        best, who = self.edge.distance(g) - hw, "board edge"
        if self.tree is not None:
            for k in self.tree.query(g.buffer(hw + self.smax + 0.5)):
                o, s, lab = self.obst[int(k)]
                d = o.distance(g) - s - hw
                if d < best:
                    best, who = d, lab
        return best, who


# ------------------------------------------------------------------ path search

def _window(model, nodes, whole):
    if whole:
        return model.outline.envelope
    x0, y0, x1, y1 = unary_union([n["own"] for n in nodes]).bounds
    return box(x0 - WINDOW_MM, y0 - WINDOW_MM, x1 + WINDOW_MM, y1 + WINDOW_MM).intersection(model.outline.envelope)


def _search(lays, rooms, fulls, vroom, win, a, b):
    """Cheapest raster path from pad node `a` to pad node `b`: [(layer, x, y)], or (None, h, reason)."""
    x0, y0, x1, y1 = win.bounds
    h = max(GRID_MM, math.sqrt((x1 - x0) * (y1 - y0) / MAX_CELLS))
    xs, ys = np.arange(x0, x1 + EPS, h), np.arange(y0, y1 + EPS, h)
    X, Y = np.meshgrid(xs, ys)
    ny, nx = X.shape
    nl = len(lays)
    free = np.stack([shapely.contains_xy(rooms[L].buffer(-RASTER_TOL), X, Y) for L in lays])
    full = np.stack([shapely.contains_xy(fulls[L], X, Y) for L in lays])
    cost = np.where(full, 1.0, NARROW_COST)
    idx = np.arange(nl * ny * nx).reshape(nl, ny, nx)
    rows, cols, wts = [], [], []
    for k in range(nl):
        for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
            j0, j1, i0, i1 = 0, ny - dy, max(0, -dx), nx - max(0, dx)
            A, B = (slice(j0, j1), slice(i0, i1)), (slice(j0 + dy, j1 + dy), slice(i0 + dx, i1 + dx))
            ok = free[k][A] & free[k][B]
            rows.append(idx[k][A][ok])
            cols.append(idx[k][B][ok])
            wts.append(h * math.hypot(dx, dy) * (cost[k][A][ok] + cost[k][B][ok]) / 2)
    if nl > 1 and vroom is not None and not vroom.is_empty:
        site = shapely.contains_xy(vroom, X, Y) & free.all(axis=0)
        for k in range(nl):
            for m in range(k + 1, nl):
                rows.append(idx[k][site])
                cols.append(idx[m][site])
                wts.append(np.full(int(site.sum()), VIA_COST_MM))
    G = coo_matrix((np.concatenate(wts), (np.concatenate(rows), np.concatenate(cols))),
                   shape=(nl * ny * nx,) * 2).tocsr()

    def cells(n):
        out = []
        for k, L in enumerate(lays):
            if L in n["layers"]:
                m = shapely.contains_xy(n["own"], X, Y) & free[k]
                out += list(idx[k][m])
        return np.array(out, dtype=int)
    src, dst = cells(a), cells(b)
    for n, c in ((a, src), (b, dst)):
        if not len(c):
            return None, h, f"pad {n['ref']}.{n['number']} has no room for a track end"
    dist, pred, _ = dijkstra(G, directed=False, indices=src, min_only=True, return_predecessors=True)
    end = dst[np.argmin(dist[dst])]
    if not np.isfinite(dist[end]):
        return None, h, None
    path = [int(end)]
    while pred[path[-1]] >= 0:
        path.append(int(pred[path[-1]]))
    out = []
    for c in reversed(path):
        k, r = divmod(c, ny * nx)
        j, i = divmod(r, nx)
        out.append((lays[k], float(xs[i]), float(ys[j]), bool(full[k, j, i])))
    return out, h, None


def _pull(pts, room, wide, h):
    """The raster points [(x, y, full)] of one layer as few straight segments: from each point the farthest later point
    a straight centre line reaches inside `room`, with no more narrow length (outside `wide`) than the raster path it
    replaces. Collinear runs are merged first."""
    keep = [0]
    for k in range(1, len(pts) - 1):
        (ax, ay), (bx, by), (cx, cy) = pts[keep[-1]][:2], pts[k][:2], pts[k + 1][:2]
        if abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) > EPS or pts[k][2] != pts[k + 1][2]:
            keep.append(k)
    keep.append(len(pts) - 1)
    cum = [0.0]  # narrow length of the raster path up to each raster point
    for p, q in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.dist(p[:2], q[:2]) * ((not p[2]) + (not q[2])) / 2)
    pr = prep(room)
    out, i = [keep[0]], 0
    while i < len(keep) - 1:
        j = i + 1
        for m in range(len(keep) - 1, i + 1, -1):
            seg = LineString([pts[keep[i]][:2], pts[keep[m]][:2]])
            if pr.contains(seg) and seg.difference(wide).length <= cum[keep[m]] - cum[keep[i]] + 2 * h:
                j = m
                break
        out.append(keep[j])
        i = j
    return [pts[k][:2] for k in out]


# ------------------------------------------------------------------ one net

def _mst(nodes) -> list[tuple[int, int]]:
    """Edges of the minimum spanning tree of the pads (centre distance, Prim)."""
    c = [n["own"].centroid for n in nodes]
    done, edges = {0}, []
    while len(done) < len(nodes):
        a, b = min(((i, j) for i in done for j in range(len(nodes)) if j not in done), key=lambda e: c[e[0]].distance(c[e[1]]))
        edges.append((a, b))
        done.add(b)
    return edges


def _widths(w, floor) -> list[float]:
    out, k = [w], 1
    while w - k * STEP_MM > floor + EPS:
        out.append(round(w - k * STEP_MM, 4))
        k += 1
    return out + ([floor] if floor < w - EPS else [])


def _pieces(a, b):
    n = max(1, math.ceil(math.dist(a, b) / PIECE_MM))
    pts = [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1)]
    return list(zip(pts, pts[1:]))


def route_net(model, design, net, board, placed, floor) -> dict:
    w = model.width(net)
    nodes = corridor.corridor_nodes(model, design, net, {})
    if nodes is None or len(nodes) < 2:
        return {"ok": False, "msg": f"seed route {net}: not possible (fewer than two pads on its layers)"}
    if not corridor.corridor_connects(model, net, nodes, w, w > model.net_floor(net) + EPS):
        return {"ok": False, "msg": f"seed route {net}: not possible (the corridor proof does not connect it at {w:g} mm)"}
    lays = model.net_copper(net)
    obst = {L: _obstacles(model, net, L, board, placed) for L in lays}
    fit = {L: _Fit(model, obst[L]) for L in lays}
    vfit = {L: _Fit(model, obst[L] + [(g, 0.0, "via keep-out") for g in model.keepouts(net, L, "vias")]) for L in lays}
    cands = _widths(w, floor)
    segs, vias = [], []
    for ea, eb in _mst(nodes):
        a, b = nodes[ea], nodes[eb]
        lab = f"{a['ref']}.{a['number']} - {b['ref']}.{b['number']}"
        path = None
        for whole in (False, True):
            win = _window(model, nodes, whole)
            grow = MARGIN_MM + CHORD_MM
            rooms = {L: _room(model, obst[L], win, floor / 2 + grow) for L in lays}
            fulls = {L: _room(model, obst[L], win, w / 2 + grow) for L in lays}
            vroom = None
            if len(lays) > 1:
                vs = [_room(model, vfit[L].obst, win, model.via_r + grow) for L in lays]
                vroom = vs[0]
                for v in vs[1:]:
                    vroom = vroom.intersection(v)
            path, h, why = _search(lays, rooms, fulls, vroom, win, a, b)
            if path is not None or why:
                break
        if path is None:
            return {"ok": False, "msg": f"seed route {net}: not possible ({why or f'no {floor:g} mm path between {lab} in the free space, raster {h:.2f} mm'})"}
        runs, cur = [], [path[0]]
        for p in path[1:]:
            if p[0] != cur[-1][0]:
                runs.append(cur)
                vias.append((p[1], p[2]))
                cur = [p]
            else:
                cur.append(p)
        runs.append(cur)
        for k, run in enumerate(runs):
            L = run[0][0]
            pts = [(x, y, f) for _, x, y, f in run]
            # end at the pad centre when the room reaches it
            ca, cb = a["own"].centroid, b["own"].centroid
            if k == 0 and L in a["layers"] and rooms[L].contains(_line((ca.x, ca.y), pts[0][:2])):
                pts.insert(0, (ca.x, ca.y, fulls[L].contains(ca)))
            if k == len(runs) - 1 and L in b["layers"] and rooms[L].contains(_line((cb.x, cb.y), pts[-1][:2])):
                pts.append((cb.x, cb.y, fulls[L].contains(cb)))
            line = _pull(pts, rooms[L], fulls[L], h) if len(pts) > 1 else [pts[0][:2]] * 2
            for p, q in zip(line, line[1:]):
                first = True  # pieces merge only along one straight segment
                for pa, pb in _pieces(p, q):
                    s, who = fit[L].slack(_line(pa, pb), MARGIN_MM)
                    c = next((c for c in cands if c / 2 <= s + EPS), None)
                    if c is None:
                        return {"ok": False, "msg": f"seed route {net}: not possible (at ({pa[0]:.2f}, {pa[1]:.2f}) on {L} "
                                                    f"the room next to {who} fits {max(2 * s, 0):.3f} mm, under the {floor:g} mm floor)"}
                    if not first and segs[-1][2] == c:
                        segs[-1][1] = pb
                    else:
                        segs.append([pa, pb, c, L])
                    first = False
    # the merged copper, checked again with the exact spacings (no margin)
    for pa, pb, c, L in segs:
        s, who = fit[L].slack(_line(pa, pb), c / 2)
        if s < -EPS:
            return {"ok": False, "msg": f"seed route {net}: not possible (a {c:g} mm track at ({pa[0]:.2f}, {pa[1]:.2f}) "
                                        f"comes {-s:.3f} mm under its spacing to {who})"}
    for x, y in vias:
        for L in lays:
            s, who = vfit[L].slack(Point(x, y), model.via_r)
            if s < -EPS:
                return {"ok": False, "msg": f"seed route {net}: not possible (a via at ({x:.2f}, {y:.2f}) comes "
                                            f"{-s:.3f} mm under its spacing to {who} on {L})"}
    length = sum(math.dist(p, q) for p, q, _, _ in segs)
    thin = [(tuple(p), tuple(q), L) for p, q, c, L in segs if c < w - EPS]
    narrow = sum(math.dist(p, q) for p, q, _ in thin)
    neck = routing.longest_neck(thin)
    mn = min(c for _, _, c, _ in segs)
    at = next(((p, L) for p, _, c, L in segs if c == mn), None)
    shape = f"width {w:g} mm" + (f" ({mn:g} mm at the narrowest, ({at[0][0]:.2f}, {at[0][1]:.2f}) on {at[1]})" if mn < w - EPS else "")
    stats = {"length": round(length, 2), "width": w, "min_width": mn, "neck": round(neck, 2), "narrow": round(narrow, 2),
             "segments": [[round(p[0], 4), round(p[1], 4), round(q[0], 4), round(q[1], 4), c, L] for p, q, c, L in segs],
             "vias": [[round(x, 4), round(y, 4)] for x, y in vias]}
    if neck > routing.NECK_MAX_MM + EPS:
        return dict(stats, ok=False, msg=f"seed route {net}: not possible (longest neck {neck:.2f} mm, over the "
                                         f"{routing.NECK_MAX_MM:g} mm neck; {narrow:.2f} of {length:.2f} mm narrower "
                                         f"than {w:g} mm, {shape})")
    return dict(stats, ok=True, msg=f"seed route {net}: {length:.1f} mm, {shape}, longest neck {neck:.2f} mm"
                                    + (f", {len(vias)} via(s)" if vias else ""))


# ------------------------------------------------------------------ board, record, DRC

def seed(design, libs, src: Path, dst: Path, nets) -> dict[str, dict]:
    """Seed routes for `nets` on the board `src`; the accepted ones are written as tracks (and vias) of their net
    into `dst` (only when one is accepted). Returns net -> {ok, msg, length, width, min_width, neck, narrow,
    segments, vias}."""
    from ..board.pcb import read_board
    if libs is None:
        from ...core.libs import Libs
        libs = Libs(design=design)
    src, dst = Path(src), Path(dst)
    board = read_board(src)
    model = corridor.Model(design, libs, board=board)
    floor = routing.neck_floor(design.board)
    out, placed = {}, []
    for net in sorted(nets):
        r = route_net(model, design, net, board, placed, floor)
        out[net] = r
        if r["ok"]:
            placed += [(net, L, _line((x1, y1), (x2, y2)).buffer(c / 2, quad_segs=QS))
                       for x1, y1, x2, y2, c, L in r["segments"]]
            placed += [(net, None, Point(x, y).buffer(model.via_r, quad_segs=QS)) for x, y in r["vias"]]
    if any(r["ok"] for r in out.values()):
        t = sexpr.loads(src.read_text())
        idx = {str(n[2]): n[1] for n in sexpr.find_all(t, "net") if len(n) > 2}
        b = design.board
        for net, r in out.items():
            if not r["ok"]:
                continue
            ref = ["net", idx[net]] if net in idx else ["net", Q(net)]
            for x1, y1, x2, y2, c, L in r["segments"]:
                t.append(["segment", ["start", x1, y1], ["end", x2, y2], ["width", c], ["layer", Q(L)], list(ref),
                          ["uuid", Q(str(uuid.uuid4()))]])
            for x, y in r["vias"]:
                t.append(["via", ["at", x, y], ["size", b["via_dia_mm"]], ["drill", b["via_drill_mm"]],
                          ["layers", Q("F.Cu"), Q("B.Cu")], list(ref), ["uuid", Q(str(uuid.uuid4()))]])
        dst.write_text(sexpr.dumps(t) + "\n")
    return out


def record(workdir: Path, results: dict):
    """Add the accepted seed copper of `results` to the run's record (RECORD in `workdir`)."""
    f = Path(workdir) / RECORD
    cur = load(workdir)
    cur.update({n: {"segments": r["segments"], "vias": r["vias"]} for n, r in results.items() if r.get("ok")})
    f.write_text(json.dumps(cur))


def load(workdir: Path) -> dict:
    f = Path(workdir) / RECORD
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except ValueError:
        return {}


def _key(net, x1, y1, x2, y2, layer):
    a, b = (round(x1, 3), round(y1, 3)), (round(x2, 3), round(y2, 3))
    return (net, layer) + tuple(sorted((a, b)))


def drop_on_drc(seeds: dict, tracks: list, vias: list, drc: list[dict], reach: float = 0.5,
                copper_only: bool = False) -> tuple[list, list, list]:
    """Remove the seed copper of every net with a DRC error touching it: an item of the error names the net
    (`[net]`) and the error lies within `reach` mm of its seed copper. `copper_only`: that item must be a track, arc
    or via (an error on the net's pad, e.g. a fixed pad's creepage to a pour, is not the seed copper's).
    Returns (tracks, vias, dropped nets)."""
    drop = []
    kinds = ("Track", "Via", "Arc")
    for net, s in seeds.items():
        geo = unary_union([_line((x1, y1), (x2, y2)) for x1, y1, x2, y2, _, _ in s["segments"]]
                          + [Point(x, y) for x, y in s["vias"]])
        for v in drc:
            if v["sev"] != "error" or not any(f"[{net}]" in str(i or "") and (not copper_only or str(i).startswith(kinds))
                                              for i in v.get("items") or []):
                continue
            if any(p and geo.distance(Point(p)) <= reach for p in v.get("at") or []):
                drop.append(net)
                break
    if not drop:
        return tracks, vias, []
    segs = {_key(n, *x[:4], x[5]) for n in drop for x in seeds[n]["segments"]}
    vs = {(n, round(x, 3), round(y, 3)) for n in drop for x, y in seeds[n]["vias"]}
    tracks = [t for t in tracks if _key(t["net"], t["x1"], t["y1"], t["x2"], t["y2"], t["layer"]) not in segs]
    vias = [v for v in vias if (v["net"], round(v["x"], 3), round(v["y"], 3)) not in vs]
    return tracks, vias, drop
