"""Roads first (prototype, HWFLOW_ROADS=1): plan the critical routing infrastructure before the parts are placed, as
urban planning lays out the roads before the buildings.

1. classify: the "roads" are the nets whose room the rules shrink or whose copper is large: board.isolation members,
   nets with `layers`, nets of a floorplan region with `nets` (corridor.constrained), nets with `min_width_mm` or
   `impedance_ohm`, and nets whose track is at least WIDE_X x the board track. Everything else is local traffic.
2. plan: each road whose pads are all known before placement (fixed parts) gets a path through the free space of
   the fixed copper only (corridor.Model before placement, the exact spacings of the corridor proof), with the
   seed-route search (seedroute.route_net): the shortest raster path that keeps every spacing, preferring full
   width over a neck. When the net belongs to a floorplan region, the path is first searched inside that region
   (grown by REGION_SLACK_MM), then anywhere. Roads are planned in order (isolation members first, then the
   widest), each later road keeping its spacing from the earlier roads' copper.
3. reserve: each planned road becomes a reservation on its copper layers (F.Cu / B.Cu only: an inner layer road
   blocks no part): its centre line grown by half its width plus the distance a foreign pad keeps from it (the
   isolation distance + placer.ISO_MARGIN for an isolation member, else the larger class clearance +
   placer.CLEAR_MARGIN). The reservation is a rule area that forbids footprints (placer `parts.areas`), split into
   axis-aligned boxes (the placer measures boxes) on a RASTER_MM grid. Parts with a pad on the road's net, or on a
   net of its isolation group, are exempt (their own spacing rules hold). Global placement, legalization, the local
   moves and the anneal all see the boxes as blockages.
4. place the rest with the reservations, then check each road on the placed board (the same search, with every
   part's copper): a road the placement closed gets its reservation grown by GROW_MM and the parts are placed again
   (zoning feedback, at most FEEDBACK rounds), instead of a new placer seed.

Routing needs no change: the flow already routes isolation members first, then the widest nets (krt.krt_route),
and a constrained net the router leaves open gets a seed route in the corridor proof's free space, which the
reservation keeps open.

Interface: classify(design) -> {net: reason}; plan(design, libs) -> [road]; areas(roads, design) -> [area];
check(design, libs, parts, roads) -> {net: ok}; place(design, libs, seeds, iters, seed0) -> (parts, cost, viol, info).
"""

from __future__ import annotations

import math
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from ...core import floorplan
from ...core.settings import get_settings, use, with_layout_env
from ..board import pcb, routing
from ..place import corridor
from . import seedroute

WIDE_X = 2.0           # a net whose track is at least this x the board track is a road
RASTER_MM = 0.5        # reservation boxes: the grid the reservation polygon is split on
REGION_SLACK_MM = 1.0  # a region net's road is first searched inside its region grown by this
GROW_MM = 1.0          # zoning feedback: a road the placement closed widens its reservation by this per side
FEEDBACK = 2           # zoning feedback rounds at most
COARSE = 0.25          # movable roads: the coarse placement that gives their endpoints, as a share of the placer budget
MAX_PADS = 16          # a movable road with more pads is a spread net (a rail to every part), not a road
EPS = 1e-6


def _mode(design=None) -> str:
    """HWFLOW_ROADS: 0 (off), 1 (reservations + routing the roads first), route (routing the roads first only)."""
    v = get_settings().layout.roads
    if v is None:
        v = str(((getattr(design, "raw", None) or {}).get("placement") or {}).get("roads") or "0")
    return {"true": "1", "false": "0", "True": "1", "False": "0"}.get(v, v)


@contextmanager
def roads_off():
    """The block runs with HWFLOW_ROADS=0 (settings layout.roads), whatever the environment or the design says."""
    with use(with_layout_env(get_settings(), {"HWFLOW_ROADS": "0"})):
        yield


def on(design=None) -> bool:
    """The road reservations in placement."""
    return _mode(design) == "1"


def on_route(design=None) -> bool:
    """The planned roads on the board before the router (preseed)."""
    return _mode(design) in ("1", "route")


