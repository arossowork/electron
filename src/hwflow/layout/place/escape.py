"""Escape slots of every part, a placer cost term (prototype, off by default: HWFLOW_PLACE_ESCAPE=1 or
`placement: {escape: true}`; docs/layout-flow.md "Escape-aware placement").

Pin-access-aware detailed placement in the style of the ASIC papers (DATE'22, ASP-DAC'13): the placer sees, per pad,
whether a track can still leave it once the neighbours are placed.

Precomputed once per part, in footprint coordinates (rotation 0):

- an escape slot (`stub`) per pad with a net that leaves the part and per axis direction (4): the box a track of
  the net's width needs from the pad centre to `depth` beyond the part's pad hull, grown by the net's clearance on
  both sides (foreign copper may not enter it). `depth` is via_dia + clearance (room to turn or to drop a via).
- a slot is *own-open* when no foreign pad of the same part enters it (the footprint alone). Pads without an
  own-open slot (an inner QFN pad) are partescape.py's business (a via in the pocket) and are left out.

At a placement, a slot is blocked in proportion to what enters it (`GAIN` x covered share, at most 1):
foreign pads of other parts on a shared layer (grown by the isolation distance beyond the clearance: an isolation
bubble), the slots of other parts' pads of another net (the two fans compete for the same room, `FAN_SHARE`),
rule areas that forbid tracks on every layer the slot may use, and the board outside the copper-edge distance.
A pad costs its least blocked slot (one free direction is enough); a part costs the sum over its pads.

Demand (step 2, `demand`): a part whose pads have fewer free slots than they need gets its courtyard inflated in the
global placement's density feedback (asicflow._feedback), and `channels` reports the gaps between facing slot rows
that are narrower than the tracks that must pass (needed tracks x (width + clearance)).

Nothing here is specific to a design: every input is the footprints, the net classes, the isolation groups, the
rule areas and the board outline."""
from __future__ import annotations

import math

import numpy as np

from ...core.settings import get_settings

GAIN = 3.0       # a slot is fully blocked when a third of its area is covered
FAN_SHARE = 0.5  # another part's slot of another net counts half (two fans share the room, they do not wall it)
DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))


def _flag(v) -> bool:
    return str(v).strip().lower() not in ("0", "false", "no", "off", "", "none")


def on(design=None) -> bool:
    """Step 1, the escape slot term (HWFLOW_PLACE_ESCAPE, or placement: {escape: true})."""
    v = get_settings().layout.place_escape
    if v is None:
        v = ((getattr(design, "raw", None) or {}).get("placement") or {}).get("escape", "0")
    return _flag(v)


def on_step(design, name: str) -> bool:
    """Step 2 (`infl`: demand inflation and the channel check, HWFLOW_PLACE_ESCAPE_INFL) or step 3 (`rot`: forward
    check of the rotations, HWFLOW_PLACE_ESCAPE_ROT); or placement: {escape_infl, escape_rot: true}."""
    v = getattr(get_settings().layout, f"place_escape_{name}")
    if v is None:
        v = ((getattr(design, "raw", None) or {}).get("placement") or {}).get(f"escape_{name}", "0")
    return _flag(v)


def _glob(lx0, ly0, lx1, ly1, x, y, r):
    """Global boxes of local boxes (rotation 0, footprint origin) of a part at x, y, r (r rounded to 90 degrees)."""
    q = int(round((r % 360) / 90.0)) % 4
    a = q * 90.0
    cx, cy = (lx0 + lx1) / 2, (ly0 + ly1) / 2
    hw, hh = (lx1 - lx0) / 2, (ly1 - ly0) / 2
    c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
    gx, gy = cx * c + cy * s, -cx * s + cy * c  # geometry.rot
    if q % 2:
        hw, hh = hh, hw
    return np.stack([x + gx - hw, y + gy - hh, x + gx + hw, y + gy + hh], axis=-1)


