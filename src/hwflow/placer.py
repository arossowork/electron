"""Simulated-annealing placement with net length, decoupling, grouping and edge constraints."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

from .design import Design


def rot(x: float, y: float, deg: float) -> tuple[float, float]:
    """KiCad rotation (counter-clockwise on screen, y axis down)."""
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c


@dataclass
class Part:
    ref: str
    w: float          # courtyard size at rot 0 (with margin)
    h: float
    cx: float         # courtyard center offset from footprint origin at rot 0
    cy: float
    pads: dict        # pad number -> (x, y) at rot 0
    x: float = 0.0
    y: float = 0.0
    r: float = 0.0
    fixed: bool = False
    edge: str | None = None
    group: str | None = None
    decouples: str | None = None
    pad_size: dict | None = None  # pad number -> (w, h) at rot 0, for the copper-to-edge check
    sides: dict | None = None     # "front"/"back" -> (w, h, cx, cy) courtyard per board side

    def side_box(self, side: str):
        if not self.sides or side not in self.sides:
            return None
        w, h, cx, cy = self.sides[side]
        ox, oy = rot(cx, cy, self.r)
        if self.r % 180:
            w, h = h, w
        x, y = self.x + ox, self.y + oy
        return x - w / 2, y - h / 2, x + w / 2, y + h / 2

    def box(self):
        cx, cy = rot(self.cx, self.cy, self.r)
        w, h = (self.w, self.h) if self.r % 180 == 0 else (self.h, self.w)
        x, y = self.x + cx, self.y + cy
        return x - w / 2, y - h / 2, x + w / 2, y + h / 2

    def pad(self, n):
        px, py = self.pads[n]
        dx, dy = rot(px, py, self.r)
        return self.x + dx, self.y + dy


class Parts(dict):
    """ref -> Part, with the rule areas of the design's keep-out footprints (`areas`, see keepout.placement_areas)."""
    areas: list = ()