class Model(corridor.Model):
    """corridor.Model whose isolation distance is the larger of the group's clearance and creepage: a road planned
    at the clearance alone fails the creepage rule of KiCad DRC on a flat board (no slot), and the final DRC then
    removes it."""

    def __init__(self, design, libs, parts=None, board=None, use=None):
        super().__init__(design, libs, parts, board, use)
        need = {frozenset(g["members"]): float(g["mm"]) for g in pcb.isolation_needs(design)}
        have = {frozenset(m) for m, _ in self.iso}
        self.iso = [(m, max(c, need.get(frozenset(m), c))) for m, c in self.iso] + \
            [(set(m), mm) for m, mm in need.items() if m not in have]


# ------------------------------------------------------------------ 1. classify

def classify(design) -> dict[str, str]:
    """net -> why it is a road."""
    b = design.board
    track = float(b["track_mm"])
    classes = pcb.net_classes(design)
    gnds = design.ground_nets() if b.get("gnd_pour") else set()
    iso = {n for g in pcb.isolation_needs(design) for n in g["members"]}
    lays = pcb.net_layers(design)
    cons = set(corridor.constrained(design))
    power = get_settings().layout.roads_power  # HWFLOW_ROADS_POWER: also every power-class net (experiment knob)
    out = {}
    for n, cl in classes.items():
        if n in gnds:
            continue
        info = design.net_info(n)
        why = ("isolation" if n in iso else "layers" if n in lays else "region" if n in cons
               else "min_width" if info.get("min_width_mm") is not None
               else "impedance" if info.get("impedance_ohm") is not None
               else "wide" if cl["width"] >= WIDE_X * track - EPS
               else "power" if power and cl["class"] == "power" else None)
        if why:
            out[n] = why
    return out


# ------------------------------------------------------------------ 2. plan

def _order(design, roads: dict) -> list[str]:
    classes = pcb.net_classes(design)
    rank = {"isolation": 0, "layers": 1, "region": 1, "min_width": 2, "impedance": 2, "wide": 3, "power": 4}
    return sorted(roads, key=lambda n: (rank[roads[n]], -classes[n]["width"], n))


def _region_of(design) -> dict:
    out = {}
    for r in floorplan.net_regions(design):
        for n in r["nets"]:
            out.setdefault(n, []).append(r["poly"])
    return {n: unary_union(v) for n, v in out.items()}


def _out_spacing(design, net) -> float:
    """The distance a pad of a non-road part keeps from the road's copper (placer values, with their margins)."""
    from ..place import placer
    b = design.board
    s = max(float(b["clearance_mm"]), float(design.net_info(net).get("clearance_mm", b["clearance_mm"])))
    s += placer.CLEAR_MARGIN
    for g in pcb.isolation_needs(design):
        if net in g["members"]:
            s = max(s, float(g["mm"]) + placer.ISO_MARGIN)
    return s


def _exempt(design, net) -> set[str]:
    """Parts that may sit on the road: those with a pad on the net or on another net of its isolation group."""
    nets = {net}
    for g in pcb.isolation_needs(design):
        if net in g["members"]:
            nets |= set(g["members"])
    return {r for n in nets for r, _ in design.nets().get(n, [])}


def _route(model, design, net, placed, floor, region=None, board=None) -> dict:
    """seedroute.route_net, first with the outline cut to `region` (grown by REGION_SLACK_MM), then without."""
    if region is not None:
        full = model.outline
        model.outline = full.intersection(region.buffer(REGION_SLACK_MM))
        model._free = {}
        try:
            r = seedroute.route_net(model, design, net, board or {"segments": [], "vias": []}, placed, floor)
        except Exception as e:  # noqa: BLE001 - a geometry the cut outline breaks: search the whole board
            r = {"ok": False, "msg": f"{type(e).__name__}: {e}"}
        finally:
            model.outline = full
            model._free = {}
        if r["ok"]:
            r["in_region"] = True
            return r
    return seedroute.route_net(model, design, net, board or {"segments": [], "vias": []}, placed, floor)


