"""Local repair of a routed board (L2): after DRC, rip up and re-route only the nets the errors name,
and connect GND zone islands with stitching vias (or drop them). No LLM, no re-placement."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from ...core.settings import get_settings
from ..board import kicad, pcb
from . import krt
from ...core import sexpr

_NET = re.compile(r"\[([^\]]+)\]")
_ON = re.compile(r" on (\S+?)(?:,| -|$)")
MAX_RIPUP = 12  # nets ripped in one round, the ones named by errors first
# HWFLOW_REPAIR_MAX_ERRORS (settings.repair.max_errors, default 40): above this many errors local repair cannot converge: skip it
# HWFLOW_REPAIR_BUDGET_S (settings.repair.budget_s, default 1800): a safety abort of the whole repair, router calls
# included, never its normal stop. The repair is bounded by counts (rounds, max_stall, MAX_RIPUP nets per round, the
# router's own A* iteration limits), so the same board repairs the same way under any CPU load; a stop at this wall
# time is reported as a safety abort (report["aborts"]). It was a 120 s budget that stopped thermo-e2e-4's second
# round under load and not without it.
# rounds in a row without improvement before the repair stops; 1 when some errors are on fixed parts (a floor no
# re-route lowers: home-thermostat-3 spent 598 s going 5 -> 5 on courtyard errors of fixed parts and one open net)
# (settings.repair.max_stall, HWFLOW_REPAIR_MAX_STALL, default 2)


def errors(drc: list[dict]) -> list[dict]:
    return [x for x in drc if x["sev"] == "error"]


def score(drc: list[dict]) -> tuple[int, int]:
    """(DRC errors, unrouted); lower is better in both. Creepage and schematic parity are left out: the per-round
    check is `kicad.drc_fast`, which has neither."""
    e = [x for x in errors(drc) if x["type"] != "creepage" and not x["type"].startswith("parity:")]
    return len(e), sum(x["type"] == "unconnected" for x in e)


def open_nets(drc: list[dict]) -> list[str]:
    """The nets of the DRC `unconnected` errors, each once. KiCad writes one item per missing ratsnest edge (a 25-pad
    net the router left open is 24 items); the router and the repair work per net."""
    out: list[str] = []
    for x in errors(drc):
        if x["type"] != "unconnected":
            continue
        for s in x.get("items") or [x.get("msg") or ""]:
            m = _NET.search(str(s))
            if m:
                if m.group(1) not in out:
                    out.append(m.group(1))
                break
        else:
            out.append(f"?{len(out)}")  # an unconnected error without a net name still counts once
    return out


def load(drc: list[dict]) -> int:
    """The size of a board's problem for the repair and `--auto` limits: each DRC error once, each open net once
    (not each missing ratsnest edge), creepage and parity left out as in `score`."""
    e = [x for x in errors(drc) if x["type"] not in ("creepage", "unconnected") and not x["type"].startswith("parity:")]
    return len(e) + len(open_nets(drc))


def parse_item(desc: str, at) -> dict:
    m, ly = _NET.search(desc), _ON.search(desc)
    return {"kind": desc.split(" ", 1)[0], "net": m.group(1) if m else None, "layer": ly.group(1) if ly else None,
            "at": tuple(at) if at else None}


def plan(d, tracks: list, vias: list, drc: list[dict], radius: float = 0.0) -> dict:
    """What to repair: nets to rip up and re-route, pour nets with a split pour, stitching vias to remove.
    `radius` (mm) adds nets whose copper lies that close to a non-pour unconnected endpoint
    or to a track or via named by another error."""
    pour = set(d.ground_nets()) if d.board["gnd_pour"] else set()
    rip, islands, drop, centers, left = [], [], [], [], 0
    for x in errors(drc):
        items = [parse_item(s, a) for s, a in zip(x["items"], x.get("at") or [None] * len(x["items"]))]
        n0 = (len(rip), len(islands), len(drop))
        if x["type"] == "unconnected":
            zones = [i["net"] for i in items if i["net"] in pour]  # a split pour shows as zone-zone or pad-pad
            if zones:
                islands += zones
            else:
                rip += [i["net"] for i in items if i["net"] and i["net"] not in pour]
                centers += [i["at"] for i in items if i["net"] and i["net"] not in pour and i["at"]]
        else:
            for i in items:
                if i["kind"] not in ("Track", "Via") or not i["net"]:
                    continue
                if i["net"] in pour:
                    if i["kind"] == "Via" and i["at"]:
                        drop.append(i["at"])
                else:
                    rip.append(i["net"])
                if i["at"]:
                    centers.append(i["at"])
        left += (len(rip), len(islands), len(drop)) == n0
    rip, islands = list(dict.fromkeys(rip)), list(dict.fromkeys(islands))
    if radius and centers:
        near = {}
        for c in centers:
            for t in tracks:
                dist = LineString([(t["x1"], t["y1"]), (t["x2"], t["y2"])]).distance(Point(c)) - t["w"] / 2
                if t["net"] not in pour and dist <= radius:
                    near[t["net"]] = min(near.get(t["net"], 1e9), dist)
            for v in vias:
                dist = Point(v["x"], v["y"]).distance(Point(c))
                if v["net"] not in pour and dist <= radius:
                    near[v["net"]] = min(near.get(v["net"], 1e9), dist)
        rip += [n for n, _ in sorted(near.items(), key=lambda kv: kv[1]) if n not in rip]
    return {"rip": rip[:MAX_RIPUP], "islands": islands, "drop_vias": drop, "unfixable": left}


_OF = re.compile(r" of (\S+)$")


def fixed_error(x: dict, parts: dict) -> set[str] | None:
    """The parts a DRC error names when routing cannot cure it (no track, via or zone named, not unconnected) and
    every part it names is fixed in the placement; else None. `parts` maps a ref to an object with a `fixed` flag."""
    if x["type"] == "unconnected" or x["type"].startswith("parity:"):
        return None
    mine = set()
    for s in x.get("items") or []:
        if s.split(" ", 1)[0] in ("Track", "Via", "Zone"):
            return None
        m = re.match(r"Footprint (\S+)$", s) or _OF.search(s)
        if m:
            mine.add(m.group(1))
    if not mine or any(r not in parts or not parts[r].fixed for r in mine):
        return None
    return mine


def fixed_part_errors(drc: list[dict], parts: dict) -> list[str] | None:
    """The parts named by the DRC errors when every error is a fixed_error (routing cannot cure it and a new
    placement cannot move its parts either); else None."""
    refs: set[str] = set()
    for x in errors(drc):
        mine = fixed_error(x, parts)
        if mine is None:
            return None
        refs |= mine
    return sorted(refs) or None


# ------------------------------------------------------------------ GND islands

def zone_fill(board: Path) -> dict[tuple[str, str], list[Polygon]]:
    """(net, layer) -> filled polygons of the zones in a board that DRC refilled and saved."""
    t = sexpr.loads(board.read_text())
    out: dict[tuple[str, str], list[Polygon]] = {}
    for z in sexpr.find_all(t, "zone"):
        nn = sexpr.value(z, "net_name")
        if nn is None:
            nn = sexpr.value(z, "net")
        if nn is None or str(nn).isdigit():
            continue
        for fp in sexpr.find_all(z, "filled_polygon"):
            pts = [(float(q[1]), float(q[2])) for q in (sexpr.find(fp, "pts") or []) if isinstance(q, list) and q[0] == "xy"]
            if len(pts) >= 3:
                out.setdefault((str(nn), str(sexpr.value(fp, "layer"))), []).append(Polygon(pts).buffer(0))
    return out


def _obstacles(d, board: Path, net: str):
    """Copper of other nets, grown by the clearance and a via's radius, on all layers (a via is through)."""
    from ..board import pcb as _pcb
    b = d.board
    classes = _pcb.net_classes(d)
    r = b["via_dia_mm"] / 2
    rb = _pcb.read_board(board)
    geo = []
    for fp in rb["footprints"].values():
        for p in fp["pads"]:
            if p["net"] == net:
                continue
            cl = classes.get(p["net"], {}).get("clearance", b["clearance_mm"]) if p["net"] else b["clearance_mm"]
            geo.append(box(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2)
                       .buffer(r + max(cl, b["clearance_mm"]) + 0.05))
    for s in rb["segments"]:
        if s["net"] != net:
            cl = classes.get(s["net"], {}).get("clearance", b["clearance_mm"])
            geo.append(LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2 + r + max(cl, b["clearance_mm"]) + 0.05))
    for v in rb["vias"]:
        same = v["net"] == net
        cl = max(classes.get(v["net"], {}).get("clearance", b["clearance_mm"]), b["clearance_mm"])
        geo.append(Point(v["x"], v["y"]).buffer(2 * r + 0.1 if same else 2 * r + cl + 0.05))
    return unary_union(geo) if geo else Polygon()


