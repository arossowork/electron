"""Hierarchical (region-split) routing: the nets of a board split into groups that route as router jobs of their own.

- Partition. The floorplan regions that hold nets (`floorplan.regions[].nets`, e.g. `mains` with AC_*), with
  `board.route_groups: members` every region's local nets (floorplan.member_net_regions), and the
  `board.isolation` groups are the seeds (seeds that share a net merge). A net that belongs to a seed and has every
  pad inside its region is that seed's group; a net of no seed with no pad in a seed region is in `rest`; any other
  net (pads in several regions, or a seed net with a pad outside its region) is an `interface` net.
- Order. The seed groups route first, smallest first (the most constrained: mains takes seconds), then `rest`, then
  the interface nets. Each group is one krt.krt_route call on the board that holds the copper of the groups routed
  before it (and the pads of every net), so the per-pass work of krt_route (isolation and conductive keep-outs,
  nets.<name>.layers passes, between_mm classes, neck-down) runs inside each group job. Layout blocks keep their
  copper; a port net routes with the group that holds it.
- Cache. Each group's copper is cached under a key of its own: its pads, the pads of the unrouted (pour) nets within
  its pads' extent, its nets' classes and layers, the rules, the board settings and the router and driver code. A
  build that changes one group reuses the others' copper; the reused copper goes on the board first and the other
  groups route around it. The joined board's DRC judges it (validate): a DRC error on reused copper drops that
  group's cache and routes the group again.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from shapely.geometry import Point
from shapely.ops import unary_union

from ..board import pcb
from . import krt
from ...core.settings import get_settings

GROUP_DIR = "groups"  # per-group route cache, under build/routecache/


def seeds(design) -> list[dict]:
    """The net groups the floorplan and the isolation rules declare: [{name, members, poly}], poly the region shape in
    board coordinates (None for an isolation group with no region). Seeds that share a net are merged."""
    from ...core import floorplan
    rows = [{"name": r["region"], "members": set(r["nets"]), "poly": r["poly"]}
            for r in floorplan.net_regions(design) + floorplan.member_net_regions(design)]
    rows += [{"name": g["name"], "members": set(g["members"]), "poly": None}
             for g in pcb.isolation_groups(design) if g["members"]]
    out: list[dict] = []
    for r in rows:
        hit = [m for m in out if m["members"] & r["members"]]
        polys = [x["poly"] for x in hit + [r] if x["poly"] is not None]
        merged = {"name": (hit[0]["name"] if hit else r["name"]), "members": set().union(r["members"], *(m["members"] for m in hit)),
                  "poly": unary_union(polys) if polys else None}
        out = [m for m in out if m not in hit] + [merged]
    return out


def pads_by_net(board: dict) -> dict[str, list[tuple[float, float]]]:
    """net -> pad centres of a pcb.read_board result."""
    out: dict[str, list] = {}
    for fp in board["footprints"].values():
        for p in fp["pads"]:
            if p["net"]:
                out.setdefault(p["net"], []).append((p["x"], p["y"]))
    return out


def partition(nets, pads: dict[str, list], seed_rows: list[dict]) -> list[dict]:
    """Split `nets` into route groups (module docstring): [{name, kind (region, rest, interface), nets}] in routing
    order, empty groups left out. `pads` net -> pad centres, `seed_rows` as seeds()."""
    areas = [s["poly"].buffer(1e-3) if s["poly"] is not None else None for s in seed_rows]
    own: dict = {}
    for n in sorted(nets):
        pts = [Point(p) for p in pads.get(n, [])]
        home = [i for i, s in enumerate(seed_rows) if n in s["members"]]
        inside = {i for i, a in enumerate(areas) if a is not None and any(a.covers(p) for p in pts)}
        if home and inside <= set(home[:1]) and (areas[home[0]] is None or all(areas[home[0]].covers(p) for p in pts)):
            own[n] = home[0]
        elif not home and not inside:
            own[n] = "rest"
        else:
            own[n] = "interface"
    out = [{"name": s["name"], "kind": "region", "nets": [n for n in sorted(nets) if own[n] == i]}
           for i, s in enumerate(seed_rows)]
    out = sorted((g for g in out if g["nets"]), key=lambda g: (len(g["nets"]), g["name"]))
    out += [{"name": k, "kind": k, "nets": [n for n in sorted(nets) if own[n] == k]} for k in ("rest", "interface")]
    return [g for g in out if g["nets"]]


def _knobs() -> list:
    """Router environment knobs (as pipeline.stages._route_key)."""
    return get_settings().key_dump()


def group_key(design, nets, board: dict, routed, rules: str = "", parts=None) -> str:
    """Cache key of one group's copper: the pads of its nets, the pads of nets that are not routed (pour nets, no
    net) inside the extent of those pads, its nets' classes and layers, the isolation groups, the floorplan, the board
    settings, the rules text, the block copper, the router version and the driver code. The pads of the other
    routed groups are left out: the joined board's DRC checks reused copper against them (validate)."""
    nets, routed = set(nets), set(routed)

    def row(ref, p):
        return [ref, p["number"], p["net"], round(p["x"], 3), round(p["y"], 3), round(p["w"], 3), round(p["h"], 3),
                p["shape"], p["kind"], sorted(p["layers"])]
    mine = [row(r, p) for r, fp in sorted(board["footprints"].items()) for p in fp["pads"] if p["net"] in nets]
    xs, ys = [x[3] for x in mine], [x[4] for x in mine]
    near = []
    if mine:
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        near = [row(r, p) for r, fp in sorted(board["footprints"].items()) for p in fp["pads"]
                if p["net"] not in routed and x0 <= p["x"] <= x1 and y0 <= p["y"] <= y1]
        # copper of the nets not routed here already on the board (the pour taps) inside the extent
        near += [[s["net"], s["layer"], round(s["x1"], 3), round(s["y1"], 3), round(s["x2"], 3), round(s["y2"], 3),
                  s["w"]] for s in board.get("segments", []) if s["net"] not in routed
                 and x0 <= s["x1"] <= x1 and y0 <= s["y1"] <= y1]
        near += [[v["net"], round(v["x"], 3), round(v["y"], 3)] for v in board.get("vias", [])
                 if v["net"] not in routed and x0 <= v["x"] <= x1 and y0 <= v["y"] <= y1]
    classes = pcb.net_classes(design)
    lays = pcb.net_layers(design)
    h = hashlib.sha256(json.dumps([sorted(nets), mine, near, {n: classes.get(n) for n in sorted(nets)},
                                   {n: lays.get(n) for n in sorted(nets)}, pcb.isolation_needs(design),
                                   design.raw.get("floorplan"), design.board, rules, krt.KRT_VARIANTS,
                                   krt.router_version(design), _knobs()], sort_keys=True, default=str).encode())
    from ..board import pcb as board_pcb
    from ..place import pinaccess
    from ..board import routing
    from . import roads, taps
    for m in (board_pcb, krt, routing, sys.modules[__name__], pinaccess, roads, taps):
        h.update(Path(m.__file__).read_bytes())
    from ..place import asicflow
    h.update(asicflow.flow(design).encode())  # the layout flow: pin access necked or not
    if design.raw.get("blocks") and parts is not None:
        from ...core import blocks
        h.update(Path(blocks.__file__).read_bytes())
        h.update(json.dumps(blocks.copper(design, parts), sort_keys=True, default=str).encode())
    return h.hexdigest()[:24]


