"""The routability window cuts as a local placer penalty (routable first pass, layout.route.firstpass; experiment,
off by default: HWFLOW_FIRSTPASS).

routability.window_map scores a finished placement: every square window of 2, 4 and 8 mm has a ratio of the
crossing nets' width to the tracks that fit across its border. Here that score becomes a cost term inside the
placer's local stages (detailed moves, short anneal; Placer term `hotspot`): every window over R0 contributes its
excess (ratio - R0) / RATIO_HARD, spread over the position cells it covers (a box sum per window size, divided by its
cell count). A cell in the middle of a cluster of hot windows is under more of them than a cell at its edge, so the
map has a slope out of the hot spot and a pad moved outwards pays less. The penalty of a part is the map summed at
its signal pads (pour nets and pads without copper do not count).

The map is static while the moves run (it is rebuilt between the stages: `refresh`), so it pushes the parts out of
the hot spots of the placement it was built on. `bumps` adds, for the local fix of a quick route (firstpass), a
Gaussian bump at each pad of a net the quick route left open: the parts around an open net's pads are pushed away,
which widens its escape.

Nothing here is tied to a design: the inputs are the board rules and the placement."""
from __future__ import annotations

import math
import time

import numpy as np
from scipy import ndimage

from ...core.settings import get_settings

R0 = 2.0          # window ratio from which a window pushes: the clean boards peak at 1.3-2.0 (routability calibration)
BLUR_MM = 0.75    # Gaussian smoothing of the map
BUMP_MM = 1.5     # sigma of the bump at an open net's pad (local fix)
BUMP_H = 3.0      # its height, in map units (one map unit: a window at R0 + RATIO_HARD over the pad)
STEPS = ("roads", "hot", "quick")
LOG: list = []    # per map build: {s, max, cells} (docs/asic-ideas.md, "Routable first pass")


def steps(design=None) -> set[str]:
    """The steps of the routable first pass that are on: HWFLOW_FIRSTPASS (or placement: {firstpass: ...}) is 0/off,
    1/all/on (every step) or a comma list of STEPS (and `preseed`: the roads routed first, not reserved)."""
    v = get_settings().layout.firstpass
    if v is None:
        v = ((getattr(design, "raw", None) or {}).get("placement") or {}).get("firstpass", "0")
    v = str(v if v is not None else "0").strip().lower()
    if v in ("", "0", "false", "no", "off"):
        return set()
    if v in ("1", "true", "yes", "on", "all"):
        return set(STEPS)
    return {s.strip() for s in v.split(",") if s.strip()}


def on(design=None) -> bool:
    return "hot" in steps(design)


class HotMap:
    """A penalty map on a grid of `cell` mm (index [ix, iy], cell centres at (i + 0.5) * cell). `labels`: the hot
    spot of each cell (0: none) and `owner`: ref -> the spots that ref makes (it has the most pads in them); a part
    pays nothing inside its own spots: moving a fine-pitch part does not take its own escape away from it, moving
    its neighbours out of the way does."""

    def __init__(self, grid: np.ndarray, cell: float, labels: np.ndarray | None = None, owner: dict | None = None):
        self.g, self.cell = grid, cell
        self.nx, self.ny = grid.shape
        self.labels = labels
        self.owner = owner or {}

    def at(self, x: float, y: float, ref: str | None = None) -> float:
        """Bilinear value at (x, y) mm (0 off the grid, and in a spot `ref` owns)."""
        fx, fy = x / self.cell - 0.5, y / self.cell - 0.5
        i, j = math.floor(fx), math.floor(fy)
        ax, ay = fx - i, fy - j
        g, v = self.g, 0.0
        own = self.owner.get(ref) if ref is not None else None
        for di, wx in ((0, 1 - ax), (1, ax)):
            ii = i + di
            if 0 <= ii < self.nx:
                for dj, wy in ((0, 1 - ay), (1, ay)):
                    jj = j + dj
                    if 0 <= jj < self.ny and wx * wy > 0 and not (own and self.labels[ii, jj] in own):
                        v += wx * wy * g[ii, jj]
        return v


