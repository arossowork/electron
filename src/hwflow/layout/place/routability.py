"""Routability prediction from a placement, before any detailed routing (docs/layout-flow.md, "Routability gate").

A deterministic raster model of the copper (numpy and scipy.ndimage only), and from it three estimates that look
where the detailed router (KRT) really fails, which is not channel capacity at g-cell scale (docs/asic-ideas.md):

1. Window cuts (escape density). Every square window of side k (WINDOWS_MM) at every position of a POS_MM grid is
   a cut: a net with pads both inside and outside the window must cross its border at least once. Demand = the
   number of such nets (each crossing takes w + c of the border). Supply = the tracks that fit across the border
   on each copper layer: the border's free length for a track centre line (obstacles grown by c + w/2, from the
   raster's distance transform) / (w + c). Pads sit on the outer layers, so the tracks of the other layers count
   only up to the via sites inside the window (vias needed to reach them). ratio = demand / supply; a window over
   RATIO_HOT is a hot spot. This is the local version of the max-flow/min-cut bound (cutcap) with the real widths
   and the pads of the neighbours, so dense escape around fine-pitch parts shows up, which the global router's
   g-cells hide (they count a pad cell's edges as free pin access).
2. Pin access (pinaccess.pin_access): pads with at most one of the 8 exit directions open at the net's width.
3. Corridors (corridor.margin): the constrained nets (isolation between_mm, region nets, net layers) whose widest
   no-neck corridor is less than CORRIDOR_MARGIN_MM wider than the net (the router keeps routing.ISO_SLACK more and
   works on a grid, so a corridor the proof just closes is not one KRT finds).

Output (`predict`): the window ratio map (overflow map), hot spots (connected windows over RATIO_HOT, with the parts
in them), the hard nets (a net is hard when one of its pads sits in a window at RATIO_HARD or more; pads with <= 1 exit
and corridors under the margin are listed), the predicted number of hard nets, and a verdict: `routable`, `marginal` or `not routable` with this
layer count and area. The thresholds are calibrated on routed boards (scripts/routab_bench.py, docs/layout-flow.md).

The raster (`Raster`) is reusable: per copper layer, pad copper labelled by net, hard obstacles (outline, keep-outs
that forbid tracks, holes), the Euclidean distance transform of the free space (local corridor half width), its
ridge (channel centre lines) and connected components (`Raster.free`, `.edt`, `.ridge`, `.components`).

Nothing here is specific to a design: every input is the board's rules and placement."""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
from scipy import ndimage

from ...core.settings import get_settings

CELLS_MAX = 3.0e6          # fine raster cells per layer at most (side 0.05 mm or coarser)
RES_MIN = 0.05
POS_MM = 0.5               # window positions every POS_MM
WINDOWS_MM = (2.0, 4.0, 8.0)  # window sides
# Calibrated on routed boards (docs/layout-flow.md, "Routability calibration"): the clean boards peak at a window
# ratio of 1.3-2.0, the failing ones at 3.6-8; a net with a pad in a window at >= 3 is open far more often.
RATIO_HOT = 3.0            # a hot window: its crossings are at least this many times its border's tracks
RATIO_HARD = RATIO_HOT     # the window ratio at which a net is hard and the placer inflates (asicflow.escape_map)
ACCESS_MAX = 1             # a pad with at most this many exit directions at its net width (listed; not a hard net
                           # alone: the neck-down routes many of them)
CORRIDOR_MARGIN_MM = 0.3   # a constrained net whose no-neck corridor is less than this wider than the net (listed
                           # in the message; not a hard net: the one failure it flagged had another cause)
# verdict: no hard net is routable; up to HARD_SHARE_FAIL of the routed nets hard is marginal (the boards that end
# with 1 open net: 5 %); from it, not routable (the boards with 20+ open nets: 55-61 %)
HARD_SHARE_MARGINAL = 0.0
HARD_SHARE_FAIL = 0.25
# The congestion gate (`gate`, a FAIL of `hwflow floorplan`): windows at RATIO_FAIL or more over AREA_FAIL_MM2 of the
# board. Calibrated on quick placements (4 placer seeds each, budget widths on) against the real layout outcome: the
# boards that routed (home-thermostat-4, thermo-e2e-4, thermo-e2e-4-live) peak at 3.6-4.7 with no cell at 5 or more;
# thermo-e2e-6 (27-41 unrouted on 2 and 4 layers) peaks at 8.3-18.6 with 40-85 mm2 at 7 or more on every seed, and its
# routed-attempt placements at 9.3-12.8. The hard-net share does not separate them (thermo-e2e-4 routed clean at 32 %).
RATIO_FAIL = 6.0
AREA_FAIL_MM2 = 8.0
VERDICTS = ("routable", "marginal", "not routable")


# ------------------------------------------------------------------ raster primitives