def _own(path: Path, nets: set, skip: set) -> tuple[list, list]:
    """The tracks and vias of `nets` on the routed board `path` (the declared thermal vias left out)."""
    rb = pcb.read_board(path)
    return ([dict(s) for s in rb["segments"] if s["net"] in nets],
            [dict(v) for v in rb["vias"] if v["net"] in nets and (round(v["x"], 2), round(v["y"], 2)) not in skip])


def _route_one(design, libs, parts, workdir: Path, g: dict, i, tracks, vias, keep: bool, skip: set, timeout,
               deadline: float | None = None, keep_partial: bool = False) -> dict:
    """One group job on the board with `tracks` and `vias`; returns the cache entry."""
    t1 = time.time()
    src = workdir / f"{design.name}_unrouted.kicad_pcb"
    if tracks or vias:
        src = workdir / f"{design.name}_g{i}.kicad_pcb"
        pcb.write_board(src, pcb.build_board(design, libs, parts, tracks, vias))
    nets = set(g["nets"])
    cur, last, notes, npass = krt.krt_route(design, src, workdir, f"{design.name}_krt_g{i}_", nets=nets,
                                            keep_first=bool(keep or tracks or vias), timeout=timeout, deadline=deadline, keep_partial=keep_partial)
    tr, vi = _own(cur, nets, skip)
    opened = sorted({n for v in last.values() for n in krt.krt_open_nets(v)} & nets)
    return {"tracks": tr, "vias": vi, "unrouted": sum(krt.krt_unrouted(v) for v in last.values()), "open": opened,
            "passes": npass, "notes": notes, "seconds": round(time.time() - t1, 1),
            "budget_exhausted": any(v.get("budget_exhausted") for v in last.values())}