def _groups(net: str, fill: dict, rb: dict) -> tuple[list, list, list]:
    """Connected groups of the pour of `net`: polygons of different layers joined by the net's vias and pads.
    Returns (nodes [(layer, polygon)], group index per node, whether the group holds a via or pad)."""
    nodes = [(ly, g) for (n, ly), gs in sorted(fill.items()) if n == net for g in gs]
    grp = list(range(len(nodes)))

    def find(i):
        while grp[i] != i:
            grp[i] = grp[grp[i]]
            i = grp[i]
        return i

    holds = [False] * len(nodes)
    layers = {ly for ly, _ in nodes}
    items = [(Point(v["x"], v["y"]).buffer(0.05), layers) for v in rb["vias"] if v["net"] == net]
    for fp in rb["footprints"].values():
        for p in fp["pads"]:
            if p["net"] == net:
                ly = {x for x in p["layers"]}
                ly = layers if ly & {"*.Cu"} else ly & layers
                items.append((box(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2).buffer(0.02), ly))
    for shape, ly in items:
        hit = [i for i, (l2, g) in enumerate(nodes) if l2 in ly and g.intersects(shape)]
        for i in hit:
            holds[i] = True
            grp[find(i)] = find(hit[0])
    return nodes, [find(i) for i in range(len(nodes))], holds


