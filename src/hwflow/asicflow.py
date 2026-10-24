"""The layout flow of hwflow (docs/layout-flow.md; the experiments behind it: docs/asic-ideas.md).

Placement (Placer.run -> place), per placer seed:

1. macro (MACRO): when the design has layout blocks or units with a courtyard of at least MACRO_AREA x the median
   movable one, a mixed-size global placement of every unit, then the macros are legalized among themselves and the
   fixed parts and held while a second global placement places the other units (RePlAce mixed-size style).
2. global placement (gplace.Global) with routability feedback (DENSITY): every FB_EVERY rounds from round FB_START,
   a RUDY map of the current global placement (wire demand against the cell capacity minus the pad blockage,
   Placer._pad_blockage) inflates the courtyards of the units over overflowing cells.
3. legalization (Placer.legalize).
4. detailed placement: local moves (DP, Placer.detailed).
5. a short low-temperature anneal (ANNEAL) with the placer's terms (corridor, isolation, regions, budgets,
   decoupling; plain RUDY).

Routing (pcb.krt_route -> necked), every router call:

6. pin access (NECK): a net with a pad without an exit at its width, but with one at the board minimum
   (pinaccess.pin_access), routes in pcb.krt_route's narrow-first `necked` path.
7. KRT detailed routing with the existing neck-down, seed route, region-split routing, repair, prune and
   min_width_mm (pcb.py, regionroute.py, repair.py).

Each stage has a switch HWFLOW_ASIC_<NAME>=0 that turns it off, for debugging only. `HWFLOW_FLOW=legacy` (or
`placement: {flow: legacy}` in design.yaml) runs the earlier plain annealing instead of stages 1-5 and skips stage 6:
a temporary escape hatch, to be removed after two more routed designs confirm this flow.

Nothing here is tied to a design: every input is the board's rules and placement."""
from __future__ import annotations

import math
import time

from .core.settings import get_settings

import numpy as np

MACRO_AREA = 10.0     # a unit with a courtyard >= this x the median movable courtyard (or a layout block) is a macro
FB_START, FB_EVERY = 2, 2  # routability feedback after global placement rounds 2, 4 and 6 (of 8)
INFL_MAX = 2.0        # largest courtyard area inflation of one unit
INFL_BUDGET = 0.25    # the added (inflated) area is at most this share of the free board area
ANNEAL_SHARE = 0.25   # short anneal: this share of the per-seed iterations
ANNEAL_T0 = 0.03      # its start temperature, as a share of 2 x max(W, H)
FEEDBACK: list = []   # per feedback: {source, s, over, inflated, units} (docs/layout-flow.md)

DEFAULT_FLOW = "asic"


def flow(design=None) -> str:
    v = get_settings().layout.flow
    if v is None:
        v = ((getattr(design, "raw", None) or {}).get("placement") or {}).get("flow", "")
    return str(v or DEFAULT_FLOW).strip().lower()


def on(design=None) -> bool:
    return flow(design) == "asic"


def opt(name: str) -> bool:
    """Stage switch of the layout flow: HWFLOW_ASIC_<NAME>=0 turns that stage off (all on by default; debugging)."""
    return get_settings().layout.asic_on(name)


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
    """The placement stages of the layout flow on placer `pl` (Placer.run)."""
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
    if not opt("density"):
        return None

    def fb(g):
        ts = time.perf_counter()
        _inflate(g, *rudy_map(pl), "rudy")
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


# ------------------------------------------------------------------ routing


def necked(design, libs, src, nets=None) -> dict:
    """For pcb.krt_route: {net: {need_mm, plain_mm, pad}} of the nets (of `nets`, or all) with a pad that has no exit
    at the net's width but at least one at the board minimum (pinaccess.pin_access on the board `src`). They route
    narrow first. Empty under HWFLOW_FLOW=legacy, with HWFLOW_ASIC_NECK=0, or when the model cannot be built."""
    if not on(design) or not opt("neck"):
        return {}
    from pathlib import Path

    from . import corridor, pcb, pinaccess, routing
    try:
        if libs is None:
            from .core.libs import Libs
            libs = Libs(design=design)
        t = time.time()
        m = corridor.Model(design, libs, board=pcb.read_board(Path(src)))
        acc = pinaccess.pin_access(design, m)
        cls = pcb.net_classes(design)
        floor = routing.neck_floor(design.board)
        want = set(nets) if nets is not None else None
        out = {}
        for (r, p), a in acc.items():
            n = a["net"]
            if want is not None and n not in want:
                continue
            w = float(cls.get(n, {}).get("width", design.board["track_mm"]))
            if a["n"] == 0 and a["floor"] > 0 and w > floor + 1e-9:
                out[n] = {"need_mm": round(w, 3), "plain_mm": 0.0, "pad": f"{r}.{p}"}
        print(f"[layout] pin access {time.time() - t:.1f} s: {len(out)} nets without an exit at their width"
              + (f" ({', '.join(sorted(out))}) route narrow first" if out else ""))
        return out
    except Exception as e:  # noqa: BLE001 - a pre-route hint: never stops the router
        print(f"[layout] pin access skipped: {type(e).__name__}: {e}")
        return {}