def _ov(a, b):
    """Overlap areas of boxes a (S, 4) against b (M, 4): (S, M)."""
    ox = np.minimum(a[:, None, 2], b[None, :, 2]) - np.maximum(a[:, None, 0], b[None, :, 0])
    oy = np.minimum(a[:, None, 3], b[None, :, 3]) - np.maximum(a[:, None, 1], b[None, :, 1])
    return np.clip(ox, 0, None) * np.clip(oy, 0, None)


class Escape:
    """The escape slots of the parts of placer `pl` and their blockage at its current positions (incremental)."""

    def __init__(self, pl):
        from ..board import pcb
        from ...core.geometry import cu_layers
        d, P = pl.d, pl.parts
        self.pl = pl
        b = d.board
        track, clr0 = float(b["track_mm"]), float(b["clearance_mm"])
        cls = pcb.net_classes(d)
        layers = cu_layers(int(b.get("layers", 2)))
        allm = (1 << len(layers)) - 1
        self.depth = float(b["via_dia_mm"]) + clr0
        self.edge = float(b.get("copper_edge_mm", 0.3))
        nets = d.nets()
        net_of = {(r, p): n for n, conns in nets.items() for r, p in conns}
        nid = {n: i for i, n in enumerate(sorted(nets))}
        leaves = {n for n, conns in nets.items() if len({r for r, _ in conns}) > 1}
        gid, mm, btw = {}, [], []
        for g in pcb.isolation_needs(d):
            for n in g["members"]:
                gid.setdefault(n, len(mm))
            mm.append(float(g["mm"]))
            btw.append(float(g["between"] or 0.0))
        self.iso_mm, self.iso_btw = np.array(mm + [0.0]), np.array(btw + [0.0])

        def mask(part, pn):
            cu = part.cu.get(pn) if part.cu else None
            if cu is None or "*" in cu:
                return allm
            return sum(1 << i for i, L in enumerate(layers) if L in cu) or allm

        self.refs = [r for r in P if r in d.components and P[r].pads]
        self.ri = {r: i for i, r in enumerate(self.refs)}
        pad_rows, stub_rows = [], []  # local boxes and attributes
        for r in self.refs:
            p = P[r]
            own = []
            for pn, (px, py) in p.pads.items():
                if pn in p.nocu:
                    continue
                w, h = (p.pad_size or {}).get(pn, (0.0, 0.0))
                n = net_of.get((r, pn))
                own.append((pn, px - w / 2, py - h / 2, px + w / 2, py + h / 2, n, mask(p, pn)))
            if not own:
                continue
            hx0, hy0 = min(o[1] for o in own), min(o[2] for o in own)
            hx1, hy1 = max(o[3] for o in own), max(o[4] for o in own)
            for pn, x0, y0, x1, y1, n, m in own:
                pad_rows.append((self.ri[r], x0, y0, x1, y1, nid.get(n, -1), gid.get(n, -1), m))
            for pi, (pn, x0, y0, x1, y1, n, m) in enumerate(own):
                if n is None or n not in leaves:
                    continue
                c = cls.get(n, {})
                hw = float(c.get("width", track)) / 2 + float(c.get("clearance", clr0))
                px, py = (x0 + x1) / 2, (y0 + y1) / 2
                for k, (dx, dy) in enumerate(DIRS):
                    if dx:
                        sb = (px, py - hw, hx1 + self.depth, py + hw) if dx > 0 else (hx0 - self.depth, py - hw, px, py + hw)
                    else:
                        sb = (px - hw, py, px + hw, hy1 + self.depth) if dy > 0 else (px - hw, hy0 - self.depth, px + hw, py)
                    blocked = any(o[5] != n and (o[6] & m) and o[1] < sb[2] - 1e-6 and o[3] > sb[0] + 1e-6
                                  and o[2] < sb[3] - 1e-6 and o[4] > sb[1] + 1e-6 for o in own)
                    if not blocked:
                        stub_rows.append((self.ri[r], len(pad_rows) - len(own) + pi, *sb, nid[n], gid.get(n, -1), m,
                                          float(c.get("clearance", clr0)), k))
        self.pad_ref = np.array([x[0] for x in pad_rows], dtype=int)
        self.pad_loc = np.array([x[1:5] for x in pad_rows], dtype=float).reshape(-1, 4)
        self.pad_net = np.array([x[5] for x in pad_rows], dtype=int)
        self.pad_grp = np.array([x[6] for x in pad_rows], dtype=int)
        self.pad_mask = np.array([x[7] for x in pad_rows], dtype=int)
        self.st_ref = np.array([x[0] for x in stub_rows], dtype=int)
        self.st_pad = np.array([x[1] for x in stub_rows], dtype=int)
        self.st_loc = np.array([x[2:6] for x in stub_rows], dtype=float).reshape(-1, 4)
        self.st_net = np.array([x[6] for x in stub_rows], dtype=int)
        self.st_grp = np.array([x[7] for x in stub_rows], dtype=int)
        self.st_mask = np.array([x[8] for x in stub_rows], dtype=int)
        self.st_clr = np.array([x[9] for x in stub_rows], dtype=float)
        self.st_dir = np.array([x[10] for x in stub_rows], dtype=int)  # DIRS index at rotation 0
        self.channel = on_step(d, "infl")  # step 2: the channel check in the part cost
        self.st_area = np.maximum((self.st_loc[:, 2] - self.st_loc[:, 0]) * (self.st_loc[:, 3] - self.st_loc[:, 1]), 1e-9)
        n = len(self.refs)
        self.pad_of = [np.nonzero(self.pad_ref == i)[0] for i in range(n)]
        self.st_of = [np.nonzero(self.st_ref == i)[0] for i in range(n)]
        self.reach = float(max(self.iso_mm.max(), self.iso_btw.max())) + 0.5
        # parts that share a net (a series part at its connector, a decoupling cap at its IC) belong close together:
        # their copper and slots count `conn` x (HWFLOW_PLACE_ESCAPE_CONN, default 1: no exemption)
        self.conn = float(get_settings().layout.place_escape_conn)
        self.wconn = np.ones((n, n))
        if self.conn != 1.0:
            gnd = set(d.ground_nets())
            for k, conns in nets.items():
                if k in gnd:
                    continue
                ids = sorted({self.ri[r] for r, _ in conns if r in self.ri})
                for a in ids:
                    for b_ in ids:
                        if a != b_:
                            self.wconn[a, b_] = self.conn
        # per part: how far its copper reaches into a neighbour's slots (the isolation distance of its pads or slots)
        self.part_reach = np.full(n, 0.5)
        for i in range(n):
            gs = np.concatenate([self.pad_grp[self.pad_of[i]], self.st_grp[self.st_of[i]]])
            if (gs >= 0).any():
                self.part_reach[i] = self.reach
        # rule areas without tracks: (box, layer mask)
        self.ko = []
        for a in getattr(P, "areas", ()) or ():
            if "tracks" in a["forbids"]:
                lay = a["layers"]
                m = allm if "*.Cu" in lay else sum(1 << i for i, L in enumerate(layers) if L in lay)
                if m:
                    self.ko.append((tuple(a["box"]), m, frozenset(a.get("exempt") or ()), a.get("owner")))
        self.pad_g = np.zeros_like(self.pad_loc)
        self.st_g = np.zeros_like(self.st_loc)
        self.ext = np.zeros((n, 4))
        for r in self.refs:
            self._sync(r)

    # ---- positions ---------------------------------------------------------------------------------------------

    def _sync(self, r):
        i = self.ri[r]
        p = self.pl.parts[r]
        pi, si = self.pad_of[i], self.st_of[i]
        if len(pi):
            self.pad_g[pi] = _glob(*self.pad_loc[pi].T, p.x, p.y, p.r)
        if len(si):
            self.st_g[si] = _glob(*self.st_loc[si].T, p.x, p.y, p.r)
        allb = np.concatenate([self.pad_g[pi], self.st_g[si]]) if len(si) else self.pad_g[pi]
        self.ext[i] = (allb[:, 0].min(), allb[:, 1].min(), allb[:, 2].max(), allb[:, 3].max()) if len(allb) else 0.0

    def save(self, refs) -> list:
        out = []
        for r in refs:
            i = self.ri.get(r)
            if i is not None:
                out.append((i, self.pad_g[self.pad_of[i]].copy(), self.st_g[self.st_of[i]].copy(), self.ext[i].copy()))
        return out

    def load(self, saved):
        for i, pg, sg, e in saved:
            self.pad_g[self.pad_of[i]] = pg
            self.st_g[self.st_of[i]] = sg
            self.ext[i] = e

    def move(self, refs) -> set:
        """Sync the moved parts and return the refs whose cost may change (the moved ones and the parts whose escape
        box meets a moved one's, before or after the move)."""
        idx = [self.ri[r] for r in refs if r in self.ri]
        if not idx:
            return set()
        before = self.ext[idx].copy()
        for r in refs:
            if r in self.ri:
                self._sync(r)
        boxes = np.concatenate([before, self.ext[idx]])
        gm = np.concatenate([self.part_reach[idx], self.part_reach[idx]])
        g = np.maximum(self.part_reach[:, None], gm[None, :])
        e = self.ext
        hit = ((e[:, None, 0] < boxes[None, :, 2] + g) & (e[:, None, 2] > boxes[None, :, 0] - g)
               & (e[:, None, 1] < boxes[None, :, 3] + g) & (e[:, None, 3] > boxes[None, :, 1] - g)).any(axis=1)
        return {self.refs[i] for i in np.nonzero(hit)[0]} | {r for r in refs if r in self.ri}

    # ---- cost --------------------------------------------------------------------------------------------------

    def slot_block(self, r):
        """(stub indexes, blockage in [0, 1]) of part r's slots at the current positions."""
        i = self.ri.get(r)
        if i is None or not len(self.st_of[i]):
            return np.zeros(0, dtype=int), np.zeros(0)
        si = self.st_of[i]
        S = self.st_g[si]
        g = self.reach
        x0, y0, x1, y1 = S[:, 0].min() - g, S[:, 1].min() - g, S[:, 2].max() + g, S[:, 3].max() + g
        cover = np.zeros(len(si))
        # foreign pads of other parts
        G = self.pad_g
        near = np.nonzero((self.pad_ref != i) & (G[:, 0] < x1) & (G[:, 2] > x0) & (G[:, 1] < y1) & (G[:, 3] > y0))[0]
        if len(near):
            sg, qg = self.st_grp[si][:, None], self.pad_grp[near][None, :]
            sn, qn = self.st_net[si][:, None], self.pad_net[near][None, :]
            iso = np.where((sg != qg) & ((sg >= 0) | (qg >= 0)), np.maximum(self.iso_mm[sg], self.iso_mm[qg]),
                           np.where((sg == qg) & (sg >= 0) & (sn != qn), self.iso_btw[sg], 0.0))
            grow = np.clip(iso - self.st_clr[si][:, None], 0, None)
            Q = G[near]
            ox = np.minimum(S[:, None, 2], Q[None, :, 2] + grow) - np.maximum(S[:, None, 0], Q[None, :, 0] - grow)
            oy = np.minimum(S[:, None, 3], Q[None, :, 3] + grow) - np.maximum(S[:, None, 1], Q[None, :, 1] - grow)
            a = np.clip(ox, 0, None) * np.clip(oy, 0, None)
            ok = (sn != qn) & ((self.st_mask[si][:, None] & self.pad_mask[near][None, :]) != 0)
            cover += (a * ok * self.wconn[i, self.pad_ref[near]][None, :]).sum(axis=1)
        # other parts' slots of another net
        T = self.st_g
        near = np.nonzero((self.st_ref != i) & (T[:, 0] < x1) & (T[:, 2] > x0) & (T[:, 1] < y1) & (T[:, 3] > y0))[0]
        if len(near):
            a = _ov(S, T[near])
            ok = (self.st_net[si][:, None] != self.st_net[near][None, :]) & \
                 ((self.st_mask[si][:, None] & self.st_mask[near][None, :]) != 0)
            cover += FAN_SHARE * (a * ok * self.wconn[i, self.st_ref[near]][None, :]).sum(axis=1)
        # rule areas without tracks on every layer the slot may use
        ref = self.refs[i]
        for bx, m, exempt, owner in self.ko:
            if ref in exempt or owner == ref:
                continue
            full = (self.st_mask[si] & ~m) == 0
            if full.any():
                cover += _ov(S, np.array([bx]))[:, 0] * full
        # the board outside the copper-edge distance
        W, H, e = self.pl.W, self.pl.H, self.edge
        inx = np.clip(np.minimum(S[:, 2], W - e) - np.maximum(S[:, 0], e), 0, None)
        iny = np.clip(np.minimum(S[:, 3], H - e) - np.maximum(S[:, 1], e), 0, None)
        cover += np.clip(self.st_area[si] - inx * iny - 1e-9, 0, None)
        return si, np.minimum(1.0, GAIN * cover / self.st_area[si])

    def part_cost(self, r) -> float:
        si, b = self.slot_block(r)
        if not len(si):
            return 0.0
        pads = self.st_pad[si]
        best, arg = {}, {}
        for j, (p, v) in enumerate(zip(pads.tolist(), b.tolist())):
            if v < best.get(p, 2.0):
                best[p], arg[p] = v, j
        c = float(sum(best.values()))
        if self.channel:
            c += self.channel_short(r, si, [arg[p] for p in arg])
        return c

    def channel_short(self, r, si, chosen) -> float:
        """Channel check (step 2): the pads of r that leave through the same side (their least blocked slot) fan out
        along it, half each way, so the side needs ceil(n / 2) track pitches of free room in front of the pad hull,
        over the pads' span. The shortfall against the nearest foreign copper (other parts' pads on a shared layer,
        the board edge) in that strip, in track pitches."""
        i = self.ri[r]
        q = int(round((self.pl.parts[r].r % 360) / 90.0)) % 4
        dirs = (self.st_dir[si][chosen] - q) % 4  # global DIRS index (KiCad rotation turns +x to -y)
        hull = self.pad_g[self.pad_of[i]]
        out = 0.0
        G = self.pad_g
        for D in set(dirs.tolist()):
            sel = [chosen[j] for j in range(len(chosen)) if dirs[j] == D]
            if len(sel) < 2:
                continue  # one pad: its slot is the check
            st = si[sel]
            P = self.pad_g[self.st_pad[st]]
            lat = self.st_g[st, 2] - self.st_g[st, 0] if D % 2 else self.st_g[st, 3] - self.st_g[st, 1]  # w + 2 clr
            pitch = float(np.mean(lat - self.st_clr[st]))  # w + clr
            need = math.ceil(len(sel) / 2) * pitch + float(self.st_clr[st].max())
            ax = 0 if D % 2 == 0 else 1  # the axis the side faces along
            sgn = DIRS[D][ax]
            lo, hi = P[:, 1 - ax].min(), P[:, 3 - ax].max()  # lateral span (y for an x side)
            edge = hull[:, 2 + ax].max() if sgn > 0 else hull[:, ax].min()
            lim = (self.pl.W if ax == 0 else self.pl.H) - self.edge if sgn > 0 else self.edge
            gap = (lim - edge) * sgn
            m = (self.pad_ref != i) & (G[:, 1 - ax] < hi) & (G[:, 3 - ax] > lo)
            m &= (G[:, ax] >= edge - 1e-6) if sgn > 0 else (G[:, 2 + ax] <= edge + 1e-6)
            m &= (self.pad_mask & int(np.bitwise_or.reduce(self.st_mask[st]))) != 0
            if m.any():
                d = (G[m, ax] - edge) if sgn > 0 else (edge - G[m, 2 + ax])
                gap = min(gap, float(d.min()))
            if gap < need:
                out += (need - gap) / pitch
        return out

    def blocked(self, r, level: float = 0.999) -> int:
        """Pads of r with every slot blocked."""
        si, b = self.slot_block(r)
        best = {}
        for p, v in zip(self.st_pad[si].tolist(), b.tolist()):
            best[p] = min(v, best.get(p, 2.0))
        return sum(v >= level for v in best.values())

    def report(self) -> dict:
        """{pads with a slot, pads blocked, pads half blocked, cost, blocked refs}: the proxy of the bench."""
        npad = len(set(self.st_pad.tolist()))
        per = {r: self.blocked(r) for r in self.refs}
        half = sum(self.blocked(r, 0.5) for r in self.refs)
        return {"pads": npad, "blocked": sum(per.values()), "half": half,
                "cost": round(sum(self.part_cost(r) for r in self.refs), 2),
                "parts": {r: v for r, v in per.items() if v}}