def build_parts(design: Design, libs, margin: float = 0.25) -> dict[str, Part]:
    parts = Parts()
    from . import keepout
    parts.areas = keepout.placement_areas(design, libs)
    for c in design.components.values():
        if c.get("virtual"):
            continue
        fp = libs.footprint(c.get("footprint"))
        x0, y0, x1, y1 = fp["bbox"]
        pads, sizes = {}, {}
        for p in fp["pads"]:
            if p["number"] and p["number"] not in pads:
                pads[p["number"]] = (p["x"], p["y"])
                w, h = (p["w"], p["h"]) if p["rot"] % 180 == 0 else (p["h"], p["w"])
                sizes[p["number"]] = (w, h)
        pl = c.get("placement") or {}
        sides = {k: (b[2] - b[0] + 2 * margin, b[3] - b[1] + 2 * margin, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
                 for k, b in fp.get("sides", {}).items()}
        part = Part(c.ref, x1 - x0 + 2 * margin, y1 - y0 + 2 * margin, (x0 + x1) / 2, (y0 + y1) / 2, pads,
                    edge=pl.get("edge"), group=c.get("group"), decouples=c.get("decouples"), pad_size=sizes,
                    sides=sides)
        if "x" in pl and "y" in pl:
            part.x, part.y, part.r = float(pl["x"]), float(pl["y"]), float(pl.get("rot", 0))
            part.fixed = bool(pl.get("fixed", True))
        elif "rot" in pl:
            part.r = float(pl["rot"])
        parts[c.ref] = part
    return parts


def _side_overlap(pa: "Part", pb: "Part") -> float:
    """Courtyard overlap on the same board side only (a back-side holder may sit under top parts)."""
    total = 0.0
    for side in ("front", "back"):
        ba, bb = pa.side_box(side), pb.side_box(side)
        if ba and bb:
            total += _overlap(ba, bb)
    return total


def _overlap(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    w = min(ax1, bx1) - max(ax0, bx0)
    h = min(ay1, by1) - max(ay0, by0)
    return w * h if w > 0 and h > 0 else 0.0


ISO_MARGIN = 0.5  # mm added to the creepage (else clearance) of a board.isolation group
CONG_UTIL = 0.4  # share of the theoretical track capacity of a cell the router can really use


_REPORTED: set = set()  # fixed-part violations already printed, once per process
REFINE = 0.2  # refine pass length, as a share of the per-seed iterations


class Placer:
    def __init__(self, design: Design, parts: dict[str, Part], width: float, height: float, seed: int = 1,
                 weights: dict | None = None, keepouts: list | None = None):
        self.d, self.parts, self.W, self.H = design, parts, width, height
        self.rng = random.Random(seed)
        self.keepouts = list(keepouts or [])  # (x0, y0, x1, y1, capacity fraction) boxes where tracks are not allowed
        self.wt = {"wire": 1.0, "overlap": 400.0, "bounds": 400.0, "decouple": 6.0, "group": 0.6, "edge": 30.0,
                   "congestion": 1.0, "keepout": 400.0, "isolation": 400.0}
        self.wt.update(weights or {})
        gnds = design.ground_nets()
        self.nets = []
        self._net_rudy = []
        for net, conns in design.nets().items():
            conns = [(r, p) for r, p in conns if r in parts and p in parts[r].pads]
            if len(conns) < 2:
                continue
            info = design.net_info(net)
            w = 0.15 if net in gnds and design.board["gnd_pour"] else float(info.get("place_weight", 1.0))
            if info.get("class") == "power":
                w *= 1.3
            self.nets.append((w, conns))
            self._net_rudy.append(not (net in gnds and design.board["gnd_pour"]))  # a pour needs no tracks
        # decoupling pairs: (cap ref, cap pad, ic ref, ic pad) on the shared supply net
        self.decap = []
        for p in parts.values():
            if p.decouples and p.decouples in parts:
                cap = design.components[p.ref]
                ic = design.components[p.decouples]
                for cpin, net in cap.pins.items():
                    if net in gnds:
                        continue
                    for ipin, inet in ic.pins.items():
                        if inet == net and ipin in parts[ic.ref].pads:
                            self.decap.append((p.ref, cpin, ic.ref, ipin))
        # indexes for the incremental cost
        self.order = {r: i for i, r in enumerate(parts)}
        self.part_nets = {r: [] for r in parts}
        for k, (_, conns) in enumerate(self.nets):
            for r in {r for r, _ in conns}:
                self.part_nets[r].append(k)
        self.part_decs = {r: [] for r in parts}
        for k, (cr, _, ir, _) in enumerate(self.decap):
            self.part_decs[cr].append(k)
            self.part_decs[ir].append(k)
        self.group_members: dict[str, list] = {}
        for p in parts.values():
            if p.group:
                self.group_members.setdefault(p.group, []).append(p)
        self.bucket = 4.0  # mm, spatial hash cell for the overlap pairs
        self._init_rules(getattr(parts, "areas", ()))

    def _init_rules(self, areas):
        """Keep-out areas (courtyards and pads forbidden, tracks removed from the congestion capacity) and the pad
        groups of board.isolation (a pad of a group keeps creepage + ISO_MARGIN to the pads of every other net)."""
        nl = max(1, int(self.d.board.get("layers", 2)))
        self.ko_fp, self.ko_pad = [], []  # (box, board sides, owner, name)
        for a in areas:
            lay = a["layers"]
            sides = {s for s, L in (("front", "F.Cu"), ("back", "B.Cu")) if L in lay or "*.Cu" in lay}
            if "footprints" in a["forbids"]:
                self.ko_fp.append((a["box"], sides, a["owner"], a["name"]))
            elif "pads" in a["forbids"]:
                self.ko_pad.append((a["box"], sides, a["owner"], a["name"]))
            if "tracks" in a["forbids"]:
                n = nl if "*.Cu" in lay else len([L for L in lay if L.endswith(".Cu")])
                self.keepouts.append((*a["box"], max(0.0, 1 - n / nl)))
        from . import pcb
        net_of = {(r, p): n for n, conns in self.d.nets().items() for r, p in conns}
        self.iso_need, gid = [], {}
        for g in pcb.isolation_groups(self.d):
            need = g["creepage_mm"] if g["creepage_mm"] is not None else g["clearance_mm"]
            if need is not None and g["members"]:
                for n in g["members"]:
                    gid.setdefault(n, len(self.iso_need))
                self.iso_need.append(float(need) + ISO_MARGIN)
        self.pad_grp = {}  # (ref, pad) -> (group index or -1, net); only pads with a net
        self.iso_parts = set()
        for (r, p), n in net_of.items():
            if r in self.parts and p in self.parts[r].pads:
                self.pad_grp[(r, p)] = gid.get(n, -1)
                if n in gid:
                    self.iso_parts.add(r)
        if not self.iso_need:
            self.pad_grp, self.iso_parts = {}, set()
        self.part_pads = {r: [k for k in self.pad_grp if k[0] == r] for r in self.parts} if self.iso_need else {}

    # ---- congestion (RUDY) ------------------------------------------------------------------------------------

    def _init_grid(self):
        b = self.d.board
        self.cell = 1.0
        self.nx, self.ny = max(1, math.ceil(self.W / self.cell)), max(1, math.ceil(self.H / self.cell))
        pitch = float(b["track_mm"]) + float(b["clearance_mm"])
        # mm of wire a cell can carry: layers x cell area / pitch, derated for vias, pads and routing inefficiency
        per_cell = max(1, int(b.get("layers", 2))) * self.cell * self.cell / pitch * CONG_UTIL
        self.cap = np.full((self.nx, self.ny), per_cell)
        for x0, y0, x1, y1, frac in self.keepouts:  # frac of the capacity left inside the area (0 = no tracks)
            i0, i1 = max(0, int(x0 // self.cell)), min(self.nx, math.ceil(x1 / self.cell))
            j0, j1 = max(0, int(y0 // self.cell)), min(self.ny, math.ceil(y1 / self.cell))
            self.cap[i0:i1, j0:j1] *= frac
        self.grid = np.zeros((self.nx, self.ny))

    def _rudy(self, xs, ys):
        """(i0, j0, demand array) of a net: HPWL spread uniformly over its bounding box, in mm of wire per cell."""
        c = self.cell
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        hp = (x1 - x0) + (y1 - y0)
        if x1 - x0 < c:
            x0, x1 = (x0 + x1) / 2 - c / 2, (x0 + x1) / 2 + c / 2
        if y1 - y0 < c:
            y0, y1 = (y0 + y1) / 2 - c / 2, (y0 + y1) / 2 + c / 2
        dens = hp / ((x1 - x0) * (y1 - y0))
        i0 = min(self.nx - 1, max(0, int(x0 // c)))
        i1 = min(self.nx, max(i0 + 1, math.ceil(x1 / c)))
        j0 = min(self.ny - 1, max(0, int(y0 // c)))
        j1 = min(self.ny, max(j0 + 1, math.ceil(y1 / c)))
        ex, ey = np.arange(i0, i1 + 1) * c, np.arange(j0, j1 + 1) * c
        ox = np.clip(np.minimum(ex[1:], x1) - np.maximum(ex[:-1], x0), 0, None)
        oy = np.clip(np.minimum(ey[1:], y1) - np.maximum(ey[:-1], y0), 0, None)
        return i0, j0, dens * np.outer(ox, oy)

    def _grid_add(self, i0, j0, dem, sign):
        sl = (slice(i0, i0 + dem.shape[0]), slice(j0, j0 + dem.shape[1]))
        g, cp = self.grid[sl], self.cap[sl]
        o = np.maximum(g - cp, 0).ravel()
        before = o @ o
        g += sign * dem
        o = np.maximum(g - cp, 0).ravel()
        self.t_cong += o @ o - before

    def congestion(self) -> float:
        """Sum over cells of (wire demand above capacity)^2, from scratch."""
        grid = np.zeros((self.nx, self.ny))
        for k, (w, conns) in enumerate(self.nets):
            if self._net_rudy[k]:
                xs, ys = zip(*(self.parts[r].pad(p) for r, p in conns))
                i0, j0, dem = self._rudy(xs, ys)
                grid[i0:i0 + dem.shape[0], j0:j0 + dem.shape[1]] += dem
        return float(np.square(np.clip(grid - self.cap, 0, None)).sum())

    def max_overflow(self) -> float:
        """Largest per-cell wire demand above capacity (mm of wire), from scratch."""
        self.cost()
        return float(np.clip(self.grid - self.cap, 0, None).max())

    # ---- cost -------------------------------------------------------------------------------------------------

    def _net_len(self, k):
        P = self.parts
        xs, ys = zip(*(P[r].pad(p) for r, p in self.nets[k][1]))
        return self.nets[k][0] * ((max(xs) - min(xs)) + (max(ys) - min(ys))), xs, ys

    def _part_cost(self, r):
        """bounds + edge terms of one part (unweighted pair: (bounds, edge))."""
        p = self.parts[r]
        m = self.d.board["margin_mm"]
        x0, y0, x1, y1 = p.box()
        bnd = max(0, m - x0) + max(0, m - y0) + max(0, x1 - self.W + m) + max(0, y1 - self.H + m)
        edge = 0.0
        if p.edge:
            edge = abs({"left": x0 - m, "right": self.W - m - x1, "top": y0 - m, "bottom": self.H - m - y1}[p.edge])
        return bnd, edge

    def _ko_cost(self, r):
        """Courtyard area of `r` inside the areas that forbid footprints, plus pad area inside those that forbid pads."""
        p = self.parts[r]
        c = 0.0
        for box, sides, owner, _ in self.ko_fp:
            if owner != r:
                for s in sides:
                    b = p.side_box(s)
                    if b:
                        c += _overlap(b, box)
        for box, sides, owner, _ in self.ko_pad:
            if owner != r and sides & set(p.sides or ()):
                for n in p.pads:
                    c += _overlap(self._pad_box(p, n), box)
        return c

    def _boxes(self, p):
        return [b for b in (p.side_box("front"), p.side_box("back")) if b] + [self._pad_box(p, n) for n in p.pads]

    @staticmethod
    def _pad_box(p, n):
        x, y = p.pad(n)
        w, h = (p.pad_size or {}).get(n, (0.0, 0.0))
        if p.r % 180:
            w, h = h, w
        return x - w / 2, y - h / 2, x + w / 2, y + h / 2

    def _iso_short(self, a, b):
        """Shortfall (mm) of the copper gap between pads a and b (ref, pad) below the creepage need; 0 if they are
        in the same group, on the same part, or no group is involved."""
        ga, gb = self.pad_grp[a], self.pad_grp[b]
        if ga == gb or a[0] == b[0]:
            return 0.0
        need = max(self.iso_need[g] for g in (ga, gb) if g >= 0)
        ba, bb = self._pad_box(self.parts[a[0]], a[1]), self._pad_box(self.parts[b[0]], b[1])
        gap = math.hypot(max(0.0, ba[0] - bb[2], bb[0] - ba[2]), max(0.0, ba[1] - bb[3], bb[1] - ba[3]))
        return max(0.0, need - gap)

    def _iso_scan(self, r, log, skip=()):
        if not self.iso_parts:
            return
        cand = set(self.iso_parts)
        if r in self.iso_parts:  # a pad of a group: every part within reach, from the spatial hash
            B, reach = self.bucket, max(self.iso_need) + 1.0
            for a in self.part_pads[r]:
                if self.pad_grp[a] >= 0:
                    x, y = self.parts[r].pad(a[1])
                    for i in range(int((x - reach) // B), int((x + reach) // B) + 1):
                        for j in range(int((y - reach) // B), int((y + reach) // B) + 1):
                            cand |= self.buckets.get((i, j), set())
        for q in cand:
            if q == r or q in skip:
                continue
            for a in self.part_pads[r]:
                for b in self.part_pads[q]:
                    v = self._iso_short(a, b)
                    if v > 0:
                        key = (a, b) if (self.order[r], a[1]) < (self.order[q], b[1]) else (b, a)
                        if key not in self.iso_pairs:
                            self.iso_pairs[key] = v
                            self.iso_adj[r].add(key)
                            self.iso_adj[q].add(key)
                            self.t_iso += v
                            if log is not None:
                                log.append(("iso_add", key, v))

    def _decap_len(self, k):
        cr, cp, ir, ip = self.decap[k]
        ax, ay = self.parts[cr].pad(cp)
        bx, by = self.parts[ir].pad(ip)
        return math.hypot(ax - bx, ay - by)

    def _group_cost(self, g):
        grp = self.group_members[g]
        mx = sum(p.x for p in grp) / len(grp)
        my = sum(p.y for p in grp) / len(grp)
        return sum(abs(p.x - mx) + abs(p.y - my) for p in grp)

    def _buckets(self, r):
        p = self.parts[r]
        bs = [b for b in (p.side_box("front"), p.side_box("back")) if b]
        if not bs:
            return ()
        B = self.bucket
        x0, y0 = min(b[0] for b in bs), min(b[1] for b in bs)
        x1, y1 = max(b[2] for b in bs), max(b[3] for b in bs)
        return [(i, j) for i in range(int(x0 // B), int(x1 // B) + 1) for j in range(int(y0 // B), int(y1 // B) + 1)]

    def _init_state(self):
        """Build every cached cost term from the current positions. Totals are unweighted sums per term."""
        P = self.parts
        self._init_grid()
        self.t_cong = 0.0
        self.net_len, self.net_dem = [0.0] * len(self.nets), [None] * len(self.nets)
        for k in range(len(self.nets)):
            self.net_len[k], xs, ys = self._net_len(k)
            if self._net_rudy[k]:
                self.net_dem[k] = self._rudy(xs, ys)
                self._grid_add(*self.net_dem[k], 1)
        self.t_wire = sum(self.net_len)
        self.part_bnd, self.part_edge = {}, {}
        for r in P:
            self.part_bnd[r], self.part_edge[r] = self._part_cost(r)
        self.t_bnd, self.t_edge = sum(self.part_bnd.values()), sum(self.part_edge.values())
        self.dec_len = [self._decap_len(k) for k in range(len(self.decap))]
        self.t_dec = sum(self.dec_len)
        self.grp_cost = {g: self._group_cost(g) for g in self.group_members}
        self.t_grp = sum(self.grp_cost.values())
        self.part_ko = {r: self._ko_cost(r) for r in P}
        self.t_ko = sum(self.part_ko.values())
        self.iso_pairs, self.iso_adj, self.t_iso = {}, {r: set() for r in P}, 0.0
        self.pairs, self.adj, self.t_ov = {}, {r: set() for r in P}, 0.0
        self.buckets, self.part_buckets = {}, {}
        for r in P:
            self.part_buckets[r] = self._buckets(r)
            for bk in self.part_buckets[r]:
                self.buckets.setdefault(bk, set()).add(r)
        for r in P:
            self._pair_scan(r, None)
        for r in P:
            self._iso_scan(r, None)

    def _pair_scan(self, r, log, skip=()):
        for q in {q for bk in self.part_buckets[r] for q in self.buckets[bk]}:
            if q == r or q in skip:
                continue
            v = _side_overlap(self.parts[r], self.parts[q])
            if v > 0:
                key = (r, q) if self.order[r] < self.order[q] else (q, r)
                if key not in self.pairs:
                    self.pairs[key] = v
                    self.adj[r].add(key)
                    self.adj[q].add(key)
                    self.t_ov += v
                    if log is not None:
                        log.append(("pair_add", key, v))

    def _update(self, moved):
        """Recompute only what the parts in `moved` touch. Returns the new cost; `_undo` reverts it."""
        log = []
        saved = (self.t_wire, self.t_cong, self.t_ov, self.t_bnd, self.t_edge, self.t_dec, self.t_grp, self.t_ko,
                 self.t_iso)
        nets, decs, groups = set(), set(), set()
        for r in moved:
            nets.update(self.part_nets[r])
            decs.update(self.part_decs[r])
            if self.parts[r].group:
                groups.add(self.parts[r].group)
        for k in nets:
            old_len, old_dem = self.net_len[k], self.net_dem[k]
            self.net_len[k], xs, ys = self._net_len(k)
            self.t_wire += self.net_len[k] - old_len
            if self._net_rudy[k]:
                new_dem = self._rudy(xs, ys)
                if new_dem[:2] != old_dem[:2] or not np.array_equal(new_dem[2], old_dem[2]):
                    self._grid_add(*old_dem, -1)
                    self._grid_add(*new_dem, 1)
                    self.net_dem[k] = new_dem
            log.append(("net", k, old_len, old_dem))
        for r in moved:
            ob, oe = self.part_bnd[r], self.part_edge[r]
            self.part_bnd[r], self.part_edge[r] = self._part_cost(r)
            self.t_bnd += self.part_bnd[r] - ob
            self.t_edge += self.part_edge[r] - oe
            log.append(("part", r, ob, oe))
            if self.ko_fp or self.ko_pad:
                ok = self.part_ko[r]
                self.part_ko[r] = self._ko_cost(r)
                self.t_ko += self.part_ko[r] - ok
                log.append(("ko", r, ok))
        for k in decs:
            old = self.dec_len[k]
            self.dec_len[k] = self._decap_len(k)
            self.t_dec += self.dec_len[k] - old
            log.append(("dec", k, old))
        for g in groups:
            old = self.grp_cost[g]
            self.grp_cost[g] = self._group_cost(g)
            self.t_grp += self.grp_cost[g] - old
            log.append(("grp", g, old))
        for r in moved:  # drop the old pairs and buckets, then rescan
            for key in list(self.iso_adj[r]):
                v = self.iso_pairs.pop(key)
                self.iso_adj[key[0][0]].discard(key)
                self.iso_adj[key[1][0]].discard(key)
                self.t_iso -= v
                log.append(("iso_del", key, v))
            for key in list(self.adj[r]):
                v = self.pairs.pop(key)
                self.adj[key[0]].discard(key)
                self.adj[key[1]].discard(key)
                self.t_ov -= v
                log.append(("pair_del", key, v))
            old_b = self.part_buckets[r]
            for bk in old_b:
                self.buckets[bk].discard(r)
            self.part_buckets[r] = self._buckets(r)
            for bk in self.part_buckets[r]:
                self.buckets.setdefault(bk, set()).add(r)
            log.append(("bucket", r, old_b))
        done = set()
        for r in moved:
            done.add(r)
            self._pair_scan(r, log, skip=done)
        done = set()
        for r in moved:
            done.add(r)
            self._iso_scan(r, log, skip=done)
        self._log, self._saved = log, saved
        return self._total()

    def _undo(self):
        for e in reversed(self._log):
            kind = e[0]
            if kind == "net":
                _, k, ol, od = e
                if self._net_rudy[k] and self.net_dem[k] is not od:
                    self._grid_add(*self.net_dem[k], -1)
                    self._grid_add(*od, 1)
                self.net_len[k], self.net_dem[k] = ol, od
            elif kind == "part":
                self.part_bnd[e[1]], self.part_edge[e[1]] = e[2], e[3]
            elif kind == "ko":
                self.part_ko[e[1]] = e[2]
            elif kind == "iso_add":
                key = e[1]
                del self.iso_pairs[key]
                self.iso_adj[key[0][0]].discard(key)
                self.iso_adj[key[1][0]].discard(key)
            elif kind == "iso_del":
                key = e[1]
                self.iso_pairs[key] = e[2]
                self.iso_adj[key[0][0]].add(key)
                self.iso_adj[key[1][0]].add(key)
            elif kind == "dec":
                self.dec_len[e[1]] = e[2]
            elif kind == "grp":
                self.grp_cost[e[1]] = e[2]
            elif kind == "pair_add":
                key = e[1]
                del self.pairs[key]
                self.adj[key[0]].discard(key)
                self.adj[key[1]].discard(key)
            elif kind == "pair_del":
                key = e[1]
                self.pairs[key] = e[2]
                self.adj[key[0]].add(key)
                self.adj[key[1]].add(key)
            elif kind == "bucket":
                r = e[1]
                for bk in self.part_buckets[r]:
                    self.buckets[bk].discard(r)
                self.part_buckets[r] = e[2]
                for bk in e[2]:
                    self.buckets.setdefault(bk, set()).add(r)
        (self.t_wire, self.t_cong, self.t_ov, self.t_bnd, self.t_edge, self.t_dec, self.t_grp, self.t_ko,
         self.t_iso) = self._saved

    def _total(self) -> float:
        w = self.wt
        return (w["wire"] * self.t_wire + w["overlap"] * self.t_ov + w["bounds"] * self.t_bnd
                + w["edge"] * self.t_edge + w["decouple"] * self.t_dec + w["group"] * self.t_grp
                + w["congestion"] * self.t_cong + w["keepout"] * self.t_ko + w["isolation"] * self.t_iso)

    def cost(self) -> float:
        """Cost of the current placement; rebuilds the incremental state from scratch."""
        self._init_state()
        return self._total()

    def cost_full(self) -> float:
        """Reference cost, recomputed without the grid, caches or incremental updates (O(n^2) overlap loop)."""
        P = self.parts
        c = 0.0
        for w, conns in self.nets:
            xs, ys = zip(*(P[r].pad(p) for r, p in conns))
            c += self.wt["wire"] * w * ((max(xs) - min(xs)) + (max(ys) - min(ys)))
        refs = list(P)
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                c += self.wt["overlap"] * _side_overlap(P[a], P[b])
            bnd, edge = self._part_cost(a)
            c += self.wt["bounds"] * bnd + self.wt["edge"] * edge
        for cr, cp, ir, ip in self.decap:
            ax, ay = P[cr].pad(cp)
            bx, by = P[ir].pad(ip)
            c += self.wt["decouple"] * math.hypot(ax - bx, ay - by)
        for g in self.group_members.values():
            mx = sum(p.x for p in g) / len(g)
            my = sum(p.y for p in g) / len(g)
            c += self.wt["group"] * sum(abs(p.x - mx) + abs(p.y - my) for p in g)
        for r in P:
            c += self.wt["keepout"] * self._ko_cost(r)
        keys = list(self.pad_grp)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                c += self.wt["isolation"] * self._iso_short(a, b)
        return c + self.wt["congestion"] * self.congestion()

    def run(self, iters: int = 30000) -> float:
        movable = [p for p in self.parts.values() if not p.fixed]
        for p in movable:
            p.x = self.rng.uniform(self.W * 0.2, self.W * 0.8)
            p.y = self.rng.uniform(self.H * 0.2, self.H * 0.8)
        if not movable:
            return self.cost()
        self._anneal(movable, iters, max(self.W, self.H) * 2, 0.01)
        return self._finish(movable)

    def refine(self, iters: int, t0: float = 0.6) -> float:
        """Short low-temperature anneal from the current placement (weights as the last run left them)."""
        movable = [p for p in self.parts.values() if not p.fixed]
        if not movable or iters <= 0:
            return self.cost()
        self._anneal(movable, iters, t0, 0.01, ramp=False)
        return self._finish(movable)

    def _anneal(self, movable, iters, t0, t1, ramp=True):
        cur = self.cost()
        best, best_state = cur, self._state()
        base = {k: self.wt[k] for k in ("overlap", "bounds", "keepout", "isolation")}
        for i in range(iters):
            t = t0 * (t1 / t0) ** (i / iters)
            if i % 500 == 0:
                if ramp:
                    f = 1 + 20 * i / iters
                    for k, v in base.items():
                        self.wt[k] = v * f
                cur = self.cost()  # also resets the incremental drift
                best, best_state = cur, self._state()  # weights changed: costs are not comparable
            p = self.rng.choice(movable)
            old = (p.x, p.y, p.r)
            mv = self.rng.random()
            other = None
            if mv < 0.75:
                s = max(0.3, t)
                p.x = min(self.W, max(0, p.x + self.rng.gauss(0, s)))
                p.y = min(self.H, max(0, p.y + self.rng.gauss(0, s)))
            elif mv < 0.9:
                p.r = (p.r + self.rng.choice((90, 180, 270))) % 360
            else:
                other = self.rng.choice(movable)
                oold = (other.x, other.y)
                p.x, p.y, other.x, other.y = other.x, other.y, p.x, p.y
            new = self._update([p.ref, other.ref] if other else [p.ref])
            if new <= cur or self.rng.random() < math.exp(-(new - cur) / max(t, 1e-9)):
                cur = new
                if cur < best:
                    best, best_state = cur, self._state()
            else:
                p.x, p.y, p.r = old
                if other:
                    other.x, other.y = oold
                self._undo()
        self._restore(best_state)

    def _finish(self, movable):
        self.legalize()
        for p in movable:  # snap to 0.05 mm grid
            p.x, p.y = round(p.x / 0.05) * 0.05, round(p.y / 0.05) * 0.05
        self.legalize()
        return self.cost()

    def legalize(self, rounds: int = 200):
        """Push overlapping courtyards apart along the axis of least overlap, then clamp into the board."""
        P = [p for p in self.parts.values()]
        m = self.d.board["margin_mm"]
        for _ in range(rounds):
            moved = False
            boxes = {p.ref: {s: p.side_box(s) for s in ("front", "back")} for p in P}
            grid: dict = {}
            B = self.bucket
            for i, a in enumerate(P):  # spatial hash, boxes grown by 1 mm for the moves made during the round
                for bx in boxes[a.ref].values():
                    if bx:
                        for gi in range(int((bx[0] - 1) // B), int((bx[2] + 1) // B) + 1):
                            for gj in range(int((bx[1] - 1) // B), int((bx[3] + 1) // B) + 1):
                                grid.setdefault((gi, gj), set()).add(i)
            for i, a in enumerate(P):
                near = set()
                for bx in boxes[a.ref].values():
                    if bx:
                        for gi in range(int((bx[0] - 1) // B), int((bx[2] + 1) // B) + 1):
                            for gj in range(int((bx[1] - 1) // B), int((bx[3] + 1) // B) + 1):
                                near |= grid.get((gi, gj), set())
                for j in sorted(near):
                    if j <= i:
                        continue
                    b = P[j]
                    pair = None
                    for side in ("front", "back"):  # only courtyards on the same side collide
                        ba, bb = a.side_box(side), b.side_box(side)
                        if ba and bb and _overlap(ba, bb) > 0:
                            pair = (ba, bb)
                            break
                    if pair is None:
                        continue
                    ba, bb = pair
                    ox = min(ba[2], bb[2]) - max(ba[0], bb[0])
                    oy = min(ba[3], bb[3]) - max(ba[1], bb[1])
                    if ox <= 0.001 or oy <= 0.001:
                        continue
                    moved = True
                    mover = b if not b.fixed else a
                    sign_src = (bb[0] + bb[2] - ba[0] - ba[2]) if ox < oy else (bb[1] + bb[3] - ba[1] - ba[3])
                    sgn = 1 if sign_src >= 0 else -1
                    if mover is a:
                        sgn = -sgn
                    if a.fixed and b.fixed:
                        continue
                    if ox < oy:
                        mover.x += sgn * (ox + 0.05)
                    else:
                        mover.y += sgn * (oy + 0.05)
            for p in P:  # out of the areas that forbid courtyards, along the axis of least penetration
                if p.fixed:
                    continue
                for box, sides, owner, _ in self.ko_fp:
                    for side in sides:
                        b = p.side_box(side)
                        if owner == p.ref or not b or _overlap(b, box) <= 1e-6:
                            continue
                        moved = True
                        opts = [(box[0] - b[2] - 0.05, 0), (box[2] - b[0] + 0.05, 0),
                                (0, box[1] - b[3] - 0.05), (0, box[3] - b[1] + 0.05)]
                        dx, dy = min(opts, key=lambda o: abs(o[0]) + abs(o[1]))
                        p.x, p.y = p.x + dx, p.y + dy
            for p in P:
                if p.fixed:
                    continue
                x0, y0, x1, y1 = p.box()
                if x0 < m:
                    p.x += m - x0
                if x1 > self.W - m:
                    p.x -= x1 - (self.W - m)
                if y0 < m:
                    p.y += m - y0
                if y1 > self.H - m:
                    p.y -= y1 - (self.H - m)
            if not moved:
                break

    def violations(self) -> list[str]:
        P = self.parts
        out = []
        refs = list(P)
        boxes = {r: P[r].box() for r in refs}
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                if _side_overlap(P[a], P[b]) > 0.01:
                    out.append(f"courtyard overlap {a}/{b}")
            # copper must stay inside the board with the copper-to-edge clearance; a courtyard (body)
            # may overhang on purpose, for example a connector flush with the edge
            ce = float(self.d.board.get("copper_edge_mm", 0.3))
            part = P[a]
            for n in part.pads:
                px, py = part.pad(n)
                w, h = (part.pad_size or {}).get(n, (0.0, 0.0))
                if part.r % 180:
                    w, h = h, w
                if px - w / 2 < ce - 0.01 or py - h / 2 < ce - 0.01 or px + w / 2 > self.W - ce + 0.01 \
                        or py + h / 2 > self.H - ce + 0.01:
                    out.append(f"{a} pad {n} copper closer than {ce} mm to the board edge")
                    break
        mv, fx = self._rule_violations()
        return out + mv

    def _rule_violations(self):
        """(movable, fixed-only) keep-out and isolation violations of the current placement. A violation that involves
        only fixed parts cannot be repaired by the placer: it is listed apart and never makes placement fail."""
        P = self.parts
        mv, fx = [], []
        for r in P:
            if self._ko_cost(r) > 0.01:
                names = [n for box, sides, o, n in self.ko_fp + self.ko_pad if o != r and any(
                    _overlap(bx, box) > 0 for bx in self._boxes(P[r]))]
                (fx if P[r].fixed else mv).append(f"{r} courtyard or pad in keep-out '{names[0] if names else '?'}'")
        keys = list(self.pad_grp)
        seen = set()
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                v = self._iso_short(a, b)
                if v > 0.01 and (a[0], b[0]) not in seen:
                    seen.add((a[0], b[0]))
                    both_fixed = P[a[0]].fixed and P[b[0]].fixed
                    (fx if both_fixed else mv).append(f"isolation {a[0]}.{a[1]}/{b[0]}.{b[1]}: {v:.2f} mm short of creepage")
        return mv, fx

    def fixed_violations(self) -> list[str]:
        """Keep-out and isolation violations caused only by fixed parts (not moved, not counted as failures)."""
        return self._rule_violations()[1]

    def _state(self):
        return {r: (p.x, p.y, p.r) for r, p in self.parts.items()}

    def _restore(self, s):
        for r, (x, y, a) in s.items():
            self.parts[r].x, self.parts[r].y, self.parts[r].r = x, y, a


def place(design: Design, libs, seeds: int = 4, iters: int = 30000, weights=None, seed0: int = 1,
          refine: float = REFINE):
    """Best of several seeds (seed0 .. seed0 + seeds - 1), then a short low-temperature refine pass of
    `refine * iters` moves on the best one (taken out of the seeds' budget). Returns (parts, cost, violations)."""
    b = design.board
    best = None
    extra = int(iters * refine)  # the refine pass is paid for by shortening the seeds, so total moves stay the same
    iters -= extra // seeds
    for seed in range(seed0, seed0 + seeds):
        parts = build_parts(design, libs)
        pl = Placer(design, parts, b["width_mm"], b["height_mm"], seed=seed, weights=weights)
        cost = pl.run(iters)
        v = pl.violations()
        key = (len(v), cost)
        if best is None or key < best[0]:
            best = (key, parts, cost, v, pl)
    key, parts, cost, v, pl = best
    for f in pl.fixed_violations():
        if f not in _REPORTED:
            _REPORTED.add(f)
            print(f"[place] fixed parts: {f}")
    if refine > 0 and any(not p.fixed for p in parts.values()):
        before = pl._state()
        cost2 = pl.refine(extra)
        v2 = pl.violations()
        if (len(v2), cost2) <= key:
            cost, v = cost2, v2
        else:
            pl._restore(before)
    return parts, cost, v