def window_excess(wm: dict, shape) -> np.ndarray:
    """The map of routability.window_map's per-size ratios (module text), on the position grid `shape`."""
    from . import routability
    out = np.zeros(shape)
    for _, kk, ratio in wm.get("maps") or ():
        e = np.clip(ratio - R0, 0, None) / routability.RATIO_HARD
        if not e.any():
            continue
        full = np.zeros(shape)
        full[:e.shape[0], :e.shape[1]] = e
        # each window covers kk x kk cells from its lower-left corner: a box sum over the corners that cover a cell
        s = ndimage.uniform_filter(full, size=kk, origin=(kk - 1) // 2, mode="constant")
        out += s  # uniform_filter is the mean over kk^2 corners: the sum of the excesses / kk^2
    return out


def build(pl, extra: np.ndarray | None = None) -> HotMap | None:
    """The penalty map of placer `pl`'s current placement (None when the raster cannot be built)."""
    from . import routability
    ts = time.perf_counter()
    try:
        libs = getattr(pl.parts, "libs", None)
        if libs is None:
            from ...core.libs import Libs
            libs = Libs(design=pl.d)
        R = routability.Raster(pl.d, libs, parts=pl.parts)
        wm = routability.window_map(R, pl.d)
    except Exception as e:  # noqa: BLE001 - a placement hint: the placer runs without it
        print(f"[place] hotspot map skipped: {type(e).__name__}: {e}")
        return None
    pos = routability.POS_MM
    g = window_excess(wm, wm["ratio"].shape)
    if BLUR_MM > 0:
        g = ndimage.gaussian_filter(g, BLUR_MM / pos, mode="constant")
    labels, owner = _owners(g, pl, pos)
    if extra is not None:
        g = g + extra[:g.shape[0], :g.shape[1]]
    LOG.append({"s": round(time.perf_counter() - ts, 2), "max": round(float(g.max()), 3),
                "cells": int((g > OWN_MIN).sum()), "max_ratio": round(float(wm["ratio"].max()), 2),
                "owners": sorted(owner)})
    return HotMap(g, pos, labels, owner)


OWN_MIN = 0.02  # map level that bounds a hot spot (for its owner)


def _owners(g, pl, pos):
    """(labels of the connected spots over OWN_MIN, ref -> its spots): the owner of a spot is the component with the
    most signal pads inside it."""
    labels, n = ndimage.label(g > OWN_MIN)
    owner: dict = {}
    if not n:
        return labels, owner
    count: dict = {}
    for r, pads in pl.sig_pads.items():
        p = pl.parts[r]
        for pn in pads:
            x, y = p.pad(pn)
            i, j = int(x / pos), int(y / pos)
            if 0 <= i < labels.shape[0] and 0 <= j < labels.shape[1] and labels[i, j]:
                k = int(labels[i, j])
                count.setdefault(k, {}).setdefault(r, 0)
                count[k][r] += 1
    for k, c in count.items():
        r = max(c, key=lambda q: (c[q], q))
        owner.setdefault(r, set()).add(k)
    return labels, owner


def bumps(points, shape, cell: float, h: float = BUMP_H, sigma: float = BUMP_MM) -> np.ndarray:
    """Gaussian bumps of height h at `points` (x, y mm) on a grid `shape` of `cell` mm."""
    out = np.zeros(shape)
    xs = (np.arange(shape[0]) + 0.5) * cell
    ys = (np.arange(shape[1]) + 0.5) * cell
    r = 3 * sigma
    for x, y in points:
        i0, i1 = max(0, int((x - r) / cell)), min(shape[0], int(math.ceil((x + r) / cell)) + 1)
        j0, j1 = max(0, int((y - r) / cell)), min(shape[1], int(math.ceil((y + r) / cell)) + 1)
        if i1 <= i0 or j1 <= j0:
            continue
        dx = xs[i0:i1, None] - x
        dy = ys[None, j0:j1] - y
        out[i0:i1, j0:j1] = np.maximum(out[i0:i1, j0:j1], h * np.exp(-(dx * dx + dy * dy) / (2 * sigma * sigma)))
    return out


def install(pl, extra=None, weight: float | None = None) -> bool:
    """Build the map of `pl`'s placement and turn the placer term on (weight HWFLOW_FIRSTPASS_HOT_W)."""
    hm = build(pl, extra)
    if hm is None:
        return False
    pl.hot = hm
    pl.wt["hotspot"] = get_settings().layout.firstpass_hot_w if weight is None else weight
    return True


def refresh(pl, extra=None) -> None:
    if getattr(pl, "hot", None) is not None:
        hm = build(pl, extra)
        if hm is not None:
            pl.hot = hm


def remove(pl) -> None:
    pl.hot = None
    pl.wt["hotspot"] = 0.0