PIN_WIRE = 2.0  # mm of wire per escaping pad, in units of the slot depth: the exit and the turn (step 2)


def demand(pl):
    """Step 2 for the density feedback of the global placement: (extra(nx, ny, cell) -> pin escape demand per cell,
    {ref: share of its escaping pads without a free slot (with the channel shortfall), at most 1}). The slot model is
    built once per placer and synced to the current global placement."""
    e = getattr(pl, "_esc_fb", None)
    if e is None:
        e = pl.esc if pl.esc is not None else Escape(pl)
        pl._esc_fb = e
    e.move(e.refs)
    shares = {}
    for r in e.refs:
        n = len(set(e.st_pad[e.st_of[e.ri[r]]].tolist()))
        if n:
            shares[r] = min(1.0, e.part_cost(r) / n)
    pads = np.unique(e.st_pad)
    cx = (e.pad_g[pads, 0] + e.pad_g[pads, 2]) / 2
    cy = (e.pad_g[pads, 1] + e.pad_g[pads, 3]) / 2

    def extra(nx, ny, cell):
        g = np.zeros((nx, ny))
        i = np.clip((cx // cell).astype(int), 0, nx - 1)
        j = np.clip((cy // cell).astype(int), 0, ny - 1)
        np.add.at(g, (i, j), PIN_WIRE * e.depth)
        return g
    return extra, shares


FORWARD: list = []  # step 3 reports, one per forward() call: {ref: {rot: reason}} of the pruned rotations


def forward(pl, movable) -> dict:
    """Step 3, forward checking of the rotations before the global placement. For every movable part (not a layout
    block), each of the 4 rotations is checked against what is known before placement:

    - fit: the courtyard fits the part's floorplan region box (else the board inside the margin);
    - edge: a part with `edge` placed on that margin line keeps a free slot for every escaping pad (a pad whose only
      own-open slots face the board edge is dead);
    - isolation side: a part with pads of a region's nets (an isolation region, e.g. mains) and pads of other nets
      turns the region's pads towards the region (the centroid of those pads lies on the region's side of the
      centroid of the others), so the isolation band does not have to pass through or around the part.

    Returns {ref: allowed rotations}; a part keeps its rotation when allowed, else it starts at the first allowed one.
    A part with no legal rotation is reported (printed and in FORWARD) and left free: the infeasibility report."""
    from ...core import floorplan
    from ...core.geometry import rot as grot
    e = pl.esc if pl.esc is not None else Escape(pl)
    m = float(pl.d.board["margin_mm"])
    regs = floorplan.net_regions(pl.d)
    net_of = {(r, p): n for n, conns in pl.d.nets().items() for r, p in conns}
    allowed, report, dead_all = {}, {}, []
    for u in movable:
        r = getattr(u, "ref", None)
        if r is None or r not in e.ri or hasattr(u, "members"):
            continue
        p = pl.parts[r]
        box = pl.region_start.get(r) or (m, m, pl.W - m, pl.H - m)
        bw, bh = box[2] - box[0], box[3] - box[1]
        why = {}
        for q in range(4):
            a = q * 90.0
            w, h = (p.w, p.h) if q % 2 == 0 else (p.h, p.w)
            if w > bw + 1e-6 or h > bh + 1e-6:
                why[a] = f"courtyard {w:.1f} x {h:.1f} mm does not fit {bw:.1f} x {bh:.1f} mm"
                continue
            if p.edge:
                n = _edge_dead(e, p, a, m)
                if n:
                    why[a] = f"{n} pad(s) escape only towards the {p.edge} edge"
                    continue
            for g in regs:
                mem = [pn for pn in p.pads if net_of.get((r, pn)) in g["nets"]]
                oth = [pn for pn in p.pads if pn not in mem and net_of.get((r, pn))]
                if not mem or not oth:
                    continue
                c = g["poly"].centroid
                mx, my = np.mean([grot(*p.pads[pn], a) for pn in mem], axis=0)
                ox, oy = np.mean([grot(*p.pads[pn], a) for pn in oth], axis=0)
                vx, vy = mx - ox, my - oy
                # the part may sit anywhere in its box: the direction to the region from the box centre
                bx, by = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                if g["poly"].contains(_pt(bx, by)):
                    continue
                if vx * (c.x - bx) + vy * (c.y - by) < -1e-6 * (abs(vx) + abs(vy)):
                    why[a] = f"pads of region {g['region']} face away from it"
                    break
        ok = [q * 90.0 for q in range(4) if q * 90.0 not in why]
        if not ok:
            dead_all.append(r)
            report[r] = why
            print(f"[place] forward check: {r} has no legal rotation: "
                  + "; ".join(f"{int(k)}: {v}" for k, v in why.items()))
            continue
        if why:
            report[r] = why
            allowed[r] = set(ok)
            if (p.r % 360) not in allowed[r]:
                p.r = ok[0]
    FORWARD.append({"pruned": report, "infeasible": dead_all})
    if report:
        print(f"[place] forward check: rotations pruned on {len(report) - len(dead_all)} part(s)"
              + (f", {len(dead_all)} without a legal rotation ({', '.join(dead_all)})" if dead_all else ""))
    return allowed


def _pt(x, y):
    from shapely.geometry import Point
    return Point(x, y)


def _edge_dead(e, p, a, m) -> int:
    """Pads of part p at rotation a, with its courtyard on its edge's margin line, whose every own-open slot leaves
    the board inside the copper-edge distance."""
    i = e.ri[p.ref]
    si = e.st_of[i]
    if not len(si):
        return 0
    ox, oy, old_r = p.x, p.y, p.r
    p.r = a
    x0, y0, x1, y1 = p.box()
    W, H = e.pl.W, e.pl.H
    dx = {"left": m - x0, "right": W - m - x1}.get(p.edge, 0.0)
    dy = {"top": m - y0, "bottom": H - m - y1}.get(p.edge, 0.0)
    S = _glob(*e.st_loc[si].T, p.x + dx, p.y + dy, a)
    p.x, p.y, p.r = ox, oy, old_r
    inx = np.clip(np.minimum(S[:, 2], W - e.edge) - np.maximum(S[:, 0], e.edge), 0, None)
    iny = np.clip(np.minimum(S[:, 3], H - e.edge) - np.maximum(S[:, 1], e.edge), 0, None)
    out = GAIN * (e.st_area[si] - inx * iny) / e.st_area[si] >= 1.0
    best = {}
    for pad, dead in zip(e.st_pad[si].tolist(), out.tolist()):
        best[pad] = best.get(pad, True) and dead
    return sum(best.values())


__all__ = ["Escape", "on", "on_step", "demand", "forward", "FORWARD", "GAIN", "FAN_SHARE"]