def plan(design, libs, parts=None, nets=None, board=None, before=()) -> list[dict]:
    """Road plan on the fixed copper (parts=None) or on a placed board (`parts`, the zoning check). Returns
    [{net, why, width, layers, ok, msg, segments, vias, spacing, exempt, fixed}]: `fixed` when every pad of the net is
    on a fixed part (only those roads are planned before placement)."""
    roads = classify(design)
    if nets is not None:
        roads = {n: v for n, v in roads.items() if n in nets}
    model = Model(design, libs, parts=parts, board=board)
    fixed_refs = {r for r, v in model.pos.items() if v[3]}
    floor = routing.neck_floor(design.board)
    regions = _region_of(design)
    conns = design.nets()
    out, placed = [], list(before)
    for n in _order(design, roads):
        refs = {r for r, _ in conns.get(n, []) if r in design.components and not design.components[r].get("virtual")}
        row = {"net": n, "why": roads[n], "width": model.width(n), "layers": model.net_copper(n),
               "fixed": refs <= fixed_refs, "spacing": _out_spacing(design, n), "exempt": sorted(_exempt(design, n))}
        if not row["fixed"] and parts is None and board is None:
            out.append(dict(row, ok=False, msg=f"road {n}: pads on movable parts, not planned before placement"))
            continue
        npads = len(conns.get(n, []))
        if not row["fixed"] and npads > MAX_PADS:
            out.append(dict(row, ok=False, msg=f"road {n}: {npads} pads, a spread net, not a road"))
            continue
        t = time.time()
        try:
            r = _route(model, design, n, placed, floor, regions.get(n), board)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "msg": f"road {n}: {type(e).__name__}: {e}"}
        row.update(ok=r["ok"], msg=r["msg"].replace("seed route", "road"), seconds=round(time.time() - t, 2),
                   segments=r.get("segments") or [], vias=r.get("vias") or [], in_region=r.get("in_region", False))
        if r["ok"]:
            placed += [(n, L, LineString([(x1, y1), (x2, y2)]).buffer(c / 2, quad_segs=8))
                       for x1, y1, x2, y2, c, L in row["segments"]]
            placed += [(n, None, Point(x, y).buffer(model.via_r, quad_segs=8)) for x, y in row["vias"]]
        out.append(row)
    return out


# ------------------------------------------------------------------ 3. reserve

def reservation(road, grow: float = 0.0) -> dict:
    """layer -> polygon: the centre line grown by half the track plus the foreign-pad spacing (+ `grow`)."""
    out = {}
    for x1, y1, x2, y2, c, L in road["segments"]:
        g = LineString([(x1, y1), (x2, y2)]) if (x1, y1) != (x2, y2) else Point(x1, y1)
        out.setdefault(L, []).append(g.buffer(c / 2 + road["spacing"] + grow, quad_segs=8))
    for x, y in road["vias"]:
        for L in road["layers"]:
            out.setdefault(L, []).append(Point(x, y).buffer(road["width"] / 2 + road["spacing"] + grow, quad_segs=8))
    return {L: unary_union(v) for L, v in out.items()}


def _boxes(poly, h=RASTER_MM) -> list[tuple]:
    """Axis-aligned boxes that cover `poly` (cells of side h whose centre lies in poly grown by h/sqrt2, merged into
    row runs, then runs with the same span merged down the rows)."""
    import shapely
    x0, y0, x1, y1 = poly.bounds
    xs = np.arange(math.floor(x0 / h) * h, x1 + h, h)
    ys = np.arange(math.floor(y0 / h) * h, y1 + h, h)
    X, Y = np.meshgrid(xs + h / 2, ys + h / 2)
    inside = shapely.contains_xy(poly.buffer(h / math.sqrt(2) * 0.5), X, Y)
    runs = []  # (j, i0, i1)
    for j in range(inside.shape[0]):
        row = inside[j]
        i = 0
        while i < len(row):
            if row[i]:
                k = i
                while k + 1 < len(row) and row[k + 1]:
                    k += 1
                runs.append((j, i, k))
                i = k + 1
            else:
                i += 1
    out, open_ = [], {}
    for j, i0, i1 in runs:
        key = (i0, i1)
        if key in open_ and open_[key][1] == j - 1:
            open_[key] = (open_[key][0], j)
        else:
            if key in open_:
                out.append((key, open_[key]))
            open_[key] = (j, j)
    out += list(open_.items())
    return [(float(xs[i0]), float(ys[j0]), float(xs[i1] + h), float(ys[j1] + h)) for (i0, i1), (j0, j1) in out]