class Raster:
    """The copper rules of a placement as rasters, one per copper layer (cell side `res` mm, index [ix, iy])."""

    def __init__(self, design, libs, parts=None, board=None, res: float | None = None, model=None):
        from . import corridor
        from .barrier import HOLE_MM
        self.design = design
        m = model or corridor.Model(design, libs, parts=parts, board=board)
        self.model = m
        b = design.board
        self.W, self.H = float(b["width_mm"]), float(b["height_mm"])
        self.res = res or max(RES_MIN, round(math.sqrt(self.W * self.H / CELLS_MAX) / 0.01) * 0.01)
        r = self.res
        self.nx, self.ny = int(math.ceil(self.W / r)), int(math.ceil(self.H / r))
        self.layers = list(m.copper)
        self.track = float(b["track_mm"])
        self.clr = float(b["clearance_mm"])
        self.via = float(b["via_dia_mm"])
        nets = sorted({p["net"] for p in m.pads if p["net"]})
        self.net_id = {n: i + 1 for i, n in enumerate(nets)}
        self.nets = [None] + nets
        self.xs = (np.arange(self.nx) + 0.5) * r
        self.ys = (np.arange(self.ny) + 0.5) * r
        nL = len(self.layers)
        self.pad = np.zeros((nL, self.nx, self.ny), np.int32)   # net id of the pad copper, -1 copper of no net
        self.hard = np.zeros((nL, self.nx, self.ny), bool)      # outline outside, keep-outs (all nets), holes
        self.hole = np.zeros((self.nx, self.ny), bool)
        for p in m.pads:
            v = self.net_id.get(p["net"], -1)
            mask, sl = self.mask(p["own"])
            for L in p["layers"]:
                li = self.layers.index(L)
                cur = self.pad[li][sl]
                cur[mask] = v
        for h in m.holes:
            mask, sl = self.mask(h.buffer(HOLE_MM))
            self.hole[sl] |= mask
        import shapely
        X, Y = np.meshgrid(self.xs, self.ys, indexing="ij")
        inside = shapely.contains_xy(m.outline, X, Y)
        for li, L in enumerate(self.layers):
            self.hard[li] = ~inside | self.hole
            for a in m.areas:
                if L in a["layers"] and "tracks" in a["forbids"]:
                    mask, sl = self.mask(a["poly"])
                    self.hard[li][sl] |= mask
        self.via_hard = ~inside | self.hole
        for a in m.areas:
            if "vias" in a["forbids"]:
                mask, sl = self.mask(a["poly"])
                self.via_hard[sl] |= mask
        self._edt = {}

    def mask(self, geom):
        """(bool mask, slice) of the cells whose centre lies in `geom`, over its bounding box."""
        import shapely
        r = self.res
        x0, y0, x1, y1 = geom.bounds
        i0, j0 = max(0, int(x0 / r)), max(0, int(y0 / r))
        i1, j1 = min(self.nx, int(math.ceil(x1 / r)) + 1), min(self.ny, int(math.ceil(y1 / r)) + 1)
        sl = (slice(i0, max(i0, i1)), slice(j0, max(j0, j1)))
        if i1 <= i0 or j1 <= j0:  # off the raster: an empty mask of the slice's shape
            return np.zeros((max(0, i1 - i0), max(0, j1 - j0)), bool), sl
        X, Y = np.meshgrid(self.xs[i0:i1], self.ys[j0:j1], indexing="ij")
        return shapely.contains_xy(geom, X, Y), sl

    def obstacle(self, li: int) -> np.ndarray:
        return (self.pad[li] != 0) | self.hard[li]

    def edt(self, li: int) -> np.ndarray:
        """Distance (mm) of every cell to the nearest obstacle copper or hard area of layer li (0 inside)."""
        if li not in self._edt:
            self._edt[li] = ndimage.distance_transform_edt(~self.obstacle(li)) * self.res
        return self._edt[li]

    def free(self, li: int, w: float | None = None, clr: float | None = None) -> np.ndarray:
        """Cells where the centre line of a track of width w (board track) may lie on layer li: clr + w/2 from every
        obstacle (a morphological dilation by the distance transform)."""
        w = self.track if w is None else w
        c = self.clr if clr is None else clr
        return self.edt(li) >= c + w / 2 - 1e-9

    def ridge(self, li: int) -> np.ndarray:
        """Channel centre lines of layer li: free cells where the distance map is a local maximum across x or y."""
        D = self.edt(li)
        mx = ndimage.maximum_filter1d(D, 3, axis=0)
        my = ndimage.maximum_filter1d(D, 3, axis=1)
        return (D > 0) & ((D >= mx) | (D >= my))

    def components(self, li: int, w: float | None = None, clr: float | None = None):
        """(labels, count) of the connected free regions of a track of width w on layer li."""
        return ndimage.label(self.free(li, w, clr))

    def via_free(self) -> np.ndarray:
        """Cells where a via centre may lie: via_dia/2 + clearance from every pad and hard area on every layer."""
        obst = self.via_hard.copy()
        for li in range(len(self.layers)):
            obst |= self.pad[li] != 0
        return ndimage.distance_transform_edt(~obst) * self.res >= self.via / 2 + self.clr - 1e-9

    def arrays(self) -> dict:
        """The raster channels for the dataset (npz): pad net ids, hard areas, distance maps per layer."""
        return {"pad": self.pad, "hard": self.hard, "edt": np.stack([self.edt(i) for i in range(len(self.layers))]
                                                                   ).astype(np.float16)}


# ------------------------------------------------------------------ window cuts


