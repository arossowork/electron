"""Analytic global placement, stage 2 of the layout flow (asicflow.place, docs/layout-flow.md).

In the style of ePlace/RePlAce and SimPL: the movable units (parts and layout blocks, at their current rotation) get
positions that minimise a smooth cost with L-BFGS-B, in rounds of rising density weight and falling smoothing length:

- wirelength: log-sum-exp half perimeter of every net (the placer's net weights), plus the decoupling pairs
  (weight `decouple`) as two-pin nets; pads of fixed parts are anchors;
- density: the smoothed overlap area of every pair of courtyards on one board side (movable/movable and
  movable/fixed), with the rule areas that forbid footprints and the exclusive floorplan regions as fixed blockages;
- bounds, edge and region clamps: squared distance of a courtyard outside the board margin, from its edge, or
  outside its floorplan region.

The result is legalized by Placer.legalize (push apart, clamp into the board and rectangular regions), then refined
by the detailed moves and a short low-temperature anneal. Deterministic for a given start."""

from __future__ import annotations

import math

import numpy as np


def _sp(z, g):
    """softplus g*log(1+exp(z/g)) and its derivative, overflow-safe."""
    t = z / g
    v = np.where(t > 30, z, g * np.log1p(np.exp(np.minimum(t, 30))))
    d = 1.0 / (1.0 + np.exp(-np.clip(t, -60, 60)))
    return v, d


def _rect_cover(poly, step=1.0):
    """Boxes that cover a polygon (row runs of `step` cells that meet it)."""
    from shapely.geometry import box
    x0, y0, x1, y1 = poly.bounds
    out = []
    y = y0
    while y < y1 - 1e-9:
        run = None
        x = x0
        while x < x1 - 1e-9:
            c = box(x, y, min(x + step, x1), min(y + step, y1))
            if c.intersection(poly).area > 0.25 * c.area:
                run = [run[0] if run else x, min(x + step, x1)]
            elif run:
                out.append((run[0], y, run[1], min(y + step, y1)))
                run = None
            x += step
        if run:
            out.append((run[0], y, run[1], min(y + step, y1)))
        y += step
    return out


BUDGET_GAIN = 3.0  # weight of a copper_len budget leg in the global placement, x the decoupling weight