def _save(cache: Path, key: str, data: dict):
    cache.mkdir(parents=True, exist_ok=True)
    tmp = cache / f"{key}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(data))
    os.replace(tmp, cache / f"{key}.json")  # atomic: parallel attempts may write the same key


def route(design, libs, parts, workdir: Path, cache: Path, timeout: int = 1800, deadline: float | None = None):
    """Route the board group by group (module docstring). Returns (tracks, vias, summary, unrouted, rows), rows
    [{name, kind, nets, key, cache (hit, miss), seconds, unrouted, open}] in routing order, or None when the nets
    form fewer than two groups (krt.run_krt routes the board as one job)."""
    workdir = Path(workdir).resolve()
    name = design.name
    src = workdir / f"{name}_unrouted.kicad_pcb"
    pcb.write_board(src, pcb.build_board(design, libs, parts))
    (workdir / "krt.log").unlink(missing_ok=True)  # krt_route appends: every group job adds to the same log
    from . import seedroute
    (workdir / seedroute.RECORD).unlink(missing_ok=True)  # the seed copper of this run (krt_route adds to it)
    pcb.write_project(workdir, name, design)
    closed, keep = set(), False
    if design.raw.get("blocks"):  # layout blocks: their copper stays, the nets it closes are not routed
        from ...core import blocks
        closed = blocks.closed_nets(design, libs)
        keep = any(blocks.copper(design, parts))
    gnds = design.ground_nets() if design.board["gnd_pour"] else set()
    routed = [n for n in pcb.net_classes(design) if n not in gnds and n not in closed]
    tap_t, tap_v = [], []
    from . import taps
    if taps.on(design):  # every pour pad's tap first: each group routes around it
        src, tap_t, tap_v, _ = taps.preseed(design, libs, parts, src, workdir, f"{name}_")
    board = pcb.read_board(src)
    groups = partition(routed, pads_by_net(board), seeds(design))
    if len(groups) < 2:
        return None
    dru = workdir / f"{name}.kicad_dru"
    rules = dru.read_text() if dru.exists() else ""
    gdir = Path(cache) / GROUP_DIR
    from .repair import thermal_keys
    skip = thermal_keys(design, libs, parts)
    rows, entries = [], {}
    for g in groups:
        key = group_key(design, g["nets"], board, routed, rules, parts)
        f = gdir / f"{key}.json"
        hit = json.loads(f.read_text()) if f.exists() else None
        rows.append(dict(g, key=key, cache="hit" if hit else "miss"))
        if hit:
            entries[key] = hit
    tracks, vias = list(tap_t), list(tap_v)
    for r in rows:  # reused copper first: the groups routed now route around it
        if r["cache"] == "hit":
            tracks += entries[r["key"]]["tracks"]
            vias += entries[r["key"]]["vias"]
    for i, r in enumerate(rows):
        if r["cache"] == "miss":
            if deadline is not None and (time.time() >= deadline or getattr(design, "route_budget_exhausted", False)):
                e = {"tracks": [], "vias": [], "unrouted": len(r["nets"]), "open": r["nets"], "passes": 0,
                     "notes": ["stopped at layout wall-time budget"], "seconds": 0.0, "budget_exhausted": True}
            else:
                e = _route_one(design, libs, parts, workdir, r, i, tracks, vias, keep, skip, timeout,
                               deadline=deadline, keep_partial=deadline is not None)
            if e.get("budget_exhausted"):
                design.route_budget_exhausted = True
            else:
                _save(gdir, r["key"], e)
            entries[r["key"]] = e
            tracks += e["tracks"]
            vias += e["vias"]
        e = entries[r["key"]]
        r.update(seconds=e["seconds"] if r["cache"] == "miss" else 0.0, unrouted=e["unrouted"], open=e["open"])
        print(f"[layout] route group {r['name']} ({r['kind']}): {len(r['nets'])} nets, cache {r['cache']}, "
              f"{r['seconds']} s, {r['unrouted']} unrouted" + (f" ({', '.join(r['open'])})" if r["open"] else ""))
    unrouted = sum(r["unrouted"] for r in rows)
    npass = sum(entries[r["key"]]["passes"] for r in rows if r["cache"] == "miss")
    notes = [x for r in rows if r["cache"] == "miss" for x in entries[r["key"]]["notes"]]
    summary = (f"{unrouted} unrouted, krt {npass} pass(es) in {len(rows)} route groups ("
               + ", ".join(f"{r['name']} {r['cache']}" for r in rows) + ")"
               + (f"; retried in another net order ({'; '.join(notes)})" if notes else ""))
    return tracks, vias, summary, unrouted, rows