def _windows(R: Raster, nets: dict, pos: float, k: float, widths: dict):
    """Per window of side k at the positions of a `pos` grid (lower-left corner at (i, j) * pos): (crossing nets,
    the crossings' width (sum of w + c), the tracks that fit across its border on each layer, via sites inside)."""
    gx, gy = int(math.ceil(R.W / pos)), int(math.ceil(R.H / pos))
    kk = max(1, int(round(k / pos)))
    nwx, nwy = max(1, gx - kk + 1), max(1, gy - kk + 1)
    cross = np.zeros((nwx, nwy))
    need = np.zeros((nwx, nwy))
    for net, pts in nets.items():
        if len(pts) < 2:
            continue
        ci = np.clip((np.array([p[0] for p in pts]) / pos).astype(int), 0, gx - 1)
        cj = np.clip((np.array([p[1] for p in pts]) / pos).astype(int), 0, gy - 1)
        i0, i1, j0, j1 = ci.min(), ci.max(), cj.min(), cj.max()
        # windows that hold at least one pad: lower-left corner within kk-1 cells below/left of a pad
        a0, b0 = max(0, i0 - kk + 1), max(0, j0 - kk + 1)
        a1, b1 = min(nwx, i1 + 1), min(nwy, j1 + 1)
        if a1 <= a0 or b1 <= b0:
            continue
        hold = np.zeros((a1 - a0, b1 - b0), bool)
        for i, j in set(zip(ci.tolist(), cj.tolist())):
            hold[max(0, i - kk + 1) - a0:min(nwx, i + 1) - a0, max(0, j - kk + 1) - b0:min(nwy, j + 1) - b0] = True
        allin = np.zeros_like(hold)
        if i1 - i0 < kk and j1 - j0 < kk:  # windows that hold every pad: corner in [i1-kk+1, i0] x [j1-kk+1, j0]
            allin[max(0, i1 - kk + 1) - a0:min(nwx, i0 + 1) - a0, max(0, j1 - kk + 1) - b0:min(nwy, j0 + 1) - b0] = True
        c = hold & ~allin
        cross[a0:a1, b0:b1] += c
        need[a0:a1, b0:b1] += c * (widths.get(net, R.track) + R.clr)
    # supply: free centre-line length along the four sides, per layer, as tracks of the board width
    pitch = R.track + R.clr
    step = max(1, int(round(pos / R.res)))
    tracks = []
    for li in range(len(R.layers)):
        F = R.free(li).astype(np.int32)
        Cx = np.concatenate([np.zeros((1, R.ny), np.int32), np.cumsum(F, axis=0)], axis=0)  # along x per row
        Cy = np.concatenate([np.zeros((R.nx, 1), np.int32), np.cumsum(F, axis=1)], axis=1)  # along y per column
        X0 = np.minimum(np.arange(nwx) * step, R.nx - 1)
        X1 = np.minimum(X0 + kk * step, R.nx - 1)
        Y0 = np.minimum(np.arange(nwy) * step, R.ny - 1)
        Y1 = np.minimum(Y0 + kk * step, R.ny - 1)
        bottom = Cx[X1[:, None], Y0[None, :]] - Cx[X0[:, None], Y0[None, :]]
        top = Cx[X1[:, None], Y1[None, :]] - Cx[X0[:, None], Y1[None, :]]
        left = Cy[X0[:, None], Y1[None, :]] - Cy[X0[:, None], Y0[None, :]]
        right = Cy[X1[:, None], Y1[None, :]] - Cy[X1[:, None], Y0[None, :]]
        tracks.append((bottom + top + left + right) * R.res / pitch)
    V = R.via_free().astype(np.int32)
    S = np.zeros((R.nx + 1, R.ny + 1), np.int32)
    S[1:, 1:] = V.cumsum(0).cumsum(1)
    X0 = np.minimum(np.arange(nwx) * step, R.nx)
    X1 = np.minimum(X0 + kk * step, R.nx)
    Y0 = np.minimum(np.arange(nwy) * step, R.ny)
    Y1 = np.minimum(Y0 + kk * step, R.ny)
    cnt = S[X1[:, None], Y1[None, :]] - S[X0[:, None], Y1[None, :]] - S[X1[:, None], Y0[None, :]] + S[X0[:, None], Y0[None, :]]
    vias = cnt * R.res ** 2 / (R.via + R.clr) ** 2  # via sites: packing of discs of via_dia + clearance
    # single-layer (SMD) pad copper per outer layer inside each window: which face the window's pads escape on
    side = []
    for li in (0, len(R.layers) - 1):
        o = len(R.layers) - 1 - li
        M = ((R.pad[li] != 0) & (R.pad[o] == 0)).astype(np.int32)
        T = np.zeros((R.nx + 1, R.ny + 1), np.int32)
        T[1:, 1:] = M.cumsum(0).cumsum(1)
        side.append(T[X1[:, None], Y1[None, :]] - T[X0[:, None], Y1[None, :]] - T[X1[:, None], Y0[None, :]]
                    + T[X0[:, None], Y0[None, :]])
    return cross, need, tracks, vias, kk, side


def _supply(tracks, vias, side):
    """Tracks across a window's border: those of the pad layer, plus those of the other layers up to the via sites
    inside (a pad reaches another layer through a via). The pad layer is the face of the window's SMD pad copper,
    F.Cu or B.Cu, weighted by the pad area on each (a back-side window escapes on B.Cu; before, F.Cu was taken as
    the pad layer everywhere, so a back-side board counted its nearly free front layer as the escape layer); a window
    without SMD pads (through-hole pads only, or none) takes the better face."""
    total = sum(tracks)
    sF = tracks[0] + np.minimum(total - tracks[0], vias)
    sB = tracks[-1] + np.minimum(total - tracks[-1], vias)
    cF, cB = side[0].astype(float), side[1].astype(float)
    n = cF + cB
    return np.where(n > 0, (cF * sF + cB * sB) / np.maximum(n, 1.0), np.maximum(sF, sB))