def stitch_vias(d, board: Path, net: str) -> tuple[list[dict], int, bool]:
    """Join the pour of `net` that DRC reports as split. The pour polygons of the refilled board are grouped by the
    vias and pads that join them; each group other than the biggest one gets a via inside it that lands in a polygon
    of the biggest group on another layer, clear of all other copper. Groups with a pad or a via, or a few mm2 of
    copper, count; smaller fragments are only a DRC warning. Returns (new vias, groups left without a place for a
    via, whether those hold no pad or via, so KiCad may remove them as islands)."""
    b = d.board
    r = b["via_dia_mm"] / 2
    fill = zone_fill(board)
    rb = pcb.read_board(board)
    nodes, grp, holds = _groups(net, fill, rb)
    if not nodes:
        return [], 0, False
    area, held = {}, {}
    for i, g in enumerate(grp):
        area[g] = area.get(g, 0) + nodes[i][1].area
        held[g] = held.get(g, False) or holds[i]
    main = max(area, key=area.get)
    from ...core import floorplan
    m = pcb.EDGE_CLEARANCE_MM + r + 0.1
    inner = floorplan.outline(d).buffer(-m, join_style="mitre")
    obst = _obstacles(d, board, net)
    for fp in rb["footprints"].values():  # no via inside a pad of the net either
        for p in fp["pads"]:
            if p["net"] == net:
                obst = obst.union(box(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2).buffer(r + 0.1))
    new, left, removable = [], 0, True
    for g in sorted((g for g in area if g != main and (held[g] or area[g] >= 2.0)), key=lambda g: -area[g]):
        best = None
        for i in [i for i, x in enumerate(grp) if x == g]:
            la, pa = nodes[i]
            for j in [j for j, x in enumerate(grp) if x == main]:
                lb, pb = nodes[j]
                if la == lb:
                    continue
                ok = pa.buffer(-(r + 0.05)).intersection(pb.buffer(-(r + 0.05))).intersection(inner).difference(obst)
                for piece in getattr(ok, "geoms", [ok]):
                    if piece.area > 0 and (best is None or piece.area > best.area):
                        best = piece
        if best is None:
            left += 1
            removable = removable and not held[g]
            continue
        q = best.centroid if best.contains(best.centroid) else best.representative_point()
        new.append({"net": net, "x": round(q.x, 3), "y": round(q.y, 3)})
        obst = obst.union(Point(q.x, q.y).buffer(2 * r + 0.1))
        grp = [main if x == g else x for x in grp]
    return new, left, removable