def stale(rows: list[dict], drc: list[dict]) -> list[tuple[dict, int]]:
    """The reused groups whose copper a DRC error names: [(row, errors)]. An error counts when it names a track or
    via of one of the group's nets, or the group's net is unconnected (unless the cached route left it open)."""
    from .repair import errors, parse_item
    out = []
    for r in rows:
        if r["cache"] != "hit":
            continue
        nets, opened = set(r["nets"]), set(r.get("open") or [])
        n = 0
        for x in errors(drc):
            items = [parse_item(s, None) for s in x["items"]]
            if x["type"] == "unconnected":
                n += any(i["net"] in nets - opened for i in items)
            else:
                n += any(i["kind"] in ("Track", "Via") and i["net"] in nets for i in items)
        if n:
            out.append((r, n))
    return out


def validate(design, libs, parts, workdir: Path, cache: Path, tracks: list, vias: list, drc: list[dict], rows: list[dict],
             check, timeout: int = 1800, deadline: float | None = None) -> tuple[list, list, list]:
    """The joined board's DRC on reused copper: every reused group a DRC error names (stale) loses its cache entry and
    routes again on the board with all other copper; `check(tracks, vias)` then gives the new DRC. Returns
    (tracks, vias, drc)."""
    bad = stale(rows, drc)
    if not bad:
        return tracks, vias, drc
    workdir = Path(workdir).resolve()
    gdir = Path(cache) / GROUP_DIR
    from .repair import thermal_keys
    skip = thermal_keys(design, libs, parts)
    keep = False
    if design.raw.get("blocks"):
        from ...core import blocks
        keep = any(blocks.copper(design, parts))
    for r, n in bad:
        (gdir / f"{r['key']}.json").unlink(missing_ok=True)
        if deadline is not None and (time.time() >= deadline or getattr(design, "route_budget_exhausted", False)):
            design.route_budget_exhausted = True
            r["cache"] = f"hit invalid ({n} DRC errors); budget exhausted before reroute"
            continue
        nets = set(r["nets"])
        tracks = [t for t in tracks if t["net"] not in nets]
        vias = [v for v in vias if v["net"] not in nets]
        e = _route_one(design, libs, parts, workdir, r, f"v{rows.index(r)}", tracks, vias, keep, skip, timeout,
                       deadline=deadline, keep_partial=deadline is not None)
        if e.get("budget_exhausted"):
            design.route_budget_exhausted = True
        else:
            _save(gdir, r["key"], e)
        tracks, vias = tracks + e["tracks"], vias + e["vias"]
        r.update(cache=f"hit dropped ({n} DRC errors on its copper), re-routed", seconds=e["seconds"],
                 unrouted=e["unrouted"], open=e["open"])
        print(f"[layout] route group {r['name']}: {n} DRC errors on the reused copper: cache dropped, re-routed in "
              f"{e['seconds']} s, {e['unrouted']} unrouted")
    return tracks, vias, check(tracks, vias)


def text(rows: list[dict]) -> str:
    """One line for the report: the route groups in routing order."""
    return "; ".join(f"{r['name']} {len(r['nets'])} nets {r['cache']} {r['seconds']} s {r['unrouted']} unrouted"
                     for r in rows)