def window_map(R: Raster, design, libs=None) -> dict:
    """The window cuts at every side of WINDOWS_MM: per window size the ratio map (crossing width / border
    capacity), and the worst ratio over the sizes per position cell (`ratio`, on the POS_MM grid)."""
    from ..board import pcb
    classes = pcb.net_classes(design)
    gnd = design.ground_nets() if design.board.get("gnd_pour") else set()
    nets: dict = {}
    for p in R.model.pads:
        n = p["net"]
        if n and n not in gnd:
            c = p["own"].centroid
            nets.setdefault(n, []).append((c.x, c.y))
    widths = {n: float(classes.get(n, {}).get("width", R.track)) for n in nets}
    pitch = R.track + R.clr
    gx, gy = int(math.ceil(R.W / POS_MM)), int(math.ceil(R.H / POS_MM))
    worst = np.zeros((gx, gy))
    sizes, maps = [], []
    for k in WINDOWS_MM:
        if k > min(R.W, R.H):
            continue
        cross, need, tracks, vias, kk, side = _windows(R, nets, POS_MM, k, widths)
        supply = _supply(tracks, vias, side) * pitch  # mm of border, in w + c units of the board track
        ratio = need / np.maximum(supply, pitch)
        sizes.append({"k": k, "max": round(float(ratio.max()), 3), "hot": int((ratio > RATIO_HOT).sum()),
                      "cross_max": int(cross.max())})
        maps.append((k, kk, ratio))
        # every position cell under a window takes the window's ratio (worst over windows and sizes)
        full = np.zeros((gx, gy))
        full[:ratio.shape[0], :ratio.shape[1]] = ratio
        r2 = ndimage.maximum_filter(full, size=kk, origin=(kk - 1) // 2, mode="constant")
        worst = np.maximum(worst, r2)
    # maps: per window size (k mm, k in position cells, ratio per window lower-left corner) for hotspot.py
    return {"ratio": worst, "sizes": sizes, "nets": nets, "widths": widths, "maps": maps}


# ------------------------------------------------------------------ placed board


def board_of(parts) -> dict:
    """A placed board without copper (pcb.read_board shape) from placer parts."""
    return {"footprints": {r: {"x": p.x, "y": p.y, "r": p.r} for r, p in parts.items()}, "segments": [], "vias": []}


# ------------------------------------------------------------------ exact bounds (proofs)
#
# Each bound is a necessary condition: when it fails, no router can route the placement, whatever its order or
# time. A supply is an upper bound (centre lines of board-width tracks at pitch w + c in the free runs of the line,
# obstacles grown by c + w/2, plus one crossing per pad copper run that the line cuts, where a net leaves through
# its own pad), a demand a lower bound (each net with pads on both sides crosses at least once, taking its w + c).

RING_PADS_MIN = 8      # parts with at least this many signal pads get a ring cut around their pads
RING_GROW_MM = None    # the ring lies this far outside the part's pad copper (None: clearance + track)


def _runs(v: np.ndarray, res: float, pitch: float) -> int:
    """Track centre lines that fit along the free cells `v` of a line: floor(run / pitch) + 1 per run."""
    if not v.any():
        return 0
    d = np.diff(np.concatenate(([0], v.astype(np.int8), [0])))
    s, e = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    return int((((e - s) * res) // pitch + 1).sum())


def _nruns(v: np.ndarray) -> int:
    return int((np.diff(np.concatenate(([0], v.astype(np.int8), [0]))) == 1).sum())


def line_cuts(R: Raster, wm: dict) -> dict:
    """Channel density (the left-edge bound) across every straight line of the board, x and y, every POS_MM: the
    nets whose pads lie on both sides (demand, in board-track units) against the tracks that cross the line on all
    layers (vias may sit on either side, so every layer counts). Returns the worst line per axis and every line
    whose demand exceeds its supply (a proof)."""
    pitch = R.track + R.clr
    nets, widths = wm["nets"], wm["widths"]
    spans = [(min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts),
              (widths.get(n, R.track) + R.clr) / pitch) for n, pts in nets.items() if len(pts) >= 2]
    F = [R.free(li) for li in range(len(R.layers))]
    P = [R.pad[li] != 0 for li in range(len(R.layers))]
    out = {"x": None, "y": None, "proofs": []}
    for ax, n_line, length in (("x", R.nx, R.W), ("y", R.ny, R.H)):
        step = max(1, int(round(POS_MM / R.res)))
        worst = None
        for c in range(step, n_line - step, step):
            at = (c + 0.5) * R.res
            need = sum(s[4] for s in spans if (s[0] < at < s[1] if ax == "x" else s[2] < at < s[3]))
            if need <= 0:
                continue
            sup = 0
            for li in range(len(R.layers)):
                f = F[li][c, :] if ax == "x" else F[li][:, c]
                p = P[li][c, :] if ax == "x" else P[li][:, c]
                sup += _runs(f, R.res, pitch) + _nruns(p)
            row = {"at": round(at, 2), "need": round(need, 1), "supply": sup, "ratio": round(need / max(sup, 1), 3)}
            if worst is None or row["ratio"] > worst["ratio"]:
                worst = row
            if need > sup:
                out["proofs"].append(f"line {ax} = {at:.1f} mm: {need:.0f} nets cross it, {sup} tracks fit across it")
        out[ax] = worst
    return out


def ring_cuts(R: Raster, wm: dict) -> list[dict]:
    """A min-cut around every part with RING_PADS_MIN or more signal pads: the rectangle RING_GROW_MM outside its
    pad copper. Demand: the nets with pads inside and outside it. Supply: the tracks across its border on the pad
    layer, plus on each other layer at most the via sites and the pads of that layer inside it (a track leaves on
    another layer only through one of them). A ring with demand over supply is a proof (`proof` true)."""
    pitch = R.track + R.clr
    g = RING_GROW_MM if RING_GROW_MM is not None else R.clr + R.track
    gnd = R.design.ground_nets() if R.design.board.get("gnd_pour") else set()
    by_ref: dict = {}
    for p in R.model.pads:
        by_ref.setdefault(p["ref"], []).append(p)
    V = R.via_free()
    out = []
    for ref, pads in sorted(by_ref.items()):
        sig = [p for p in pads if p["net"] and p["net"] not in gnd]
        if len(sig) < RING_PADS_MIN:
            continue
        x0 = min(p["own"].bounds[0] for p in pads) - g
        y0 = min(p["own"].bounds[1] for p in pads) - g
        x1 = max(p["own"].bounds[2] for p in pads) + g
        y1 = max(p["own"].bounds[3] for p in pads) + g
        i0, j0 = max(0, int(x0 / R.res)), max(0, int(y0 / R.res))
        i1, j1 = min(R.nx - 1, int(math.ceil(x1 / R.res))), min(R.ny - 1, int(math.ceil(y1 / R.res)))
        if i1 - i0 < 2 or j1 - j0 < 2:
            continue
        need = 0.0
        for n, pts in wm["nets"].items():
            ins = [x0 < x < x1 and y0 < y < y1 for x, y in pts]
            if any(ins) and not all(ins):
                need += (wm["widths"].get(n, R.track) + R.clr) / pitch
        if need <= 0:
            continue

        def border(A, cnt):
            return (cnt(A[i0:i1 + 1, j0]) + cnt(A[i0:i1 + 1, j1]) + cnt(A[i0, j0 + 1:j1]) + cnt(A[i1, j0 + 1:j1]))
        runs = lambda v: _runs(v, R.res, pitch)  # noqa: E731
        layer_sup = []
        for li in range(len(R.layers)):
            layer_sup.append(border(R.free(li), runs) + border(R.pad[li] != 0, _nruns))
        vias = float(V[i0 + 1:i1, j0 + 1:j1].sum()) * R.res ** 2 / (R.via + R.clr) ** 2
        sup = layer_sup[0]
        for li in range(1, len(R.layers)):
            L = R.layers[li]
            own = sum(1 for p in pads if L in p["layers"]) + sum(
                1 for p in R.model.pads if p["ref"] != ref and L in p["layers"]
                and x0 < p["own"].centroid.x < x1 and y0 < p["own"].centroid.y < y1)
            sup += min(layer_sup[li], int(vias) + own)
        out.append({"ref": ref, "at": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)], "need": round(need, 1),
                    "supply": int(sup), "ratio": round(need / max(sup, 1), 3), "proof": need > sup})
    out.sort(key=lambda r: -r["ratio"])
    return out


