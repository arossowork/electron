"""Combined ASIC-style placement and routing flow (experiment, docs/asic-ideas.md "Combined flow"; HWFLOW_FLOW=asic,
or `placement: {flow: asic}` in design.yaml; off by default).

Stages, each with a switch HWFLOW_ASIC_<NAME>=0 that turns it off (for the ablations):

1. macro (MACRO): when the design has layout blocks or units with a courtyard of at least MACRO_AREA x the median
   movable one, a mixed-size global placement of every unit, then the macros are legalized among themselves and the
   fixed parts and held while a second global placement places the other units (RePlAce mixed-size style).
2. global placement (gplace.Global) whose density term sees routing demand (DENSITY = rudy | groute | none):
   every FB_EVERY rounds from round FB_START, a congestion map of the current global placement inflates the
   courtyards of the units over overflowing cells (cell inflation of routability-driven placers):
     rudy:   RUDY wire demand against the cell capacity minus the pad blockage (Placer._pad_blockage);
     groute: the PathFinder global router (globalroute.py) on the current placement, overflow of its g-cell edges.
3. legalization (Placer.legalize).
4. detailed placement: local moves (DP, Placer.detailed), then a short low-temperature anneal (ANNEAL) with the
   placer's terms (corridor, isolation, regions; RUDY without pad blockage, as in the default cost).
5. pin access:
     ACCESS (placer): a cost term, ACCESS_W per pad with at most 1 of ACCESS_DIRS exits at its net's width
       (2 x ACCESS_W with none), in the detailed moves and the anneal (PinAccess, an incremental rectangle model of
       globalroute.pin_access);
     NECK (router): a net with a pad without an exit at its width, but with one at the board minimum
       (globalroute.pin_access), routes in pcb.krt_route's narrow-first `necked` path.
6. detailed routing: KRT with the nets ordered most constrained first by pin access (ORDER: the first try is
   `--ordering original` on that order, the other tries follow). The global-route layer hints stay off.

Nothing here is tied to a design: every input is the board's rules and placement."""
from __future__ import annotations

import math
import os
import time

import numpy as np

MACRO_AREA = 10.0     # a unit with a courtyard >= this x the median movable courtyard (or a layout block) is a macro
FB_START, FB_EVERY = 2, 2  # routability feedback after global placement rounds 2, 4 and 6 (of 8)
INFL_MAX = 2.0        # largest courtyard area inflation of one unit
INFL_BUDGET = 0.25    # the added (inflated) area is at most this share of the free board area
GR_ITERS = 10         # PathFinder iterations of a feedback global route
GR_TARGET = 1.0       # g-cell use / capacity above which the courtyards over it inflate
ANNEAL_SHARE = 0.25   # short anneal: this share of the per-seed iterations
ANNEAL_T0 = 0.03      # its start temperature, as a share of 2 x max(W, H) (placer.ANALYTIC_T0)
ACCESS_W = 3.0        # placer weight of a pad with at most 1 exit (twice that with none), mm of wire equivalent
ACCESS_DIRS = 8
ACCESS_STEP_MM = 0.3  # as globalroute: an exit reaches this far beyond the pad edge
ACCESS_SAMPLES = 5    # points sampled along each exit
FEEDBACK: list = []   # per feedback: {source, round, s, over, inflated} (docs/asic-ideas.md)


def flow(design=None) -> str:
    v = os.environ.get("HWFLOW_FLOW")
    if v is None:
        v = ((getattr(design, "raw", None) or {}).get("placement") or {}).get("flow", "")
    return str(v or "").strip().lower()


def on(design=None) -> bool:
    return flow(design) == "asic"


def opt(name: str) -> bool:
    """Stage switch of the combined flow: HWFLOW_ASIC_<NAME>=0 turns that stage off (all on by default)."""
    return os.environ.get(f"HWFLOW_ASIC_{name.upper()}", "1").strip().lower() not in ("0", "false", "no", "off")


def density() -> str:
    return os.environ.get("HWFLOW_ASIC_DENSITY", "rudy").strip().lower()


# ------------------------------------------------------------------ placement