def _copper(roads) -> list[tuple]:
    """The planned copper of `roads` as seedroute `placed` obstacles."""
    out = []
    for r in roads:
        if r.get("ok"):
            out += [(r["net"], L, LineString([(x1, y1), (x2, y2)]).buffer(c / 2, quad_segs=8))
                    for x1, y1, x2, y2, c, L in r["segments"]]
            out += [(r["net"], None, Point(x, y).buffer(r["width"] / 2, quad_segs=8)) for x, y in r["vias"]]
    return out


def areas(roads, design, grow: dict | None = None) -> list[dict]:
    """placer `parts.areas` entries for the planned roads: rule areas that forbid footprints (a road between fixed
    parts) or pads (a road planned on a coarse placement: the parts around it only keep their copper off it), with
    `exempt` refs."""
    out = []
    for road in roads:
        if not road.get("ok"):
            continue
        for L, poly in reservation(road, (grow or {}).get(road["net"], 0.0)).items():
            if L not in ("F.Cu", "B.Cu"):
                continue
            W, H = float(design.board["width_mm"]), float(design.board["height_mm"])
            poly = poly.intersection(box(0, 0, W, H))
            for k, bx in enumerate(_boxes(poly)):
                out.append({"name": f"road {road['net']} {L} {k}", "owner": f"road:{road['net']}",
                            "forbids": {"footprints"} if road["fixed"] else {"pads"}, "layers": [L], "box": bx,
                            "poly": box(*bx),
                            "exempt": frozenset(road["exempt"])})
    return out


# ------------------------------------------------------------------ 4. place with the reservations, zoning feedback

def check(design, libs, parts, roads) -> dict[str, dict]:
    """Each planned road on the placed board: does the same search still find it with every part's copper?"""
    nets = [r["net"] for r in roads if r.get("ok")]
    got = plan(design, libs, parts=parts, nets=nets) if nets else []
    return {r["net"]: r for r in got}


def place(design, libs, seeds: int = 4, iters: int = 30000, weights=None, seed0: int = 1,
          deadline: float | None = None):
    """placer.place with the road reservations as blockages, then the zoning feedback. `deadline` (time.time()) goes
    to every placer.place call. Returns (parts, cost, violations without the reservations, info)."""
    from ..place import placer
    t = time.time()
    roads = plan(design, libs)
    info = {"plan_s": round(time.time() - t, 2), "roads": [{k: r.get(k) for k in ("net", "why", "fixed", "ok", "msg", "seconds", "in_region")}
                                                           for r in roads], "rounds": []}
    movable = [r["net"] for r in roads if not r["fixed"]]
    if movable:  # endpoints from a coarse placement around the fixed roads, then the movable roads on it
        t = time.time()
        coarse, _, _ = placer.place(design, libs, seeds=1, iters=max(1000, int(iters * COARSE)), weights=weights,
                                    seed0=seed0, areas=areas(roads, design), deadline=deadline)
        got = {r["net"]: r for r in plan(design, libs, parts=coarse, nets=movable, before=_copper(roads))}
        roads = [got.get(r["net"], r) for r in roads]
        for r in roads:
            if r["net"] in got:
                r["fixed"] = False
        info["coarse_s"] = round(time.time() - t, 1)
        info["roads"] = [{k: r.get(k) for k in ("net", "why", "fixed", "ok", "msg", "seconds", "in_region")} for r in roads]
    for r in roads:
        print(f"[roads] {r['why']}: {r['msg']}")
    grow: dict = {}
    best = None
    for k in range(FEEDBACK + 1):
        ar = areas(roads, design, grow)
        t = time.time()
        parts, cost, viol = placer.place(design, libs, seeds=seeds, iters=iters, weights=weights, seed0=seed0,
                                         areas=ar, deadline=deadline)
        res = check(design, libs, parts, roads)
        closed = sorted(n for n, r in res.items() if not r["ok"])
        rv = [v for v in viol if "keep-out 'road " in v]
        viol = [v for v in viol if "keep-out 'road " not in v]
        info["rounds"].append({"boxes": len(ar), "place_s": round(time.time() - t, 1), "closed": closed,
                               "in_reservation": rv, "grow": dict(grow)})
        print(f"[roads] round {k + 1}: {len(ar)} reservation boxes, placement {time.time() - t:.0f} s, "
              f"roads closed by the placement: {', '.join(closed) or 'none'}; parts left in a reservation: {len(rv)}")
        if best is None or len(closed) < best[0]:
            best = (len(closed), parts, cost, viol)
        if not closed:
            break
        for n in closed:
            grow[n] = grow.get(n, 0.0) + GROW_MM
    _, parts, cost, viol = best
    return parts, cost, viol, info


