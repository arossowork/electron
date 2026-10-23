"""Global routing in the style of ASIC tools (FastRoute / CUGR with PathFinder negotiated congestion), plus pin
access analysis in the style of TritonRoute, for hwflow's PCB routing. Experimental: off by default (HWFLOW_GROUTE=1
switches the router hooks in pcb.krt_route on; docs/asic-ideas.md has the measurements).

Model
-----
The board is cut into g-cells on every copper layer, of side `pitch(design)`: TRACKS_PER_CELL board tracks at the
board clearance (TRACKS_PER_CELL x (track + clearance)), clamped to [PITCH_MIN_MM, PITCH_MAX_MM] (HWFLOW_GROUTE_PITCH
overrides it). Nothing in this module is specific to a design: every input is the board's rules and placement: a 3D grid with planar edges
between neighbouring cells of one layer and via edges between the layers of one cell (a through via joins every
layer, so any two layers of a cell are joined directly).

Capacity of a planar edge = the tracks of the board width that fit across the boundary between its two cells:
the boundary line is sampled every SAMPLE_MM, a sample is blocked when it lies in an obstacle grown by the board
clearance, and every free run of length f holds floor((f + clr) / (track + clr)) tracks. Obstacles, from
barrier.Model / corridor.Model (the free-space model of the feasibility proofs):
  - every pad on that layer (through-hole pads are on every layer), non-plated holes (HOLE_MM),
  - rule areas that forbid tracks on the layer (footprint keep-outs, KO_ parts, notches), the board outline and
    cuts (EDGE_MM inset). A boundary blocked by these alone is impassable; one blocked by pads is only expensive
    (its capacity is 0: any use is overflow), since the net's own pads are part of it.
Rules of single nets become per-net forbidden cells (a cell the region covers whole; a narrow corridor through
partly covered cells stays open, so the bound leans towards more room, like barrier's): `.kicad_dru` disallow
rules naming the net, conductive isolation, board.isolation (a member keeps `mm` from non-member pads and
`between_mm` from other members' pads, a non-member keeps `mm` from member pads), nets.<n>.layers (other layers
forbidden). A cell that holds a terminal of the net is never forbidden to it.
Via capacity of a cell = via sites (sampled on VIA_SAMPLE_MM, via_dia/2 + clr from every pad, inside the outline,
outside via keep-outs) / sites per via ((via_dia + clr)^2).

A net of track width w uses (w + clr) / (track + clr) units of every edge it crosses (wide nets use more), at most
the capacity of an empty boundary (a track wider than a g-cell fills it; its spill is not modelled). Edges
that touch the cell of a pad centre of the net are its pin access and count no usage (the detail there is the pin
access analysis below, not the g-cell capacity).

Routing: every net is a tree grown Prim-style with A*: from the tree so far to the nearest unconnected terminal
(multi-source A*, so the result is a rectilinear Steiner tree, not a set of independent 2-pin routes). Then
PathFinder: the cost of an edge is base * (1 + history) * (1 + pres * overflow_if_used); after every iteration
history += HIST * overflow of every overflowed edge and pres *= PRES_GROW, and only the nets crossing an
overflowed edge are ripped up and rerouted, until no overflow or ITERS iterations.

Output per net: the g-cells it uses (its route guide), its layers, its vias, its remaining overflow, and a
criticality (order hint: most constrained first).

Pin access (TritonRoute style)
------------------------------
For every pad with a net, `pin_access` checks ACCESS_DIRS directions on each copper layer of the pad: a track of
the net's width (and, separately, of the board minimum width) starting at the pad centre must leave the pad's
copper and reach a point ACCESS_STEP_MM beyond its edge with its centre line in the configuration space of that
layer: the board outline inset by w/2, minus every foreign pad and hole within ACCESS_RADIUS_MM grown by the pair's
clearance + w/2 (net classes, isolation, between_mm; ACCESS_RADIUS_MM or the pad's reach + 1 mm), minus the rule areas forbidding tracks to the net grown by w/2
(the same reading as partescape.footprint_pads / _Part.free, extended from the part's own pads to the whole board).
An SMD pad that has no access on its layer at any width is reported (`no_access`): its net can only leave by a
via in the pad. With `access=True` the global router starts every pad from the g-cells of its access points
instead of the pad centre.
"""
from __future__ import annotations

