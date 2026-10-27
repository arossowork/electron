"""Track width a net's current needs (IPC-2221), and the narrowest copper a net may have.

One rule for every step that makes or checks copper: a net with `current_a` is under its IPC-2221 width only in
stretches of at most NECK_MAX_MM (a pad-entry neck): the router's neck-down accepts no longer narrow stretch
(layout.route.krt, routing.widen, the seed routes), block copper is widened where it has one (core.blocks), and the
layout stage's track check and post-layout's track-current check (verify.postlayout) fail on one.
"""

from __future__ import annotations

import math

NECK_MAX_MM = 3.0  # longest continuous stretch of a net under its width (a pad-entry neck); a longer one is a thinner net


def ipc2221_width_mm(current_a: float, copper_oz: float = 1.0, dtemp: float = 10.0, external: bool = True) -> float:
    """IPC-2221 track width in mm for `current_a` at a `dtemp` C rise on `copper_oz` copper (outer layer by default)."""
    k = 0.048 if external else 0.024
    area_mil2 = (current_a / (k * dtemp ** 0.44)) ** (1 / 0.725)
    thick_mil = 1.378 * copper_oz
    return area_mil2 / thick_mil * 0.0254


def current_width_mm(design, net: str) -> float:
    """The IPC-2221 width (outer layer, the net's `max_rise_c`, default 10 C) its `current_a` needs, rounded up to
    0.01 mm; 0 for a net without `current_a`."""
    info = design.net_info(net)
    if not info.get("current_a"):
        return 0.0
    w = ipc2221_width_mm(float(info["current_a"]), float(design.board.get("copper_oz", 1.0)),
                         float(info.get("max_rise_c", 10)))
    return math.ceil(w * 100 - 1e-6) / 100


def narrow_stretches(pieces: list[tuple]) -> list[list[int]]:
    """Connected stretches of track pieces [(x1, y1, x2, y2, layer)]: lists of piece indices, pieces that share an end
    on the same layer in one stretch (a via between layers ends it)."""
    parent = list(range(len(pieces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    ends: dict = {}
    for i, (x1, y1, x2, y2, layer) in enumerate(pieces):
        for x, y in ((x1, y1), (x2, y2)):
            k = (layer, round(x, 3), round(y, 3))
            if k in ends:
                parent[find(i)] = find(ends[k])
            else:
                ends[k] = i
    out: dict = {}
    for i in range(len(pieces)):
        out.setdefault(find(i), []).append(i)
    return list(out.values())


def piece_floors(design, tracks: list[dict]) -> list[float]:
    """The width each track {net, w, x1, y1, x2, y2, layer} of pre-laid copper (a layout block) must have at least: the
    net's `min_width_mm` everywhere, and the IPC-2221 width of its `current_a` on every connected stretch under that
    width longer than NECK_MAX_MM (a shorter one is a pad-entry neck: the router may make the same, and post-layout's
    track-current check accepts it with a warning). 0 where the track needs nothing."""
    out = [0.0] * len(tracks)
    by: dict = {}
    for i, t in enumerate(tracks):
        by.setdefault(t["net"], []).append(i)
    for net, idx in by.items():
        info = design.net_info(net)
        mw = float(info["min_width_mm"]) if info.get("min_width_mm") is not None else 0.0
        need = current_width_mm(design, net)
        thin = [i for i in idx if float(tracks[i]["w"]) + 1e-9 < need]
        for st in narrow_stretches([(tracks[i]["x1"], tracks[i]["y1"], tracks[i]["x2"], tracks[i]["y2"],
                                     tracks[i]["layer"]) for i in thin]):
            if sum(math.hypot(tracks[thin[k]]["x2"] - tracks[thin[k]]["x1"], tracks[thin[k]]["y2"] - tracks[thin[k]]["y1"])
                   for k in st) > NECK_MAX_MM + 1e-9:
                for k in st:
                    out[thin[k]] = need
        for i in idx:
            out[i] = max(out[i], mw)
    return out