def forced_corridors(cor_rows: list[dict]) -> list[str]:
    """Constraint propagation over the constrained nets (corridor.margin): a net whose widest corridor from pad to
    pad, with every isolation, region and layer rule applied, is narrower than its width has no route at that width
    (a proof); a margin under CORRIDOR_MARGIN_MM is a forced corridor (listed, not a proof)."""
    return [f"net {c['net']}: its widest corridor is {c['plain']:.2f} mm for a {c['width']:g} mm track"
            + (f" at {c['at']}" if c.get("at") else "") for c in cor_rows if c["margin"] < 0]


def packing_bounds(design, parts) -> list[str]:
    """2D packing lower bounds per floorplan region and for the board: the members' courtyard area over the
    region's area, and any member that fits the region's bounding box in neither orientation. Each is a proof."""
    from ...core import floorplan
    out = []
    try:
        rr, _ = floorplan.placer_regions(design, parts)
    except Exception:  # noqa: BLE001 - no regions, no region bounds
        rr = []
    for g in rr:
        poly = g.get("poly")
        mem = [r for r in g.get("members") or () if r in parts]
        if poly is None or poly.is_empty or not mem:
            continue
        bx0, by0, bx1, by1 = poly.bounds
        bw, bh = bx1 - bx0, by1 - by0
        area = 0.0
        for r in mem:
            x0, y0, x1, y1 = parts[r].box()
            w, h = x1 - x0, y1 - y0
            area += w * h
            if not ((w <= bw + 1e-6 and h <= bh + 1e-6) or (h <= bw + 1e-6 and w <= bh + 1e-6)):
                out.append(f"region {g['name']}: {r} ({w:.1f} x {h:.1f} mm) fits its {bw:.1f} x {bh:.1f} mm box in "
                           "neither orientation")
        if area > poly.area + 1e-6:
            out.append(f"region {g['name']}: its members' courtyards take {area:.0f} mm2 of its {poly.area:.0f} mm2")
    return out


# ------------------------------------------------------------------ prediction