import heapq
import math
import os
import time
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

TRACKS_PER_CELL = 3     # g-cell side in board tracks (track + clearance): 0.25 + 0.2 mm -> 1.35 mm
PITCH_MIN_MM, PITCH_MAX_MM = 0.75, 2.5
SAMPLE_MM = 0.025       # boundary sampling step (capacity resolution)
VIA_SAMPLE_MM = 0.25    # via-site sampling step inside a cell
ITERS = int(os.environ.get("HWFLOW_GROUTE_ITERS", 30))
PRES0, PRES_GROW, HIST = 0.5, 1.5, 1.0
VIA_COST = 4.0          # a via costs this many planar g-cell steps
BBOX_MARGIN = 6         # A* search window: terminal bounding box + this many cells (full board when that fails)
ACCESS_DIRS = 8
ACCESS_STEP_MM = 0.3
ACCESS_RADIUS_MM = 4.0
INF = float("inf")


def enabled() -> bool:
    return os.environ.get("HWFLOW_GROUTE", "0") == "1"


# ------------------------------------------------------------------ problem


@dataclass
class Problem:
    nx: int
    ny: int
    layers: list
    pitch: float
    capH: np.ndarray            # [L, ny, nx-1]
    capV: np.ndarray            # [L, ny-1, nx]
    hardH: np.ndarray           # bool: impassable
    hardV: np.ndarray
    capVia: np.ndarray          # [ny, nx]
    nets: dict                  # net -> {terms: [set(node)], demand, allowed: set(L idx), forbid: set(node)|None}
    access: dict = field(default_factory=dict)
    build_s: float = 0.0

    def node(self, L, x, y):
        return (L * self.ny + y) * self.nx + x

    def xyl(self, n):
        x = n % self.nx
        r = n // self.nx
        return r // self.ny, x, r % self.ny

    def cell(self, px, py):
        return (min(max(int(px / self.pitch), 0), self.nx - 1), min(max(int(py / self.pitch), 0), self.ny - 1))