# ------------------------------------------------------------------ 5. route the roads first, as planned

SEED_WHY = ("isolation", "layers", "region", "min_width")  # the roads whose planned copper goes on the board first


def preseed(design, libs, src, workdir, stem, nets=None):
    """Before the router: the planned copper of the roads in `nets` (default every routed net) that have no copper on
    the board `src` yet, planned on that board (its pads, tracks and vias as obstacles) in road order, written into
    a copy of it as tracks of their nets and recorded as seed copper (seedroute.RECORD: the final DRC removes a road
    an error touches, and repair reroutes it). Returns (board path, nets left to route or None, seeded nets)."""
    import uuid

    from ...core import sexpr
    from ...core.sexpr import Q
    from ..board.pcb import read_board
    if libs is None:
        from ...core.libs import Libs
        libs = Libs(design=design)
    src = Path(src)
    board = read_board(src)
    have = {s_["net"] for s_ in board["segments"]} | {v["net"] for v in board["vias"]}
    cand = {n for n, why in classify(design).items() if why in SEED_WHY and n not in have
            and (nets is None or n in nets)}
    if not cand:
        return src, nets, []
    t = time.time()
    rows = [r for r in plan(design, libs, nets=cand, board=board) if r["ok"]]
    print(f"[roads] {len(rows)} of {len(cand)} roads planned on the placed board in {time.time() - t:.1f} s: "
          + ", ".join(r["msg"] for r in rows))
    if not rows:
        return src, nets, []
    tree = sexpr.loads(src.read_text())
    idx = {str(n[2]): n[1] for n in sexpr.find_all(tree, "net") if len(n) > 2}
    b = design.board
    for r in rows:
        ref = ["net", idx[r["net"]]] if r["net"] in idx else ["net", Q(r["net"])]
        for x1, y1, x2, y2, c, L in r["segments"]:
            tree.append(["segment", ["start", x1, y1], ["end", x2, y2], ["width", c], ["layer", Q(L)], list(ref),
                         ["uuid", Q(str(uuid.uuid4()))]])
        for x, y in r["vias"]:
            tree.append(["via", ["at", x, y], ["size", b["via_dia_mm"]], ["drill", b["via_drill_mm"]],
                         ["layers", Q("F.Cu"), Q("B.Cu")], list(ref), ["uuid", Q(str(uuid.uuid4()))]])
    dst = Path(workdir) / f"{stem}roads.kicad_pcb"
    dst.write_text(sexpr.dumps(tree) + "\n")
    seedroute.record(workdir, {r["net"]: dict(r, ok=True) for r in rows})
    done = {r["net"] for r in rows}
    left = (set(nets) if nets is not None else set(pcb.net_classes(design))) - done
    return dst, left, sorted(done)


def reroute(design, libs, parts, workdir, tracks, vias, nets):
    """Roads the final DRC removed (seedroute.drop_on_drc) go back to the router as ordinary nets: one krt_route call
    for `nets` on the board with the other copper kept, without the preseed. Returns (tracks, vias, note)."""
    from . import krt
    workdir = Path(workdir)
    src = workdir / f"{design.name}_roads_back.kicad_pcb"
    pcb.write_board(src, pcb.build_board(design, libs, parts, tracks, vias))
    with roads_off():
        cur, last, _, _ = krt.krt_route(design, src, workdir, f"{design.name}_roads_back_", nets=set(nets),
                                        keep_first=True, libs=libs)
    b = pcb.read_board(cur)
    left = sorted({n for v in last.values() for n in krt.krt_open_nets(v)} & set(nets))
    return ([dict(x) for x in b["segments"] if x["net"]], [dict(v) for v in b["vias"] if v["net"]],
            f"roads {', '.join(sorted(nets))} routed again by the router" + (f" ({', '.join(left)} still open)" if left else ""))