def predict(design, libs, parts=None, board=None, corridors: bool = True, access: bool = True) -> dict:
    """The routability prediction of a placement (module text). `parts`: placer parts at their positions, or
    `board`: pcb.read_board of a placed board."""
    from . import corridor, pinaccess
    t0 = time.time()
    tm = {}
    m = corridor.Model(design, libs, parts=parts, board=board)
    R = Raster(design, libs, model=m)
    tm["raster_s"] = round(time.time() - t0, 2)
    t = time.time()
    wm = window_map(R, design, libs)
    tm["windows_s"] = round(time.time() - t, 2)
    gnd = design.ground_nets() if design.board.get("gnd_pour") else set()
    routed = sorted(n for n, pts in wm["nets"].items() if len(pts) >= 2)
    hard: dict[str, list[str]] = {}
    # hot spots: connected position cells over RATIO_HOT
    hot = wm["ratio"] > RATIO_HOT
    lab, nh = ndimage.label(hot)
    spots = []
    pos_of = {}
    for p in m.pads:
        c = p["own"].centroid
        pos_of.setdefault((int(c.x / POS_MM), int(c.y / POS_MM)), set()).add((p["ref"], p["net"]))
    for k in range(1, nh + 1):
        cells = np.argwhere(lab == k)
        refs, nets_in = set(), set()
        for i, j in cells:
            for ref, net in pos_of.get((int(i), int(j)), ()):
                refs.add(ref)
                if net and net not in gnd:
                    nets_in.add(net)
        peak = float(wm["ratio"][lab == k].max())
        x0, y0 = cells.min(0) * POS_MM
        x1, y1 = (cells.max(0) + 1) * POS_MM
        spots.append({"at": [round(float(x0), 1), round(float(y0), 1), round(float(x1), 1), round(float(y1), 1)],
                      "peak": round(peak, 2), "cells": len(cells), "refs": sorted(refs), "nets": sorted(nets_in)})
    spots.sort(key=lambda s: -s["peak"] * s["cells"])
    acc_rows, acc = [], {}
    if access:
        t = time.time()
        try:
            acc = pinaccess.pin_access(design, m)
        except Exception as e:  # noqa: BLE001 - the window cuts stand without it
            acc, tm["access_error"] = {}, f"{type(e).__name__}: {e}"
        for (ref, pad), a in acc.items():
            if a["net"] in gnd or a["net"] not in wm["nets"]:
                continue
            if a["n"] <= ACCESS_MAX:
                acc_rows.append({"ref": ref, "pad": pad, "net": a["net"], "n": a["n"], "floor": a["floor"]})
        tm["access_s"] = round(time.time() - t, 2)
    cor_rows = []
    if corridors and corridor.constrained(design):
        t = time.time()
        try:
            for row in corridor.margin(design, libs, parts=parts, board=board):
                mg = row["plain"] - row["width"]
                # listed, not a hard net: on ht4 seed 11, AC_ROOM1 (margin 0.15 mm) stayed open with the margin
                # raised to 0.73 mm by moving R8, so a small margin did not cause that failure
                cor_rows.append({**row, "margin": round(mg, 3)})
        except Exception as e:  # noqa: BLE001
            tm["corridor_error"] = f"{type(e).__name__}: {e}"
        tm["corridor_s"] = round(time.time() - t, 2)
    # per net: the worst window ratio at its pads and its fewest exits (calibration and the router's net order)
    gx, gy = wm["ratio"].shape
    per_net = {}
    for n in routed:
        rs = [float(wm["ratio"][min(int(x / POS_MM), gx - 1), min(int(y / POS_MM), gy - 1)]) for x, y in wm["nets"][n]]
        per_net[n] = {"ratio": round(max(rs), 3)}
        if max(rs) >= RATIO_HARD:
            hard.setdefault(n, []).append(f"window ratio {max(rs):.1f}")
    if access:
        for (ref, pad), a in acc.items():
            if a["net"] in per_net:
                per_net[a["net"]]["exits"] = min(per_net[a["net"]].get("exits", 99), a["n"])
    for row in cor_rows:
        if row["net"] in per_net:
            per_net[row["net"]]["corridor"] = row["margin"]
    # exact bounds: any failed one is a proof that no router routes this placement
    t = time.time()
    lines = line_cuts(R, wm)
    rings = ring_cuts(R, wm)
    proofs = list(lines["proofs"]) + [f"ring around {r['ref']}: {r['need']:.0f} nets leave it, {r['supply']} tracks "
                                      "fit across it" for r in rings if r["proof"]]
    proofs += forced_corridors(cor_rows)
    if parts is not None:
        proofs += packing_bounds(design, parts)
    tm["bounds_s"] = round(time.time() - t, 2)
    n_routed = len(routed)
    share = len(hard) / max(n_routed, 1)
    verdict = VERDICTS[2] if proofs else verdict_of(share)
    res = {"board": [R.W, R.H], "layers": len(R.layers), "res_mm": R.res, "routed_nets": n_routed,
           "hard_nets": len(hard), "hard_share": round(share, 3), "hard": {n: v for n, v in sorted(hard.items())},
           "verdict": verdict, "hot_spots": spots[:20], "n_hot_spots": len(spots),
           "max_ratio": round(float(wm["ratio"].max()), 3), "hot_cells": int(hot.sum()),
           "hot_area_mm2": round(float(hot.sum()) * POS_MM ** 2, 1), "windows": wm["sizes"], "access": acc_rows,
           "corridors": cor_rows, "per_net": per_net, "timing": tm, "proofs": proofs,
           "bounds": {"line_x": lines["x"], "line_y": lines["y"], "rings": rings[:10]},
           "seconds": round(time.time() - t0, 2),
           "_ratio": wm["ratio"], "_raster": R}
    res["_widths"] = wm["widths"]
    res["gate"] = gate(res, design, parts)
    if res["gate"]["fail"]:
        res["verdict"] = VERDICTS[2]
    res["score"] = res["hard_share"]
    res["message"] = message(res)
    return res