def _runs_cap(free: np.ndarray, step: float, track: float, clr: float) -> int:
    """Tracks of width `track` that fit in the free runs of the boolean sample row `free`."""
    if not free.any():
        return 0
    f = np.concatenate(([0], free.astype(np.int8), [0]))
    d = np.diff(f)
    starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    n = 0
    for s, e in zip(starts, ends):
        n += int(((e - s) * step + clr + 1e-9) // (track + clr))
    return n


def _model(design, libs, board=None, parts=None):
    from . import corridor
    return corridor.Model(design, libs, parts=parts, board=board)


def pitch(design) -> float:
    """The g-cell side for `design`: TRACKS_PER_CELL x (track + clearance) of the board, clamped, rounded to 0.05 mm;
    HWFLOW_GROUTE_PITCH overrides it."""
    env = os.environ.get("HWFLOW_GROUTE_PITCH")
    if env:
        return float(env)
    b = design.board
    p = TRACKS_PER_CELL * (float(b["track_mm"]) + float(b["clearance_mm"]))
    return round(min(max(p, PITCH_MIN_MM), PITCH_MAX_MM) / 0.05) * 0.05


def build(design, libs, board=None, parts=None, model=None, pitch: float | None = None, access: bool = False,
          nets=None) -> Problem:
    """The global-routing problem of `design` at the placement of `board` (pcb.read_board) or `parts`."""
    from . import pcb
    t0 = time.time()
    pitch = pitch or globals()["pitch"](design)
    m = model or _model(design, libs, board, parts)
    b = design.board
    W, H = float(b["width_mm"]), float(b["height_mm"])
    nx, ny = max(2, math.ceil(W / pitch)), max(2, math.ceil(H / pitch))
    copper = m.copper
    nL = len(copper)
    track, clr = float(b["track_mm"]), float(b["clearance_mm"])
    from .barrier import HOLE_MM
    outline_bad = box(-1, -1, W + 1, H + 1).difference(m.outline)
    holes = [h.buffer(HOLE_MM) for h in m.holes]
    capH = np.zeros((nL, ny, nx - 1))
    capV = np.zeros((nL, ny - 1, nx))
    hardH = np.zeros((nL, ny, nx - 1), bool)
    hardV = np.zeros((nL, ny - 1, nx), bool)
    ys = np.arange(SAMPLE_MM / 2, ny * pitch, SAMPLE_MM)
    xs = np.arange(SAMPLE_MM / 2, nx * pitch, SAMPLE_MM)
    per = int(round(pitch / SAMPLE_MM))
    for li, L in enumerate(copper):
        hard = [outline_bad] + [a["poly"] for a in m.areas if L in a["layers"] and "tracks" in a["forbids"]]
        hard_g = unary_union(hard).buffer(clr)  # copper keeps the clearance from a keep-out edge too
        pads = [p["obst"].buffer(clr) for p in m.pads if L in p["layers"]]
        soft_g = unary_union(pads + holes + [hard_g])
        shapely.prepare(soft_g)
        shapely.prepare(hard_g)
        for k in range(1, nx):        # vertical boundary lines: horizontal edges (x = k*pitch)
            X = np.full_like(ys, k * pitch)
            fs = ~shapely.contains_xy(soft_g, X, ys)
            fh = ~shapely.contains_xy(hard_g, X, ys)
            for j in range(ny):
                sl = slice(j * per, (j + 1) * per)
                capH[li, j, k - 1] = _runs_cap(fs[sl], SAMPLE_MM, track, clr)
                hardH[li, j, k - 1] = _runs_cap(fh[sl], SAMPLE_MM, float(m.floor), clr) == 0
        for j in range(1, ny):        # horizontal boundary lines: vertical edges
            Y = np.full_like(xs, j * pitch)
            fs = ~shapely.contains_xy(soft_g, xs, Y)
            fh = ~shapely.contains_xy(hard_g, xs, Y)
            for i in range(nx):
                sl = slice(i * per, (i + 1) * per)
                capV[li, j - 1, i] = _runs_cap(fs[sl], SAMPLE_MM, track, clr)
                hardV[li, j - 1, i] = _runs_cap(fh[sl], SAMPLE_MM, float(m.floor), clr) == 0
    # via sites
    vr = m.via_r
    vblock = [p["obst"].buffer(vr + clr) for p in m.pads] + [h.buffer(vr) for h in holes]
    vblock += [m.outline.exterior.buffer(vr)] + [outline_bad]
    vblock += [a["poly"].buffer(vr) for a in m.areas if "vias" in a["forbids"]]
    vg = unary_union(vblock)
    shapely.prepare(vg)
    gx, gy = np.meshgrid(np.arange(VIA_SAMPLE_MM / 2, nx * pitch, VIA_SAMPLE_MM),
                         np.arange(VIA_SAMPLE_MM / 2, ny * pitch, VIA_SAMPLE_MM))
    ok = ~shapely.contains_xy(vg, gx.ravel(), gy.ravel()).reshape(gx.shape)
    vper = int(round(pitch / VIA_SAMPLE_MM))
    site = VIA_SAMPLE_MM ** 2
    per_via = (2 * vr + clr) ** 2
    capVia = np.zeros((ny, nx))
    for j in range(ny):
        for i in range(nx):
            a = ok[j * vper:(j + 1) * vper, i * vper:(i + 1) * vper].sum() * site
            capVia[j, i] = math.floor(a / per_via + 0.3) if a > 0 else 0
    P = Problem(nx, ny, list(copper), pitch, capH, capV, hardH, hardV, capVia, {})
    # nets
    classes = pcb.net_classes(design)
    gnds = design.ground_nets() if b.get("gnd_pour") else set()
    acc = pin_access(design, m) if access else {}
    P.access = acc
    want = set(nets) if nets is not None else None
    by_net: dict[str, list] = {}
    for p in m.pads:
        if p["net"] and p["net"] not in gnds and (want is None or p["net"] in want):
            by_net.setdefault(p["net"], []).append(p)
    # a track wider than a g-cell fills an empty boundary (its spill into the next cell is not modelled)
    full = float(int((pitch + clr + 1e-9) // (track + clr)))
    centers = [(i + 0.5) * pitch for i in range(nx)], [(j + 0.5) * pitch for j in range(ny)]
    for net, pads in by_net.items():
        allowed = {copper.index(L) for L in m.net_copper(net)}
        w = float(classes.get(net, {}).get("width", track))
        terms = []
        home = set()  # the pad-centre cells: their edges are pin access, no usage (in both modes)
        for p in pads:
            c0 = p["own"].centroid
            hx, hy = P.cell(c0.x, c0.y)
            home.add(hy * nx + hx)
            lays = [copper.index(L) for L in p["layers"] if copper.index(L) in allowed] or \
                   [copper.index(L) for L in p["layers"]]
            a = acc.get((p["ref"], p["number"]))
            nodes = set()
            if access and a and a["points"]:
                for (L, x, y) in a["points"]:
                    li = copper.index(L)
                    if li in allowed:
                        cx, cy = P.cell(x, y)
                        nodes.add(P.node(li, cx, cy))
            if not nodes:
                c = p["own"].centroid
                cx, cy = P.cell(c.x, c.y)
                nodes = {P.node(li, cx, cy) for li in lays}
            terms.append(nodes)
        # merge terminals that share a g-cell
        merged: list[set] = []
        for t in terms:
            hit = [k for k, u in enumerate(merged) if u & t or {n % (nx * ny) for n in u} & {n % (nx * ny) for n in t}]
            if hit:
                base = merged[hit[0]]
                base |= t
                for k in sorted(hit[1:], reverse=True):
                    base |= merged.pop(k)
            else:
                merged.append(set(t))
        if len(merged) < 2:
            continue
        P.nets[net] = {"terms": merged, "demand": min((w + clr) / (track + clr), full), "width": w, "allowed": allowed,
                       "forbid": None, "pins": home,
                       "terms_cells": {n % (nx * ny) for t in merged for n in t}}
    _forbid(P, m, centers)
    P.build_s = round(time.time() - t0, 2)
    return P


def _forbid(P: Problem, m, centers):
    """Per-net forbidden cells from the rules of single nets (isolation, between_mm, disallow rules, conductive
    isolation, nets.<n>.layers). Nets with the same rule signature share one mask."""
    copper = P.layers
    generic = {L: {id(a["poly"]) for a in m.areas if L in a["layers"] and "tracks" in a["forbids"]} for L in copper}
    cx = np.repeat(np.array(centers[0])[None, :], P.ny, 0).ravel()
    cy = np.repeat(np.array(centers[1])[:, None], P.nx, 1).ravel()
    h = P.pitch / 2
    cells = shapely.box(cx - h, cy - h, cx + h, cy + h)
    between = getattr(m, "between", [])
    cache: dict = {}
    for net, nd in P.nets.items():
        iso_sig = tuple(net in mem for mem, _ in m.iso)
        bet_sig = tuple(net in mem for mem, _ in between)
        extra = {L: [g for g in m.keepouts(net, L) if id(g) not in generic[L]] for L in copper}
        sig = (iso_sig, bet_sig, net if any(bet_sig) else None, tuple((L, tuple(sorted(id(g) for g in extra[L]))) for L in copper))
        if not any(iso_sig) and not any(bet_sig) and not m.iso and not any(extra.values()):
            mask = None
        elif sig in cache:
            mask = cache[sig]
        else:
            mask = set()
            for li, L in enumerate(copper):
                polys = list(extra[L])
                for (mem, mm), inside in zip(m.iso, iso_sig):
                    polys += [p["obst"].buffer(mm) for p in m.pads if L in p["layers"] and p["net"] is not None
                              and (p["net"] in mem) != inside]
                for (mem, mm), inside in zip(between, bet_sig):
                    if inside:
                        polys += [p["obst"].buffer(mm) for p in m.pads if L in p["layers"] and p["net"] in mem
                                  and p["net"] != net]
                if not polys:
                    continue
                g = unary_union(polys)
                shapely.prepare(g)
                hit = np.nonzero(shapely.contains(g, cells))[0]  # only a cell the region covers whole
                base = li * P.nx * P.ny
                mask |= {base + int(h) for h in hit}
            cache[sig] = mask
        allowed = nd["allowed"]
        layer_forbid = {li for li in range(len(copper)) if li not in allowed}
        if mask is not None and nd["pins"]:
            # a net's own pins (and pins of the same net names) stay reachable; also own-net cells
            keep = nd["pins"] | nd["terms_cells"]
            mask = {n for n in mask if n % (P.nx * P.ny) not in keep}
            mask = mask or None
        nd["forbid"] = mask
        nd["layer_forbid"] = layer_forbid


# ------------------------------------------------------------------ PathFinder


@dataclass
class Result:
    nets: dict                  # net -> {nodes, edges, layers, vias, overflow, crit, length, ok}
    overflow_edges: list        # [(kind, L, x, y, use, cap)]
    total_overflow: float
    iterations: int
    route_s: float
    build_s: float
    no_access: list = field(default_factory=list)
    access_s: float = 0.0

    def failing(self) -> list[str]:
        return sorted(n for n, r in self.nets.items() if r["overflow"] > 1e-9 or not r["ok"])


class _Router:
    def __init__(self, P: Problem):
        self.P = P
        nL, nx, ny = len(P.layers), P.nx, P.ny
        self.nH = nL * ny * (nx - 1)
        self.nV = nL * (ny - 1) * nx
        self.cap = np.concatenate([P.capH.ravel(), P.capV.ravel(), P.capVia.ravel()]).tolist()
        self.hard = np.concatenate([P.hardH.ravel(), P.hardV.ravel(), np.zeros(ny * nx, bool)]).tolist()
        self.use = [0.0] * len(self.cap)
        self.hist = [0.0] * len(self.cap)
        self.pres = PRES0
        self.routes: dict[str, dict] = {}

    # edge index helpers
    def eH(self, L, x, y):
        P = self.P
        return (L * P.ny + y) * (P.nx - 1) + x

    def eV(self, L, x, y):
        P = self.P
        return self.nH + (L * (P.ny - 1) + y) * P.nx + x

    def eVia(self, x, y):
        return self.nH + self.nV + y * self.P.nx + x

    def cost(self, e, d, local):
        if self.hard[e]:
            return INF
        if local:
            return 1.0
        over = self.use[e] + d - self.cap[e]
        return (1.0 + self.hist[e]) * (1.0 + self.pres * (over if over > 0 else 0.0))

    def astar(self, net, nd, sources: set, targets: set, window):
        P = self.P
        nx, ny, nL = P.nx, P.ny, len(P.layers)
        nxy = nx * ny
        d = nd["demand"]
        pins = nd["pins"]
        forbid = nd["forbid"] or ()
        lf = nd["layer_forbid"]
        x0, y0, x1, y1 = window
        tcells = [(n % nx, (n // nx) % ny) for n in targets]

        def h(n):
            x, y = n % nx, (n // nx) % ny
            return min(abs(x - tx) + abs(y - ty) for tx, ty in tcells)
        dist = {}
        prev = {}
        pq = []
        for s in sources:
            dist[s] = 0.0
            prev[s] = None
            heapq.heappush(pq, (h(s), 0.0, s))
        while pq:
            f, g, n = heapq.heappop(pq)
            if g > dist.get(n, INF):
                continue
            if n in targets:
                path = []
                while n is not None:
                    path.append(n)
                    n = prev[n]
                return path[::-1]
            L, r = divmod(n, nxy)
            y, x = divmod(r, nx)
            local_here = r in pins
            nbrs = []
            if x > x0:
                nbrs.append((n - 1, self.eH(L, x - 1, y), r - 1))
            if x < x1:
                nbrs.append((n + 1, self.eH(L, x, y), r + 1))
            if y > y0:
                nbrs.append((n - nx, self.eV(L, x, y - 1), r - nx))
            if y < y1:
                nbrs.append((n + nx, self.eV(L, x, y), r + nx))
            for m, e, rm in nbrs:
                if m in forbid:
                    continue
                c = self.cost(e, d, local_here or rm in pins)
                if c == INF:
                    continue
                ng = g + c
                if ng < dist.get(m, INF):
                    dist[m] = ng
                    prev[m] = n
                    heapq.heappush(pq, (ng + h(m), ng, m))
            if nL > 1:
                ev = self.eVia(x, y)
                cv = VIA_COST * self.cost(ev, 1.0, local_here)
                for L2 in range(nL):
                    if L2 == L or L2 in lf:
                        continue
                    m = L2 * nxy + r
                    if m in forbid:
                        continue
                    ng = g + cv
                    if ng < dist.get(m, INF):
                        dist[m] = ng
                        prev[m] = n
                        heapq.heappush(pq, (ng + h(m), ng, m))
        return None

    def edges_of(self, path, pins):
        """(edge index, demand-kind, local) of consecutive path nodes."""
        P = self.P
        nx, nxy = P.nx, P.nx * P.ny
        out = []
        for a, b in zip(path, path[1:]):
            La, ra = divmod(a, nxy)
            Lb, rb = divmod(b, nxy)
            local = ra in pins or rb in pins
            if La != Lb:
                y, x = divmod(ra, nx)
                out.append((self.eVia(x, y), "via", local))
                continue
            ya, xa = divmod(ra, nx)
            yb, xb = divmod(rb, nx)
            if ya == yb:
                out.append((self.eH(La, min(xa, xb), ya), "p", local))
            else:
                out.append((self.eV(La, xa, min(ya, yb)), "p", local))
        return out

    def route_net(self, net, nd):
        P = self.P
        terms = nd["terms"]
        allc = [(n % P.nx, (n // P.nx) % P.ny) for t in terms for n in t]
        xs, ys = [c[0] for c in allc], [c[1] for c in allc]
        win = (max(0, min(xs) - BBOX_MARGIN), max(0, min(ys) - BBOX_MARGIN),
               min(P.nx - 1, max(xs) + BBOX_MARGIN), min(P.ny - 1, max(ys) + BBOX_MARGIN))
        full = (0, 0, P.nx - 1, P.ny - 1)
        tree = set(terms[0])
        nodes = set(tree)
        rest = list(range(1, len(terms)))
        edges = []
        ok = True
        while rest:
            # nearest terminal to the tree (Prim), by Manhattan distance of cells
            tc = [(n % P.nx, (n // P.nx) % P.ny) for n in tree]

            def dist(k):
                return min(abs(n % P.nx - x) + abs((n // P.nx) % P.ny - y) for n in terms[k] for x, y in tc)
            k = min(rest, key=dist)
            rest.remove(k)
            path = self.astar(net, nd, tree, terms[k], win) or self.astar(net, nd, tree, terms[k], full)
            if path is None:
                ok = False
                continue
            edges += self.edges_of(path, nd["pins"])
            tree |= set(path) | terms[k]
            nodes |= set(path)
        uniq = {}
        for e, kind, local in edges:
            uniq[e] = (kind, local)
        r = {"nodes": nodes, "edges": uniq, "ok": ok}
        self.commit(nd, r, +1)
        self.routes[net] = r

    def commit(self, nd, r, sign):
        for e, (kind, local) in r["edges"].items():
            if local:
                continue
            self.use[e] += sign * (1.0 if kind == "via" else nd["demand"])

    def overflowed(self):
        return {e for e, (u, c) in enumerate(zip(self.use, self.cap)) if u > c + 1e-9}

    def run(self, iters=ITERS, order=None):
        P = self.P
        names = order or sorted(P.nets, key=lambda n: (-len(P.nets[n]["terms"]), n))
        for n in names:
            self.route_net(n, P.nets[n])
        it = 1
        for it in range(1, iters):
            ov = self.overflowed()
            if not ov:
                break
            for e in ov:
                self.hist[e] += HIST * (self.use[e] - self.cap[e])
            self.pres *= PRES_GROW
            rip = [n for n in names if any(e in ov for e, (k, loc) in self.routes[n]["edges"].items() if not loc)]
            for n in rip:
                self.commit(P.nets[n], self.routes[n], -1)
            for n in rip:
                self.route_net(n, P.nets[n])
        return it


def route(P: Problem, iters: int = ITERS) -> Result:
    t0 = time.time()
    R = _Router(P)
    it = R.run(iters)
    ov = R.overflowed()
    nx, ny, nL = P.nx, P.ny, len(P.layers)
    out = {}
    for net, r in R.routes.items():
        nd = P.nets[net]
        planar_layers = set()
        vias = 0
        o = 0.0
        press = 0.0
        npl = 0
        for e, (kind, local) in r["edges"].items():
            if kind == "via":
                vias += 1
            else:
                L = e // (ny * (nx - 1)) if e < R.nH else (e - R.nH) // ((ny - 1) * nx)
                planar_layers.add(P.layers[L])
            if local:
                continue
            if e in ov:
                o += R.use[e] - R.cap[e]
            if kind != "via":
                npl += 1
                press = max(press, (R.use[e]) / max(R.cap[e], 0.5))
        node_layers = {P.layers[n // (nx * ny)] for n in r["nodes"]}
        allc = [(n % nx, (n // nx) % ny) for t in nd["terms"] for n in t]
        hp = (max(c[0] for c in allc) - min(c[0] for c in allc)) + (max(c[1] for c in allc) - min(c[1] for c in allc))
        length = sum(1 for e, (k, _) in r["edges"].items() if k != "via")
        crit = (1000.0 if not r["ok"] else 0.0) + 100.0 * o + 10.0 * press + length / max(hp, 1)
        out[net] = {"cells": sorted(r["nodes"]), "layers": sorted(planar_layers or node_layers), "vias": vias,
                    "overflow": round(o, 3), "pressure": round(press, 3), "length": length, "hpwl": hp,
                    "crit": round(crit, 3), "ok": r["ok"]}
    edges = []
    for e in sorted(ov):
        if e < R.nH:
            L, r_ = divmod(e, ny * (nx - 1))
            y, x = divmod(r_, nx - 1)
            kind = "H"
        elif e < R.nH + R.nV:
            L, r_ = divmod(e - R.nH, (ny - 1) * nx)
            y, x = divmod(r_, nx)
            kind = "V"
        else:
            L = -1
            y, x = divmod(e - R.nH - R.nV, nx)
            kind = "via"
        edges.append((kind, P.layers[L] if L >= 0 else "*", x, y, round(R.use[e], 2), R.cap[e]))
    no_acc = [k for k, v in P.access.items() if v.get("no_access")]
    return Result(out, edges, round(sum(u - c for _, _, _, _, u, c in edges), 2), it, round(time.time() - t0, 2),
                  P.build_s, no_acc)


def global_route(design, libs, board=None, parts=None, access: bool = False, nets=None,
                 pitch: float | None = None, iters: int = ITERS) -> Result:
    P = build(design, libs, board=board, parts=parts, access=access, nets=nets, pitch=pitch)
    return route(P, iters)


# ------------------------------------------------------------------ uses: order and layer hints


def order(res: Result, nets) -> list[str]:
    """`nets` most constrained first (criticality), the nets the global route does not know last, in given order."""
    nets = list(nets)
    known = [n for n in nets if n in res.nets]
    known.sort(key=lambda n: -res.nets[n]["crit"])
    return known + [n for n in nets if n not in res.nets]


def layer_hint(res: Result, net) -> tuple | None:
    """The one layer the global route keeps `net` on (no via, planar edges on one layer), else None."""
    r = res.nets.get(net)
    if not r or not r["ok"] or r["vias"] or len(r["layers"]) != 1 or r["overflow"] > 0:
        return None
    return tuple(r["layers"])


ORIGINAL = ["--ordering", "original"]


def variants(base: list) -> list:
    """The router's net orders with the global-route order (KRT `--ordering original` on the ordered --nets) first,
    the same number of tries."""
    rest = [v for v in base if v != ORIGINAL]
    return [ORIGINAL] + rest[:max(len(base) - 1, 0)]


def layer_hints(res: Result) -> dict:
    """net -> (layer,) for every net the global route keeps on one layer."""
    return {n: h for n in res.nets if (h := layer_hint(res, n))}


def for_router(design, libs, src, nets=None) -> Result | None:
    """The global route of the board file `src` for pcb.krt_route (None when it cannot be built: no hints).
    HWFLOW_GROUTE_ACCESS=1 starts the nets from their pin access points."""
    from pathlib import Path

    from . import pcb
    try:
        if libs is None:
            from .libs import Libs
            libs = Libs(design=design)
        res = global_route(design, libs, board=pcb.read_board(Path(src)), nets=nets,
                           access=os.environ.get("HWFLOW_GROUTE_ACCESS") == "1")
        print(f"[groute] {report(res, top=5)}")
        return res
    except Exception as e:  # noqa: BLE001 - experimental: never stops the router
        print(f"[groute] skipped: {type(e).__name__}: {e}")
        return None


def report(res: Result, top: int = 15) -> str:
    fail = res.failing()
    lines = [f"global route: {len(res.nets)} nets, {res.iterations} PathFinder iterations, build {res.build_s} s, "
             f"route {res.route_s} s; overflow {res.total_overflow} on {len(res.overflow_edges)} edges; "
             f"{len(fail)} nets do not fit"]
    for n in sorted(fail, key=lambda n: -res.nets[n]["crit"])[:top]:
        r = res.nets[n]
        lines.append(f"  {n}: overflow {r['overflow']}, layers {' '.join(r['layers'])}, "
                     f"{'NO PATH, ' if not r['ok'] else ''}crit {r['crit']}")
    for k, L, x, y, u, c in res.overflow_edges[:top]:
        lines.append(f"  edge {k} {L} cell ({x},{y}): use {u} > capacity {c:g}")
    if res.no_access:
        lines.append(f"  pads without access: {', '.join(f'{r}.{p}' for r, p in res.no_access[:top])}")
    return "\n".join(lines)


# ------------------------------------------------------------------ pin access


def pin_access(design, m, dirs: int = ACCESS_DIRS) -> dict:
    """(ref, pad) -> {net, points: [(layer, x, y)] at the net's width, floor: number of access points at the board
    minimum width, n: number of points at the net width, no_access}. See the module text."""
    t0 = time.time()
    from . import pcb
    classes = pcb.net_classes(design)
    track = float(design.board["track_mm"])
    floor = float(m.floor)
    from .barrier import HOLE_MM
    obst = [p["obst"] for p in m.pads]
    tree = STRtree(obst)
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
            if L not in m.net_copper(net) and not tht:
                pass
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


def access_summary(acc: dict, top: int = 20) -> list[dict]:
    """Pads ordered by fewest access points at the net width (a fraction of the directions tried)."""
    rows = [dict(ref=k[0], pad=k[1], **{x: v[x] for x in ("net", "n", "floor", "no_access", "dirs")})
            for k, v in acc.items()]
    rows.sort(key=lambda r: (r["n"] / max(r["dirs"], 1), r["floor"]))
    return rows[:top]


# ------------------------------------------------------------------ CLI: feasibility report


def main(argv=None):
    import argparse
    import json
    from pathlib import Path

    from . import pcb
    from .design import Design
    from .libs import Libs
    ap = argparse.ArgumentParser(prog="python -m hwflow.globalroute",
                                 description="Global route (feasibility) of a placed board")
    ap.add_argument("project")
    ap.add_argument("--board", help="placed .kicad_pcb (default build/<name>.kicad_pcb)")
    ap.add_argument("--access", action="store_true", help="start nets from pin access points")
    ap.add_argument("--pitch", type=float, default=None, help="g-cell side in mm (default: from the board rules)")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    d = Design.load(Path(a.project))
    libs = Libs(design=d)
    bp = Path(a.board) if a.board else Path(a.project) / "build" / f"{d.name}.kicad_pcb"
    res = global_route(d, libs, board=pcb.read_board(bp), access=a.access, pitch=a.pitch)
    print(report(res))
    if a.json:
        Path(a.json).write_text(json.dumps({"nets": {n: {k: v for k, v in r.items() if k != "cells"}
                                                     for n, r in res.nets.items()},
                                            "overflow_edges": res.overflow_edges, "failing": res.failing(),
                                            "build_s": res.build_s, "route_s": res.route_s,
                                            "no_access": res.no_access}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