class Global:
    def __init__(self, pl, movable):
        self.pl, self.mv = pl, movable
        P = pl.parts
        idx = {id(u): i for i, u in enumerate(movable)}
        n = len(movable)
        self.n = n
        ux = np.array([u.x for u in movable])
        uy = np.array([u.y for u in movable])
        # pins: (unit index or -1, dx, dy) with dx, dy from the unit origin (or absolute for a fixed pad)
        def pin(r, pn):
            u = pl._unit(P[r])
            x, y = P[r].pad(pn)
            i = idx.get(id(u), -1)
            return (i, x - u.x, y - u.y) if i >= 0 else (-1, x, y)
        self.nets = []
        for w, conns in pl.nets:
            pins = [pin(r, p) for r, p in conns]
            if any(i >= 0 for i, _, _ in pins):
                self.nets.append((w * pl.wt["wire"], pins))
        lims = getattr(pl, "dec_lim", ())
        for k, (cr, cp, ir, ip, cg, ig) in enumerate(pl.decap):
            pins = [pin(cr, cp), pin(ir, ip)]
            if any(i >= 0 for i, _, _ in pins):
                # a copper_len budget leg (placer._init_budget_pairs) pulls harder than a decoupling pair
                bud = k < len(lims) and lims[k] is not None
                self.nets.append((pl.wt["decouple"] * (BUDGET_GAIN if bud else 1.0), pins))
        # flatten nets for vectorised LSE: per net arrays
        self.net_arrays = []
        for w, pins in self.nets:
            a = np.array(pins, dtype=float)
            self.net_arrays.append((w, a[:, 0].astype(int), a[:, 1], a[:, 2]))
        # courtyard boxes per side: movable (unit, side, cx off, cy off, hw, hh), fixed (side, x0, y0, x1, y1)
        mbox, own = [], set()
        for i, u in enumerate(movable):
            refs = pl._refs(u) if hasattr(u, "members") else [u.ref]
            own.update(refs)
            ps = [P[u.ref]] if hasattr(u, "members") else [u]  # a block: its courtyard holds the members
            for p in ps:
                for s in ("front", "back"):
                    b = p.side_box(s)
                    if b:
                        mbox.append((i, s, (b[0] + b[2]) / 2 - u.x, (b[1] + b[3]) / 2 - u.y, (b[2] - b[0]) / 2,
                                     (b[3] - b[1]) / 2))
        fbox = []
        for r, p in P.items():
            if r in own:
                continue
            for s in ("front", "back"):
                b = p.side_box(s)
                if b:
                    fbox.append((s, *b))
        for b, sides, owner, name in pl.ko_fp:
            for s in sides:
                fbox.append((s, *b, frozenset({owner}) | getattr(pl, "ko_exempt", {}).get((owner, name), frozenset())))
        self.region_pen = []  # (unit index, cx off, cy off, hw, hh, rect)
        for g in pl.regions:
            rect = g["rect"] or g["poly"].bounds
            mem = set(g["members"])
            for k, (i, s, ox, oy, hw, hh) in enumerate(mbox):
                u = movable[i]
                refs = set(pl._refs(u)) if hasattr(u, "members") else {u.ref}
                if refs & mem:
                    self.region_pen.append((i, ox, oy, hw, hh, rect))
            # a part with pads on the region's nets (not a member): those pads inside the region box
            # (Placer._region_cost `pads`)
            for r, pns in g["pads"].items():
                if r in mem or r not in P:
                    continue
                u = pl._unit(P[r])
                i = idx.get(id(u), -1)
                if i < 0:
                    continue
                for pn in pns:
                    px, py = P[r].pad(pn)
                    w, h = (getattr(P[r], "pad_size", None) or {}).get(pn, (0.6, 0.6))
                    if P[r].r % 180:
                        w, h = h, w
                    self.region_pen.append((i, px - u.x, py - u.y, w / 2, h / 2, rect))
            if g["excl"]:
                boxes = [rect] if g["rect"] else _rect_cover(g["poly"])
                for s in g["sides"]:
                    for b in boxes:
                        fbox.append((s, *b, frozenset(g["allow"]) | mem))
        for u in movable:
            pl._refs(u)
        self.mbox = mbox
        self.fbox = [f if len(f) == 6 else (*f, frozenset()) for f in fbox]
        # movable pairs on one side
        mm = [(a, b) for a in range(len(mbox)) for b in range(a + 1, len(mbox))
              if mbox[a][0] != mbox[b][0] and mbox[a][1] == mbox[b][1]]
        self.mm = np.array(mm, dtype=int).reshape(-1, 2)
        mf = []
        for a, (i, s, *_r) in enumerate(mbox):
            u = movable[i]
            refs = set(pl._refs(u)) if hasattr(u, "members") else {u.ref}
            for f, fb in enumerate(self.fbox):
                if fb[0] == s and not (refs & fb[5]):
                    mf.append((a, f))
        for u in movable:
            pl._refs(u)
        self.mf = np.array(mf, dtype=int).reshape(-1, 2)
        mb = np.array([m[2:] for m in mbox], dtype=float).reshape(-1, 4)
        self.mbi = np.array([m[0] for m in mbox], dtype=int)
        self.mox, self.moy, self.mhw, self.mhh = mb.T if len(mb) else (np.zeros(0),) * 4
        fb = np.array([f[1:5] for f in self.fbox], dtype=float).reshape(-1, 4)
        self.fcx, self.fcy = (fb[:, 0] + fb[:, 2]) / 2, (fb[:, 1] + fb[:, 3]) / 2
        self.fhw, self.fhh = (fb[:, 2] - fb[:, 0]) / 2, (fb[:, 3] - fb[:, 1]) / 2
        first = {}
        for k, i in enumerate(self.mbi):
            first.setdefault(int(i), k)
        self.edge = [(first[i], u.edge) for i, u in enumerate(movable) if getattr(u, "edge", None) and i in first]
        self.x0 = np.concatenate([ux, uy])
        self.infl = np.ones(len(self.mbi))  # courtyard side inflation of the density term (asicflow routability feedback)

    # ---- cost ----------------------------------------------------------------------------------------------------

    def _wl(self, x, y, gx, gy, gamma):
        c = 0.0
        for w, ui, dx, dy in self.net_arrays:
            mvm = ui >= 0
            px = np.where(mvm, x[np.maximum(ui, 0)] + dx, dx)
            py = np.where(mvm, y[np.maximum(ui, 0)] + dy, dy)
            for p, g in ((px, gx), (py, gy)):
                m1, m2 = p.max(), p.min()
                e1, e2 = np.exp((p - m1) / gamma), np.exp((m2 - p) / gamma)
                s1, s2 = e1.sum(), e2.sum()
                c += w * (gamma * math.log(s1) + m1 + gamma * math.log(s2) - m2)
                np.add.at(g, ui[mvm], w * (e1 / s1 - e2 / s2)[mvm])
        return c

    def _ov(self, ax, ay, ahw, ahh, bx, by, bhw, bhh, g):
        dx, dy = ax - bx, ay - by
        rx, ry = np.sqrt(dx * dx + 0.01), np.sqrt(dy * dy + 0.01)
        ox, dox = _sp(ahw + bhw - rx, g)
        oy, doy = _sp(ahh + bhh - ry, g)
        area = ox * oy
        gax = -oy * dox * dx / rx  # d area / d ax
        gay = -ox * doy * dy / ry
        return area, gax, gay

    def cost(self, v, lam, gamma, soft):
        n = self.n
        x, y = v[:n], v[n:]
        gx, gy = np.zeros(n), np.zeros(n)
        c = self._wl(x, y, gx, gy, gamma)
        cx, cy = x[self.mbi] + self.mox, y[self.mbi] + self.moy
        gbx, gby = np.zeros(len(cx)), np.zeros(len(cx))
        ihw, ihh = self.mhw * self.infl, self.mhh * self.infl
        if len(self.mm):
            a, b = self.mm[:, 0], self.mm[:, 1]
            area, gax, gay = self._ov(cx[a], cy[a], ihw[a], ihh[a], cx[b], cy[b], ihw[b], ihh[b], soft)
            c += lam * area.sum()
            np.add.at(gbx, a, lam * gax); np.add.at(gbx, b, -lam * gax)
            np.add.at(gby, a, lam * gay); np.add.at(gby, b, -lam * gay)
        if len(self.mf):
            a, f = self.mf[:, 0], self.mf[:, 1]
            area, gax, gay = self._ov(cx[a], cy[a], ihw[a], ihh[a], self.fcx[f], self.fcy[f], self.fhw[f],
                                      self.fhh[f], soft)
            c += lam * area.sum()
            np.add.at(gbx, a, lam * gax)
            np.add.at(gby, a, lam * gay)
        # bounds: squared courtyard excess past the board margin, weighted like the overlap
        pl = self.pl
        m, W, H = pl.d.board["margin_mm"], pl.W, pl.H
        for lo, hi, cc, hw, gb in ((m, W - m, cx, self.mhw, gbx), (m, H - m, cy, self.mhh, gby)):
            e0 = np.maximum(lo - (cc - hw), 0)
            e1 = np.maximum((cc + hw) - hi, 0)
            c += lam * (e0 ** 2 + e1 ** 2).sum()
            gb += lam * (-2 * e0 + 2 * e1)
        for i, ox, oy, hw, hh, (rx0, ry0, rx1, ry1) in self.region_pen:  # region members: inside the region box
            bx, by = x[i] + ox, y[i] + oy
            for lo, hi, cc, h, k, gg in ((rx0, rx1, bx, hw, i, gx), (ry0, ry1, by, hh, i, gy)):
                e0 = max(lo - (cc - h), 0.0)
                e1 = max((cc + h) - hi, 0.0)
                c += 4 * lam * (e0 ** 2 + e1 ** 2)
                gg[k] += 4 * lam * (-2 * e0 + 2 * e1)
        for k, side in self.edge:  # an edge part: its courtyard side on the board margin line
            if True:
                if side in ("left", "right"):
                    e = (cx[k] - self.mhw[k] - m) if side == "left" else (W - m - cx[k] - self.mhw[k])
                    c += pl.wt["edge"] * e * e
                    gbx[k] += pl.wt["edge"] * 2 * e * (1 if side == "left" else -1)
                else:
                    e = (cy[k] - self.mhh[k] - m) if side == "top" else (H - m - cy[k] - self.mhh[k])
                    c += pl.wt["edge"] * e * e
                    gby[k] += pl.wt["edge"] * 2 * e * (1 if side == "top" else -1)
        np.add.at(gx, self.mbi, gbx)
        np.add.at(gy, self.mbi, gby)
        return c, np.concatenate([gx, gy])

    def run(self, rounds: int = 8, maxiter: int = 150, feedback=None):
        """`feedback(self)` (asicflow): called with the units at the current solution after rounds FB_START,
        FB_START + FB_EVERY, ... (not after the last); it may raise self.infl."""
        from .asicflow import FB_EVERY, FB_START
        from scipy.optimize import minimize
        pl = self.pl
        n = self.n
        bounds = [(0.0, pl.W)] * n + [(0.0, pl.H)] * n
        v = self.x0.copy()
        # smoothing lengths from the typical part size u (sqrt of the median movable courtyard area): LSE gamma from
        # 1.6 u down to 0.2 u, softplus width from 0.4 u down to 0.04 u; density weight from 0.05 up x4 per round
        a = sorted(4 * hw * hh for hw, hh in zip(self.mhw, self.mhh)) or [1.0]
        u = math.sqrt(a[len(a) // 2])
        lam, gamma, soft = 0.05, 1.6 * u, 0.4 * u
        for rnd in range(rounds):
            res = minimize(self.cost, v, args=(lam, gamma, soft), jac=True, method="L-BFGS-B", bounds=bounds,
                           options={"maxiter": maxiter})
            v = res.x
            if feedback is not None and rnd >= FB_START and (rnd - FB_START) % FB_EVERY == 0 and rnd < rounds - 1:
                for i, un in enumerate(self.mv):
                    un.x, un.y = float(v[i]), float(v[n + i])
                    pl._refs(un)
                feedback(self)
            lam *= 4.0
            gamma = max(0.2 * u, gamma * 0.7)
            soft = max(0.04 * u, soft * 0.6)
        for i, u in enumerate(self.mv):
            u.x, u.y = float(v[i]), float(v[n + i])
            pl._refs(u)
        return v


def global_place(pl, movable) -> None:
    """Analytic global placement of `movable` from their current positions, then legalization (Placer.legalize)."""
    if not movable:
        return
    Global(pl, movable).run()
    pl.legalize()
