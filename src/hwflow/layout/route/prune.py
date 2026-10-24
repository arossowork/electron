"""Removal of router leftovers after routing and repair: a via connected on one layer only, a track with a free end.
They carry no current and only add copper (a stub on a switching node is an antenna). Applied to the board kept, also
on a route-cache hit; the route cache keeps the router's own copper."""

from __future__ import annotations

from .repair import errors, parse_item

DANGLING = ("track_dangling", "via_dangling")
EPS = 0.01  # mm: a dangling item reported by DRC is matched to track ends and vias this close


def dangling(tracks: list, vias: list, drc: list[dict]) -> tuple[list, list, int]:
    """(tracks, vias, number removed) without the items `drc` reports as dangling."""
    hits = [parse_item(str(it), at) for x in drc if x["type"] in DANGLING
            for it, at in zip(x.get("items") or [], x.get("at") or []) if at]
    near = lambda a, b: abs(a[0] - b[0]) <= EPS and abs(a[1] - b[1]) <= EPS  # noqa: E731
    vs = [v for v in vias if not any(h["kind"] == "Via" and h["net"] == v["net"] and near(h["at"], (v["x"], v["y"]))
                                     for h in hits)]
    ts = [t for t in tracks if not any(h["kind"] == "Track" and h["net"] == t["net"] and h["layer"] == t["layer"]
                                       and (near(h["at"], (t["x1"], t["y1"])) or near(h["at"], (t["x2"], t["y2"])))
                                       for h in hits)]
    return ts, vs, len(tracks) - len(ts) + len(vias) - len(vs)


def prune(tracks: list, vias: list, drc: list[dict], check, rounds: int = 3) -> tuple[list, list, list, int]:
    """Remove dangling items until none is left (a removal can leave the next piece dangling). `check(tracks, vias)`
    writes the board and returns its DRC. A removal that adds errors (it cut a connection) is undone and stops.
    Returns (tracks, vias, drc, number removed)."""
    total = 0
    for _ in range(rounds):
        tr, vi, n = dangling(tracks, vias, drc)
        if not n:
            break
        new = check(tr, vi)
        if len(errors(new)) > len(errors(drc)):
            check(tracks, vias)
            break
        tracks, vias, drc, total = tr, vi, new, total + n
    return tracks, vias, drc, total