# ------------------------------------------------------------------ the loop

def thermal_keys(d, libs, parts) -> set:
    return {(round(v["x"], 2), round(v["y"], 2)) for v in pcb.thermal_vias(d, libs, parts)}


def reroute(d, libs, parts, workdir: Path, tracks: list, vias: list, nets: list, attempt: int, island: int,
            deadline: float | None = None) -> tuple[list, list]:
    """Re-route `nets` on a board that keeps `tracks` and `vias` (the copper of every other net). `deadline`
    (time.time()) stops the router calls (subprocess.TimeoutExpired)."""
    src = workdir / f"{d.name}_rep.kicad_pcb"
    pcb.write_board(src, pcb.build_board(d, libs, parts, tracks, vias, island_removal=island))
    n = len(krt.KRT_VARIANTS)
    variants = [krt.KRT_VARIANTS[(attempt + j) % n] for j in range(1 + int(d.board.get("router_retries", 3)))]
    cur, _, _, _ = krt.krt_route(d, src, workdir, f"{d.name}_rep{attempt}_", nets=set(nets), keep_first=True, variants=variants,
                                  deadline=deadline)
    rb = pcb.read_board(cur)
    skip = thermal_keys(d, libs, parts)  # build_board adds the declared thermal vias itself
    return ([dict(s) for s in rb["segments"] if s["net"]],
            [dict(v) for v in rb["vias"] if v["net"] and (round(v["x"], 2), round(v["y"], 2)) not in skip])