def gate(res: dict, design, parts=None) -> dict:
    """The congestion gate of a prediction: {fail, area_mm2, peak, spots}. The cells at RATIO_FAIL or more form the
    spots; each spot names its box, its peak ratio, its parts (movable ones apart), the face its SMD pads are on, and
    the nets that cross its border (widest first, with the width and where a requirement budget set it)."""
    from ..board import pcb
    r = res["_ratio"]
    R = res["_raster"]
    over = r >= RATIO_FAIL
    area = float(over.sum()) * POS_MM ** 2
    out = {"fail": area >= AREA_FAIL_MM2, "area_mm2": round(area, 1), "peak": round(float(r.max()), 2),
           "ratio_fail": RATIO_FAIL, "area_fail_mm2": AREA_FAIL_MM2, "track": R.track, "spots": []}
    if not over.any():
        return out
    widths = res.get("_widths") or {}
    bud = getattr(design, "budget_widths", None) or {}
    own = {}
    if bud:  # the nets' own widths, without the budgets
        design.budget_widths = {}
        try:
            own = {n: float(c.get("width", R.track)) for n, c in pcb.net_classes(design).items()}
        finally:
            design.budget_widths = bud
    lab, n = ndimage.label(over)
    pads = R.model.pads
    spots = []
    for k in range(1, n + 1):
        cells = np.argwhere(lab == k)
        x0, y0 = cells.min(0) * POS_MM
        x1, y1 = (cells.max(0) + 1) * POS_MM
        x0, y0, x1, y1 = (float(v) for v in (x0, y0, x1, y1))
        # the windows behind these cells reach up to the largest window side around them
        g = max(WINDOWS_MM) / 2
        bx = (x0 - g, y0 - g, x1 + g, y1 + g)
        inside, refs, faces = {}, set(), {}
        for p in pads:
            c = p["own"].centroid
            if x0 <= c.x <= x1 and y0 <= c.y <= y1:
                refs.add(p["ref"])
                if len(p["layers"]) == 1:
                    faces[p["layers"][0]] = faces.get(p["layers"][0], 0) + 1
            if p["net"]:
                inside.setdefault(p["net"], [0, 0])[0 if (bx[0] <= c.x <= bx[2] and bx[1] <= c.y <= bx[3]) else 1] += 1
        cross = [m for m, (a, b) in inside.items() if a and b and m in widths]
        cross.sort(key=lambda m: (-widths[m], m))
        nets = [{"net": m, "width": round(widths[m], 2), "budget": bool(bud.get(m) and bud[m] > own.get(m, 0) + 1e-9),
                 "pads": sum(inside[m])} for m in cross]
        movable = sorted(x for x in refs if parts is not None and x in parts and not parts[x].fixed)
        spots.append({"at": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
                      "peak": round(float(r[lab == k].max()), 2), "area_mm2": round(len(cells) * POS_MM ** 2, 1),
                      "refs": sorted(refs), "movable": movable, "nets": nets,
                      "face": max(faces, key=faces.get) if faces else None})
    spots.sort(key=lambda s: -s["area_mm2"] * s["peak"])
    out["spots"] = spots[:5]
    return out


def gate_message(g: dict, layers: int, budgets: dict | None = None) -> str:
    """The FAIL text of the congestion gate: where, how much over, which nets, and the moves that reduce it."""
    sp = g["spots"]
    s = (f"congestion: {g['area_mm2']:g} mm2 of the board where the nets that must cross a window's border need at "
         f"least {g['ratio_fail']:g} x the tracks that fit across it (FAIL from {g['area_fail_mm2']:g} mm2; boards "
         f"that routed stay under 5 x everywhere); peak {g['peak']:.1f} x")
    for i, h in enumerate(sp[:2]):
        wide = [x for x in h["nets"] if x["width"] > g["track"] + 1e-9][:4]
        s += (f"; {'hot spot' if i == 0 else 'next'} x {h['at'][0]:g}-{h['at'][2]:g}, y {h['at'][1]:g}-{h['at'][3]:g} "
              f"mm ({h['area_mm2']:g} mm2, {h['peak']:.1f} x), parts {', '.join(h['refs'][:10])}"
              + (f", SMD pads on {h['face']}" if h["face"] else "")
              + f", {len(h['nets'])} nets cross it")
        if wide:
            s += " (wide: " + ", ".join(
                f"{x['net']} {x['width']:g} mm" + (f" from budget {budgets[x['net']]}" if budgets and x["net"] in budgets
                                                   else " from a budget" if x["budget"] else "")
                + f" to {x['pads']} pads" for x in wide) + ")"
    h = sp[0] if sp else None
    moves = []
    if h and h["movable"]:
        moves.append(f"spread the movable parts {', '.join(h['movable'][:8])} out of the hot spot (a floorplan "
                     "region or placement for them elsewhere)")
    wide = [x for x in h["nets"] if x["budget"] and x["pads"] > 2] if h else []
    if wide:
        moves.append("a budget width applies to the whole net, every pad of it ("
                     + ", ".join(f"{x['net']} {x['width']:g} mm on {x['pads']} pads" for x in wide[:3])
                     + "): give the high-current path its own net (or a pour) so the branches to the small pads stay "
                     "at the board track, or loosen the budget")
    if h and h["face"]:
        other = "F.Cu" if h["face"] == "B.Cu" else "B.Cu"
        moves.append(f"move some of its SMD parts to the other face ({other})")
    moves.append(f"more than {layers} layers or a larger board")
    return s + ". Moves: " + "; ".join(moves)


def rank_on(design=None) -> bool:
    """HWFLOW_ROUTAB_RANK: placer.place keeps, among its legal seeds, the one with the lowest rank_score."""
    v = get_settings().layout.routab_rank
    if v is None:
        v = str(((getattr(design, "raw", None) or {}).get("placement") or {}).get("routability_rank", RANK_DEFAULT))
    return v.strip().lower() not in ("0", "false", "no", "off", "")


RANK_DEFAULT = "0"


def rank_score(design, libs, parts) -> float:
    """The seed ranking score of a placement: the predicted hard-net share (lower is better), the hot area as a
    tie-break. inf when the prediction cannot be made."""
    try:
        r = predict(design, libs, parts=parts)
    except Exception:  # noqa: BLE001 - ranking only
        return math.inf
    return r["hard_share"] + 1e-6 * r["hot_area_mm2"]


RETRY_DEFAULT = "0"
RETRY_OPEN_MAX = 3  # a board-width pass with more nets open after its first try: other net orders do not help