def _area(u):
    y = getattr(u, "yard", u)
    return y.w * y.h


def macros(pl, movable) -> list:
    from .placer import Rigid
    a = sorted(_area(u) for u in movable) or [1.0]
    cut = MACRO_AREA * a[len(a) // 2]
    return [u for u in movable if isinstance(u, Rigid) or _area(u) >= cut]


def place(pl, movable, iters: int, t0: float) -> float:
    """The placement stages of the combined flow on placer `pl` (Placer.run)."""
    from . import gplace
    fb = _feedback(pl)
    big = macros(pl, movable) if opt("macro") else []
    ts = time.perf_counter()
    if big and len(big) < len(movable):
        gplace.Global(pl, movable).run(feedback=fb)
        pl.legalize(refs={r for u in big for r in pl._refs(u)})
        small = [u for u in movable if not any(u is b for b in big)]
        gplace.Global(pl, small).run(feedback=fb)  # the macros are fixed boxes and anchors of this one
        pl._stage("macro+global", ts)
    else:
        gplace.Global(pl, movable).run(feedback=fb)
        pl._stage("global", ts)
    ts = time.perf_counter()
    pl.legalize()
    pl._stage("legalize", ts)
    if opt("dp"):
        ts = time.perf_counter()
        pl.detailed(movable)
        pl._stage("detailed", ts)
    if opt("anneal"):
        ts = time.perf_counter()
        pl._anneal(movable, max(1, int(iters * ANNEAL_SHARE)), t0 * ANNEAL_T0, 0.01, ramp=False)
        pl._stage("anneal", ts)
    return pl._finish(movable)


def _feedback(pl):
    src = density()
    maps = {"rudy": rudy_map, "groute": groute_map}
    if src not in maps:
        return None

    def fb(g):
        ts = time.perf_counter()
        _inflate(g, *maps[src](pl), src)
        FEEDBACK[-1]["s"] = round(time.perf_counter() - ts, 2)
    return fb


def rudy_map(pl):
    """(ratio grid [nx, ny], cell mm, overflow): RUDY wire demand / (capacity minus pad blockage) per 1 mm cell."""
    pl._init_grid()
    grid = np.zeros((pl.nx, pl.ny))
    for k, (_, conns) in enumerate(pl.nets):
        if pl._net_rudy[k]:
            xs, ys = zip(*(pl.parts[r].pad(p) for r, p in conns))
            i0, j0, dem = pl._rudy(xs, ys)
            grid[i0:i0 + dem.shape[0], j0:j0 + dem.shape[1]] += dem
    blk = np.zeros_like(pl.cap)
    for r in pl.parts:
        e = pl._pad_blockage(r)
        if e is not None:
            blk[e[0]:e[0] + e[2].shape[0], e[1]:e[1] + e[2].shape[1]] += e[2]
    ecap = np.maximum(pl.cap - blk, 0.0)
    floor = 0.05 * float(pl.cap.max() or 1.0)
    over = float(np.square(np.clip(grid - ecap, 0, None)).sum())
    return grid / np.maximum(ecap, floor), pl.cell, over


def groute_map(pl):
    """(ratio grid, g-cell mm, overflow) from a PathFinder global route of the current placement: per g-cell the
    largest use / capacity (capacity at least 0.5) of the planar edges at its sides and of its via edge, divided by
    GR_TARGET (1: only overflow inflates; HWFLOW_ASIC_GR_TARGET)."""
    from . import globalroute as G
    libs = getattr(pl.parts, "libs", None)
    if libs is None:
        from .libs import Libs
        libs = Libs(design=pl.d)
    P = G.build(pl.d, libs, parts=pl.parts)
    R_ = G._Router(P)
    R_.run(GR_ITERS)
    nL, nx, ny = len(P.layers), P.nx, P.ny
    use, cap = np.array(R_.use), np.maximum(np.array(R_.cap), 0.5)
    rat = use / cap
    over = float(np.clip(use - np.array(R_.cap), 0, None).sum())
    h = rat[:R_.nH].reshape(nL, ny, nx - 1).max(axis=0)          # [ny, nx-1]
    v = rat[R_.nH:R_.nH + R_.nV].reshape(nL, ny - 1, nx).max(axis=0)  # [ny-1, nx]
    via = rat[R_.nH + R_.nV:].reshape(ny, nx)
    R = via.copy()
    R[:, :-1] = np.maximum(R[:, :-1], h)
    R[:, 1:] = np.maximum(R[:, 1:], h)
    R[:-1, :] = np.maximum(R[:-1, :], v)
    R[1:, :] = np.maximum(R[1:, :], v)
    target = float(os.environ.get("HWFLOW_ASIC_GR_TARGET", GR_TARGET))
    return (R / target).T, P.pitch, over


def _inflate(g, R, cell, over, source):
    """Area inflation per courtyard box of global placement `g`: the mean of max(1, ratio) over the cells under it,
    clipped to INFL_MAX, at most INFL_BUDGET of the free area in total, never below the last one."""
    ts = time.perf_counter()
    pl = g.pl
    n = g.n
    nx, ny = R.shape
    Rm = np.maximum(R, 1.0)
    f = np.ones(len(g.mbi))
    for k in range(len(g.mbi)):
        u = g.mv[g.mbi[k]]
        cx, cy, hw, hh = u.x + g.mox[k], u.y + g.moy[k], g.mhw[k], g.mhh[k]
        i0, i1 = max(0, int((cx - hw) // cell)), min(nx, int(math.ceil((cx + hw) / cell)))
        j0, j1 = max(0, int((cy - hh) // cell)), min(ny, int(math.ceil((cy + hh) / cell)))
        if i1 > i0 and j1 > j0:
            f[k] = min(INFL_MAX, float(Rm[i0:i1, j0:j1].mean()))
    area = 4 * g.mhw * g.mhh
    free = pl.W * pl.H - area.sum() - float(sum((b[3] - b[1]) * (b[4] - b[2]) for b in g.fbox if b[0] == "front"))
    add = ((f - 1) * area).sum()
    cap = INFL_BUDGET * max(free, 0.0)
    if add > cap > 0:
        f = 1 + (f - 1) * cap / add
    elif cap <= 0:
        f[:] = 1.0
    g.infl = np.maximum(g.infl, np.sqrt(f))  # side factor
    FEEDBACK.append({"source": source, "s": round(time.perf_counter() - ts, 2), "over": round(over, 2),
                     "inflated": int((f > 1.01).sum()), "units": n})


class PinAccess:
    """Pads with at most 1 of ACCESS_DIRS exits at their net's width, for the placer cost. A rectangle model of
    globalroute.pin_access: an exit is a ray from the pad centre to ACCESS_STEP_MM + width beyond the pad corner
    radius, sampled at ACCESS_SAMPLES points from the pad edge; it is open on a layer when no sample lies in a
    foreign pad box (other net, or no net) on that layer grown by the larger net class clearance + width / 2, and every
    sample stays copper_edge + width / 2 inside the board rectangle. A pad's exits are those of its best layer.
    Subjects: pads of routed nets (not a ground pour) with at least 2 pads. Value of a pad: 2 - exits when exits <= 1."""

    def __init__(self, pl):
        from collections import Counter

        from . import pcb
        d, b = pl.d, pl.d.board
        self.pl = pl
        cls = pcb.net_classes(d)
        clr = float(b["clearance_mm"])
        self.edge = float(b.get("copper_edge_mm", pcb.EDGE_CLEARANCE_MM))
        copper = pcb.cu_layers(int(b.get("layers", 2)))
        self.layers = copper
        gn = d.ground_nets() if b.get("gnd_pour") else set()
        P = pl.parts
        net_of = {(r, p): n for n, conns in d.nets().items() for r, p in conns}
        count = Counter(n for (r, p), n in net_of.items() if r in P and p in P[r].pads)
        self.obs, self.subj = {}, {}
        for r, p in P.items():
            for n in p.pads:
                if n in p.nocu:
                    continue
                cu = p.cu.get(n) if p.cu else None
                lays = tuple(k for k, L in enumerate(copper) if cu is None or "*" in cu or L in cu)
                net = net_of.get((r, n))
                c = float(cls[net]["clearance"]) if net in cls else clr
                self.obs.setdefault(r, []).append((n, net, frozenset(lays), c))
                if net and net not in gn and count[net] >= 2 and lays:
                    self.subj.setdefault(r, []).append((n, net, float(cls[net]["width"]), c, lays))
        ws = [s[2] for v in self.subj.values() for s in v] or [float(b["track_mm"])]
        cs = [o[3] for v in self.obs.values() for o in v] or [clr]
        self.grow = max(cs) + max(ws) / 2  # largest growth of an obstacle box
        a = 2 * math.pi / ACCESS_DIRS
        self.dirs = np.array([(math.cos(k * a), math.sin(k * a)) for k in range(ACCESS_DIRS)])
        self.val, self.ext = {}, {}
        self.rq_max = self.grow + max([math.hypot(*(P[r].pad_size or {}).get(s[0], (0.0, 0.0))) / 2 + ACCESS_STEP_MM
                                       + s[2] for r, ss in self.subj.items() for s in ss] or [0.0])

    def _pbox(self, r, n):
        from .placer import _pad_box
        return _pad_box(self.pl.parts[r], n)

    def _extent(self, r):
        """Bounding box of the copper pads of `r`, or None."""
        bs = [self._pbox(r, o[0]) for o in self.obs.get(r, ())]
        if not bs:
            return None
        return min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)

    def exits(self, r, s, buckets) -> int:
        n, net, w, c, lays = s
        pl = self.pl
        x0, y0, x1, y1 = self._pbox(r, n)
        x, y = (x0 + x1) / 2, (y0 + y1) / 2
        hw, hh = (x1 - x0) / 2, (y1 - y0) / 2
        reach = math.hypot(hw, hh) + ACCESS_STEP_MM + w
        dx, dy = self.dirs[:, 0], self.dirs[:, 1]
        with np.errstate(divide="ignore"):
            t0 = np.minimum(np.where(np.abs(dx) > 1e-9, hw / np.abs(dx), np.inf),
                            np.where(np.abs(dy) > 1e-9, hh / np.abs(dy), np.inf))
        t = t0[:, None] + (reach - t0)[:, None] * np.linspace(0.0, 1.0, ACCESS_SAMPLES)[None, :]
        px, py = x + dx[:, None] * t, y + dy[:, None] * t  # [dirs, samples]
        m = self.edge + w / 2
        inside = (px >= m) & (px <= pl.W - m) & (py >= m) & (py <= pl.H - m)
        G = reach + self.grow
        B = pl.bucket
        cand = set()
        for i in range(int((x - G) // B), int((x + G) // B) + 1):
            for j in range(int((y - G) // B), int((y + G) // B) + 1):
                cand |= buckets.get((i, j), set())
        cand.add(r)
        boxes, lmask = [], []
        for q in cand:
            for o in self.obs.get(q, ()):
                if (q == r and o[0] == n) or (o[1] is not None and o[1] == net) or not (o[2] & set(lays)):
                    continue
                bx = self._pbox(q, o[0])
                g = max(c, o[3]) + w / 2
                if bx[0] - g > x + reach or bx[2] + g < x - reach or bx[1] - g > y + reach or bx[3] + g < y - reach:
                    continue
                boxes.append((bx[0] - g, bx[1] - g, bx[2] + g, bx[3] + g))
                lmask.append([k in o[2] for k in lays])
        best = 0
        if not boxes:
            return int(inside.all(axis=1).sum())
        bx = np.array(boxes)
        lm = np.array(lmask, dtype=bool)  # [K, layers of the pad]
        hit = ((px[..., None] > bx[:, 0]) & (px[..., None] < bx[:, 2]) & (py[..., None] > bx[:, 1])
               & (py[..., None] < bx[:, 3]))  # [dirs, samples, K]
        for li in range(len(lays)):
            blocked = hit[..., lm[:, li]].any(axis=-1) if lm[:, li].any() else np.zeros_like(inside)
            best = max(best, int((inside & ~blocked).all(axis=1).sum()))
            if best > 1:
                break
        return best

    @staticmethod
    def value(k: int) -> float:
        return float(max(0, 2 - k))

    def init(self, buckets) -> float:
        self.val = {(r, s[0]): self.value(self.exits(r, s, buckets)) for r, ss in self.subj.items() for s in ss}
        self.ext = {r: self._extent(r) for r in self.obs}
        return sum(self.val.values())

    def full(self, buckets) -> float:
        return sum(self.value(self.exits(r, s, buckets)) for r, ss in self.subj.items() for s in ss)

    def flagged(self) -> int:
        return sum(1 for v in self.val.values() if v > 0)

    def update(self, moved, log) -> float:
        """Recompute the pads whose exits a move of `moved` can change; returns the change of the total."""
        pl = self.pl
        regions = []
        for r in moved:
            old, new = self.ext.get(r), self._extent(r)
            log.append(("pa_ext", r, old))
            self.ext[r] = new
            regions += [e for e in (old, new) if e]
        if not regions:
            return 0.0
        G = self.rq_max  # a subject pad farther than its reach + grow from every region cannot change
        B = pl.bucket
        cand = set(moved)
        for e in regions:
            for i in range(int((e[0] - G) // B), int((e[2] + G) // B) + 1):
                for j in range(int((e[1] - G) // B), int((e[3] + G) // B) + 1):
                    cand |= pl.buckets.get((i, j), set())
        mv = set(moved)
        delta = 0.0
        for q in cand:
            for s in self.subj.get(q, ()):
                if q not in mv:
                    bx = self._pbox(q, s[0])
                    cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
                    rq = math.hypot((bx[2] - bx[0]) / 2, (bx[3] - bx[1]) / 2) + ACCESS_STEP_MM + s[2] + self.grow
                    if not any(e[0] - rq <= cx <= e[2] + rq and e[1] - rq <= cy <= e[3] + rq for e in regions):
                        continue
                key = (q, s[0])
                v = self.value(self.exits(q, s, pl.buckets))
                old = self.val.get(key, 0.0)
                if v != old:
                    log.append(("pa", key, old))
                    self.val[key] = v
                    delta += v - old
        return delta


# ------------------------------------------------------------------ routing


def router(design, libs, src, nets=None) -> dict | None:
    """For pcb.krt_route under HWFLOW_FLOW=asic: {necked: {net: {need_mm, plain_mm}}, rank: {net: fraction of exits
    open at its width on its worst pad}} from globalroute.pin_access on the board `src`; None when off or not
    buildable."""
    if not on(design) or not (opt("neck") or opt("order")):
        return None
    from pathlib import Path

    from . import corridor, globalroute, pcb, routing
    try:
        if libs is None:
            from .libs import Libs
            libs = Libs(design=design)
        t = time.time()
        m = corridor.Model(design, libs, board=pcb.read_board(Path(src)))
        acc = globalroute.pin_access(design, m)
        cls = pcb.net_classes(design)
        floor = routing.neck_floor(design.board)
        want = set(nets) if nets is not None else None
        rank, necked = {}, {}
        for (r, p), a in acc.items():
            n = a["net"]
            if want is not None and n not in want:
                continue
            rank[n] = min(rank.get(n, 1.0), a["n"] / max(a["dirs"], 1))
            w = float(cls.get(n, {}).get("width", design.board["track_mm"]))
            if a["n"] == 0 and a["floor"] > 0 and w > floor + 1e-9:
                necked[n] = {"need_mm": round(w, 3), "plain_mm": 0.0, "pad": f"{r}.{p}"}
        print(f"[asic] pin access {time.time() - t:.1f} s: {len(necked)} nets without an exit at their width"
              + (f" ({', '.join(sorted(necked))})" if necked else "") + " route narrow first")
        return {"necked": necked if opt("neck") else {}, "rank": rank if opt("order") else None}
    except Exception as e:  # noqa: BLE001 - experimental: never stops the router
        print(f"[asic] pin access skipped: {type(e).__name__}: {e}")
        return None


def order(rank: dict, nets) -> list[str]:
    """`nets` with the fewest open exits first (stable)."""
    return sorted(nets, key=lambda n: rank.get(n, 1.0))