def repair(d, libs, parts, tracks: list, vias: list, drc: list[dict], board: Path, check, rounds: int = 3,
           deadline: float | None = None) -> dict:
    """Repair loop. `check(tracks, vias, island_removal) -> drc` writes the board and runs DRC (refilling the zones).
    `check` should be the fast DRC; the caller runs the full one on the kept board. A round is kept only if it has fewer errors (or equal errors and fewer unrouted) and no more unrouted than the
    best board so far; the best board is left in `board`. Skipped when every error is on fixed parts (fixed_error).
    Stops after `rounds` rounds, when nothing can be repaired, or after max_stall rounds in a row without improvement
    (one round when some errors are on fixed parts). Wall-clock stops are safety aborts only (settings.repair.budget_s,
    and `deadline`, the layout wall-time budget), listed in report["aborts"]. Returns the best copper, DRC and a
    report."""
    t0 = time.time()
    workdir = board.parent
    best = {"tracks": tracks, "vias": vias, "drc": drc, "island": 0, "score": score(drc)}
    drc_json = board.with_suffix(".drc.json")
    snap = (board.read_bytes(), drc_json.read_bytes() if drc_json.exists() else None)
    first, log, fails, island, done = best["score"], [], 0, 0, 0
    aborts: list[str] = []

    def abort(msg):
        aborts.append(msg)
        log.append(f"safety abort: {msg}")
    rs = get_settings().repair
    max_err, budget = rs.max_errors, rs.budget_s
    fixed = [x for x in errors(drc) if fixed_error(x, parts) is not None]  # errors no re-route can change
    stall = 1 if fixed else rs.max_stall
    size = load(drc)  # open nets once each: 41 unconnected items on 6 open nets is a repairable board
    skipped = size > max_err or bool(fixed and len(fixed) == len(errors(drc)))
    if size > max_err:
        log.append(f"repair skipped: {size} errors and open nets > limit {max_err} ({first[0]} DRC errors, "
                   f"{len(open_nets(drc))} open nets)")
    elif skipped:
        refs = sorted(set().union(*(fixed_error(x, parts) for x in fixed)))
        log.append(f"repair skipped: the {len(fixed)} errors are on fixed parts ({', '.join(refs)}), "
                   "which routing cannot change")
    for r in range(0 if skipped else max(rounds, 0)):
        if not best["score"][0]:
            break
        if not [x for x in errors(best["drc"]) if fixed_error(x, parts) is None]:
            log.append("the errors left are on fixed parts: nothing the router can repair")
            break
        if time.time() - t0 > budget:
            abort(f"repair stopped before round {r + 1}: {time.time() - t0:.0f} s > {budget:.0f} s "
                  "(HWFLOW_REPAIR_BUDGET_S)")
            break
        if deadline is not None and time.time() > deadline:
            abort(f"repair stopped before round {r + 1}: the layout wall-time budget is used up")
            break
        p = plan(d, best["tracks"], best["vias"], best["drc"], radius=2.0 * fails)
        if not (p["rip"] or p["islands"] or p["drop_vias"]):
            log.append("nothing the router can repair")
            break
        t1, done = time.time(), done + 1
        stop = t0 + budget if deadline is None else min(t0 + budget, deadline)
        keep_v = [v for v in best["vias"] if v["net"] not in p["rip"]
                  and not any(abs(v["x"] - a[0]) < 0.05 and abs(v["y"] - a[1]) < 0.05 for a in p["drop_vias"])]
        keep_t = [t for t in best["tracks"] if t["net"] not in p["rip"]]
        note = []
        new_isl = island
        if p["islands"] and not zone_fill(board):  # an unfilled board (fast DRC saves nothing): refill it once
            kicad.drc(board, refill=True)
        for net in p["islands"]:
            new, left, removable = stitch_vias(d, board, net)
            keep_v += new
            if left and removable:  # no place for a via: let KiCad remove the islands that hold no pad
                new_isl = 1
            note.append(f"{len(new)} stitching vias" + (f", {left} islands without room" if left else ""))
        try:
            if p["rip"]:
                keep_t, keep_v = reroute(d, libs, parts, workdir, keep_t, keep_v, p["rip"], r + fails, new_isl, stop)
                note.append(f"re-routed {len(p['rip'])} nets ({', '.join(p['rip'][:4])}{'...' if len(p['rip']) > 4 else ''})")
            cand = check(keep_t, keep_v, new_isl)
        except Exception as e:  # noqa: BLE001 - a router failure rejects the round
            if isinstance(e, subprocess.TimeoutExpired) and time.time() >= stop - 1e-3:
                abort(f"round {r + 1}: router stopped at the repair wall-time limit ({time.time() - t0:.0f} s, "
                      f"limit {budget:.0f} s)")
                board.write_bytes(snap[0])
                break
            log.append(f"round {r + 1}: {type(e).__name__}: {str(e)[:100]}")
            board.write_bytes(snap[0])
            fails += 1
            if fails >= stall:
                break
            continue
        s = score(cand)
        ok = s[1] <= best["score"][1] and s < best["score"]
        log.append(f"round {r + 1}: {'; '.join(note)}: {best['score'][0]} -> {s[0]} errors"
                   f"{'' if ok else ' (rejected)'}, {time.time() - t1:.1f} s")
        if ok:
            best = {"tracks": keep_t, "vias": keep_v, "drc": cand, "island": new_isl, "score": s}
            island = new_isl
            snap = (board.read_bytes(), drc_json.read_bytes() if drc_json.exists() else None)
            fails = 0
        else:
            board.write_bytes(snap[0])  # the best board stays in place (its DRC report too)
            if snap[1] is not None:
                drc_json.write_bytes(snap[1])
            fails += 1
            if fails >= stall:  # no progress `stall` rounds in a row
                log.append(f"repair stopped: {fails} round{'s' if fails > 1 else ''} without improvement")
                break
    for f in workdir.glob(f"{d.name}_rep*"):
        f.unlink()
    return {"tracks": best["tracks"], "vias": best["vias"], "drc": best["drc"], "island": best["island"],
            "report": {"before": first[0], "after": best["score"][0], "unrouted_before": first[1],
                       "unrouted_after": best["score"][1], "rounds": done, "log": log, "skipped": skipped,
                       "aborts": aborts,
                       "seconds": round(time.time() - t0, 1)}}


def summary(rep: dict) -> str:
    if rep.get("skipped"):
        return rep["log"][0]
    return (f"repair: {rep['before']} -> {rep['after']} errors in {rep['rounds']} rounds, {rep['seconds']} s"
            if rep["rounds"] else f"repair: {rep['before']} errors, none the router can repair")