def retry_stop(design, args, n_nets):
    """The net-order retry policy of krt.route_class for one router pass (HWFLOW_ROUTAB_RETRY, or placement:
    {routability_retry: true}): a pass wider than the board track keeps its first try (its open nets go to the
    neck-down, which routes them), a board-width pass retries only while at most RETRY_OPEN_MAX nets are open. None
    (the old policy: every retry) when off."""
    v = get_settings().layout.routab_retry
    if v is None:
        v = str(((getattr(design, "raw", None) or {}).get("placement") or {}).get("routability_retry", RETRY_DEFAULT))
    if v.strip().lower() in ("0", "false", "no", "off", ""):
        return None
    track = float(design.board["track_mm"])
    w = track
    if "--track-width" in args:
        w = float(args[args.index("--track-width") + 1])
    wide = w > track + 1e-9 and "--power-nets" not in args

    def stop(unrouted, tries):
        if wide:
            stop.why = f"a {w:g} mm pass: its open nets go to the neck-down"
            return True
        if unrouted > RETRY_OPEN_MAX:
            stop.why = f"{unrouted} nets open (more than {RETRY_OPEN_MAX}): another net order does not close them"
            return True
        return False
    stop.why = ""
    return stop


def verdict_of(share: float) -> str:
    return VERDICTS[0] if share <= HARD_SHARE_MARGINAL else VERDICTS[1] if share < HARD_SHARE_FAIL else VERDICTS[2]


def message(res: dict) -> str:
    """One structural message: the verdict, where, how much over, and what would help."""
    s = (f"routability {res['verdict']}: {res['hard_nets']} of {res['routed_nets']} nets predicted hard "
         f"({res['hard_share']:.0%}; marginal from 1 net, not routable from {HARD_SHARE_FAIL:.0%})")
    if res.get("proofs"):
        s += f"; proven: {'; '.join(res['proofs'][:3])}" + (" ..." if len(res["proofs"]) > 3 else "")
    g = res.get("gate") or {}
    if g.get("fail"):
        s += f"; congested: {g['area_mm2']:g} mm2 at {g['ratio_fail']:g} x or more (gate from {g['area_fail_mm2']:g} mm2)"
    if res["hot_spots"]:
        h = res["hot_spots"][0]
        s += (f"; worst hot spot x {h['at'][0]}-{h['at'][2]}, y {h['at'][1]}-{h['at'][3]} mm: {h['peak']:.1f} x its "
              f"border's tracks, parts {', '.join(h['refs'][:8])}")
    na = len(res["access"])
    if na:
        s += f"; {na} pads with at most {ACCESS_MAX} exit at their net width"
    bad = [c for c in res["corridors"] if c["margin"] < CORRIDOR_MARGIN_MM]
    if bad:
        s += "; corridors under margin: " + ", ".join(f"{c['net']} {c['plain']:.2f}/{c['width']:g} mm" for c in bad[:4])
    if res["verdict"] != VERDICTS[0]:
        s += (f" (helps: more layers than {res['layers']}, more area around the hot spots, or fewer or smaller parts "
              "there)")
    return s


def summary(res: dict) -> str:
    return res.get("message") or message(res)


def brief(res: dict) -> dict:
    """The prediction as status.json keeps it: verdict, counts, proofs, the hard nets and the message."""
    return {"verdict": res["verdict"], "hard_nets": res["hard_nets"], "routed_nets": res["routed_nets"],
            "hard_share": res["hard_share"], "max_ratio": res["max_ratio"], "hot_area_mm2": res["hot_area_mm2"],
            "access_pads": len(res["access"]), "proofs": res.get("proofs") or [], "hard": sorted(res["hard"]),
            "message": res["message"], "seconds": res["seconds"]}


def outcome_check(b: dict, unrouted: list, errs: list) -> str:
    """One line: the prediction `b` (brief) against the routed board (its unconnected items and DRC errors)."""
    import re
    opened = set()
    for x in unrouted:
        m = re.search(r"\[([^\]]+)\]", str(x.get("msg", "")) + " " + " ".join(map(str, x.get("items") or [])))
        if m:
            opened.add(m.group(1))
    hard = set(b["hard"])
    hit = len(opened & hard)
    ok = not unrouted and not errs
    if b["verdict"] == VERDICTS[1]:
        how = "a risk, either outcome"
    else:
        how = "agrees" if (b["verdict"] == VERDICTS[0]) == ok else "disagrees"
    return (f"{len(unrouted)} unrouted, {len(errs)} DRC errors; predicted {b['verdict']} ({how}); {hit} of {len(opened)} open nets among the {len(hard)} predicted hard")


LOG_DIR = "routab"  # build/routab/: run_<n>.npz (raster channels) and runs.jsonl (features, outcome) per layout run


def log_run(build: Path, design, res: dict, outcome: dict | None) -> None:
    """Append the (cheap features, outcome) row of one layout run to build/routab/runs.jsonl and save its raster
    channels as build/routab/run_<n>.npz (format: scripts/routab_bench.py). Never raises."""
    import json
    try:
        d = Path(build) / LOG_DIR
        d.mkdir(parents=True, exist_ok=True)
        log = d / "runs.jsonl"
        n = sum(1 for _ in log.open()) if log.exists() else 0
        dataset(res, d / f"run_{n}.npz")
        row = {"run": n, "at": round(time.time(), 1), "design": design.name, "features": brief(res),
               "per_net": res["per_net"], "bounds": res.get("bounds"), "outcome": outcome}
        with log.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception as e:  # noqa: BLE001 - a log
        print(f"[layout] routability log skipped: {type(e).__name__}: {e}")


def public(res: dict) -> dict:
    return {k: v for k, v in res.items() if not k.startswith("_")}


def dataset(res: dict, path) -> None:
    """Save the raster channels and the window ratio map of a prediction as a compressed npz (dataset for a later
    model; the format is documented in scripts/routab_bench.py)."""
    R = res["_raster"]
    np.savez_compressed(path, res_mm=R.res, pos_mm=POS_MM, layers=np.array(R.layers), nets=np.array(R.nets[1:]),
                        ratio=res["_ratio"].astype(np.float32), **R.arrays())
