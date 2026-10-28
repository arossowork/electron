"""Connect a split pour net (GND) by rule after routing (settings.layout.pour_stitch, HWFLOW_POUR_STITCH).

The router never routes a pour net: a pad of it reaches the net only through the zone fill on its own layer, and the
layers join only at the through-hole pads and vias of the net. On a dense board the signal tracks cut the outer-layer
fill into fragments: a fragment that holds a pad and touches no via is an island (DRC: unconnected zone-zone or
pad-zone), a pad whose fill the clearances swallow sits in a pour hole (no fill touches it), and a plane layer that
no via joins floats (no DRC error: it holds no pad). The repair loop's stitching (repair.stitch_vias) puts a via
only inside a fragment, which a 1-3 mm2 fragment around one pad never has room for, and it shares its round with the
rip-up of the signal nets, so the round is lost when that re-route times out or is rejected.

`plan` works on the refilled board. The fill polygons of the net are grouped by the vias and pads that join them
(repair._groups); the main group is the one with the most pads (then the largest). Every other group with a pad, or
of at least MIN_AREA_MM2, and every pad that touches no fill, is joined to the main group by the first of:

1. a via inside the group's fill that lands in the main group's fill on another layer (as repair.stitch_vias);
2. a via in a pad of the group that holds it (an exposed pad: via in pad, as thermal vias; a pasted pad only when
   it is two vias wide) that lands in the main group's fill on another layer;
3. a short stub (at most REACH_MM beyond the pad) on the pad's layer to a via that lands in the main group's fill
   (the inner plane on a 4-layer board): the pad's fan-out via;
4. (`rip`) the same stub and via where they cross routed tracks or vias of other nets, the fewest such nets first
   (never an isolation-group member or a net with a layer rule): `stitch` removes those nets' copper, places the
   stub and via and routes the nets again (repair.reroute). The re-routed nets take new paths, so free vias (1-3)
   are tried again on that board; a displacement that leaves a displaced net open, or does not end with fewer
   errors, is undone and the next one displaces other nets.

Every via keeps the clearance (the larger of the board and the two net-class clearances, and the isolation distance
of `board.isolation` and the creepage drc_rules when the other net is across a group) to the copper of other nets on
every layer, stays out of the other pads of the net, the rule areas that forbid vias and the board-edge margin; a
stub keeps the same distances on its layer and stays out of the rule areas that forbid tracks. `stitch` keeps a
result only when the DRC has fewer errors and no more errors of any other type than unconnected."""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from shapely import STRtree
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from ...core import sexpr
from ..board import keepout, pcb, routing
from . import repair

MIN_AREA_MM2 = 2.0  # a fragment without a pad smaller than this is only a DRC warning (as repair.stitch_vias)
REACH_MM = 2.5  # longest stub, beyond the pad edge
STEP_MM = 0.1  # radial step of the via search
ANGLES = 24  # directions of the via search around a pad
ROUNDS = 2  # rounds of free vias and stubs: refill and regroup (a new via changes the fill)
MAX_RIP = 3  # routed nets one stub may displace
DISPLACE_ROUNDS = 3  # rounds that displace routed nets, each followed by free rounds on the re-routed board


def _box(p: dict, grow: float = 0.0):
    return box(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2).buffer(grow)


def _cu(layers: list[str], copper: list[str]) -> list[str]:
    return routing.pad_layers(layers, copper)


def isolation_mm(d, net: str) -> dict[str, float]:
    """Other net -> the isolation distance `net` keeps to it (pcb.isolation_needs: one net in a group, the other not)."""
    out: dict[str, float] = {}
    for g in pcb.isolation_needs(d):
        mem = set(g["members"])
        for other in d.nets():
            if (net in mem) != (other in mem):
                out[other] = max(out.get(other, 0.0), float(g["mm"]))
    return out


def fixed_nets(d) -> set[str]:
    """Nets a stub never displaces: isolation-group members and nets with a layer rule."""
    out = {n for g in pcb.isolation_needs(d) for n in g["members"]}
    return out | set(pcb.net_layers(d))


class _Space:
    """The copper of other nets than `net`, each item with the distance it keeps to `net`: pads (fixed) and routed
    tracks and vias (by net, which `rip` may displace)."""

    def __init__(self, d, rb: dict, tree: list, net: str, copper: list[str]):
        b = d.board
        classes = pcb.net_classes(d)
        iso = isolation_mm(d, net)
        own = max(b["clearance_mm"], classes.get(net, {}).get("clearance", b["clearance_mm"]))

        def gap(other):
            cl = classes.get(other, {}).get("clearance", b["clearance_mm"]) if other else b["clearance_mm"]
            return max(cl, own, iso.get(other, 0.0)) + 0.02

        self.copper = copper
        fixed: dict[str, list] = {L: [] for L in copper}
        routed: dict[str, list] = {L: [] for L in copper}  # (geometry, gap, net)
        for fp in rb["footprints"].values():
            for p in fp["pads"]:
                if p["net"] == net:
                    continue
                ls = _cu(p["layers"], copper) or (copper if p["kind"] == "np_thru_hole" else [])
                for L in ls:
                    fixed[L].append((_box(p), gap(p["net"])))
        for s in rb["segments"]:
            if s["net"] != net and s["layer"] in routed:
                routed[s["layer"]].append((LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2),
                                           gap(s["net"]), s["net"]))
        vr = b["via_dia_mm"] / 2
        self.same = []
        for v in rb["vias"]:
            if v["net"] == net:
                self.same.append(Point(v["x"], v["y"]))
            else:
                for L in copper:
                    routed[L].append((Point(v["x"], v["y"]).buffer(vr), gap(v["net"]), v["net"]))
        self.fixed, self.routed = fixed, routed
        self.tree = {L: STRtree([g for g, _, _ in routed[L]]) for L in copper}
        self.reach = max([g for L in copper for _, g, _ in routed[L]] + [g for L in copper for _, g in fixed[L]] + [own])
        self.vias_ko = [a for a in keepout.rule_areas(tree, ("vias",)) if "vias" in a["forbids"]]
        self.tracks_ko = [a for a in keepout.rule_areas(tree, ("tracks",)) if "tracks" in a["forbids"]]
        self._grown: dict = {}
        self.via_r = vr

    def _fixed_grown(self, layer: str, r: float):
        k = (layer, round(r, 4))
        if k not in self._grown:
            f = self.fixed[layer]
            self._grown[k] = unary_union([s.buffer(g + r) for s, g in f]) if f else None
        return self._grown[k]

    def _routed_hits(self, layer: str, shape, r: float) -> set[str] | None:
        """Nets of the routed copper on `layer` closer to `shape` than their distance plus `r`."""
        out = set()
        items = self.routed[layer]
        for i in self.tree[layer].query(shape.buffer(self.reach + r)):
            g, gap, n = items[int(i)]
            if g.distance(shape) < gap + r:
                out.add(n)
        return out

    def via(self, q: Point, own_pads) -> set[str] | None:
        """None when a via at `q` is blocked by fixed copper, a rule area or another via of the net; else the nets of
        the routed copper it would cross (empty: free)."""
        r = self.via_r
        for L in self.copper:
            g = self._fixed_grown(L, r)
            if g is not None and g.contains(q):
                return None
        if own_pads is not None and own_pads.contains(q):
            return None
        if any(q.distance(v) < 2 * r + 0.1 for v in self.same):
            return None
        disk = q.buffer(r)
        if any(a["poly"].intersects(disk) and _cu(a["layers"], self.copper) for a in self.vias_ko):
            return None
        nets = set()
        for L in self.copper:
            nets |= self._routed_hits(L, q, r)
        return nets

    def stub(self, line: LineString, layer: str, w: float) -> set[str] | None:
        """As `via`, for a track of width `w` along `line` on `layer`."""
        g = self._fixed_grown(layer, w / 2)
        if g is not None and g.intersects(line):
            return None
        band = line.buffer(w / 2)
        if any(a["poly"].intersects(band) and layer in _cu(a["layers"], self.copper) for a in self.tracks_ko):
            return None
        return self._routed_hits(layer, line, w / 2)

    def add_via(self, q: Point):
        self.same.append(q)


def _candidates(piece):
    """Via points in a polygon, the most interior first: its centroid, a representative point, then a coarse grid."""
    out = []
    c = piece.centroid
    if piece.contains(c):
        out.append(c)
    out.append(piece.representative_point())
    x0, y0, x1, y1 = piece.bounds
    n = 6
    for i in range(n + 1):
        for j in range(n + 1):
            q = Point(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * j / n)
            if piece.contains(q):
                out.append(q)
    return out


def plan(d, board: Path, net: str, rip: bool = False, avoid=()) -> dict:
    """What joins the split pour of `net` on the refilled `board`: {tracks, vias, rip (nets whose copper the stubs
    cross; only with `rip`, never a net of `avoid`), stats {groups, pads, inside, in_pad, stub, rip, left}}."""
    b = d.board
    r = b["via_dia_mm"] / 2
    copper = pcb.cu_layers(b["layers"])
    fill = repair.zone_fill(board)
    rb = pcb.read_board(board)
    nodes, grp, _ = repair._groups(net, fill, rb)
    stats = Counter(groups=0, pads=0, inside=0, in_pad=0, stub=0, rip=0, left=0)
    out = {"tracks": [], "vias": [], "rip": [], "stats": stats}
    if not nodes:
        return out
    pads = [p for fp in rb["footprints"].values() for p in fp["pads"] if p["net"] == net]
    pads_of = []  # groups each pad touches
    for p in pads:
        ls, shape = _cu(p["layers"], copper), _box(p, 0.02)
        pads_of.append({grp[i] for i, (ly, g) in enumerate(nodes) if ly in ls and g.intersects(shape)})
    area: dict[int, float] = {}
    for i, g in enumerate(grp):
        area[g] = area.get(g, 0.0) + nodes[i][1].area
    npads = Counter(g for gs in pads_of for g in gs)
    main = max(area, key=lambda g: (npads.get(g, 0), area[g]))
    space = _Space(d, rb, sexpr.loads(board.read_text()), net, copper)
    from ...core import floorplan
    inner = floorplan.outline(d).buffer(-(pcb.EDGE_CLEARANCE_MM + r + 0.1), join_style="mitre")
    own = unary_union([_box(p, r + 0.1) for p in pads]) if pads else None
    landing = {}
    for i, (ly, g) in enumerate(nodes):
        if grp[i] == main:
            landing.setdefault(ly, []).append(g)
    landing = {ly: unary_union(gs).buffer(-0.05) for ly, gs in landing.items()}
    no_rip = fixed_nets(d) | set(avoid)
    size = {n: len(pins) for n, pins in d.nets().items()}  # a displaced net's re-route grows with its pads

    def cost(nets, rad):  # the cheapest displacement: the fewest pads to re-route, then the fewest nets, the shortest
        return sum(size.get(n, 0) for n in nets), len(nets), rad
    widths = sorted({float(b["track_mm"]), routing.neck_floor(b)}, reverse=True)

    def lands(q: Point, not_on=()) -> bool:
        return any(g.contains(q) for ly, g in landing.items() if ly not in not_on)

    def take(q: Point, line=None, layer=None, w=None):
        out["vias"].append({"net": net, "x": round(q.x, 4), "y": round(q.y, 4)})
        space.add_via(q)
        if line is not None:
            (x1, y1), (x2, y2) = line.coords
            out["tracks"].append({"net": net, "x1": round(x1, 4), "y1": round(y1, 4), "x2": round(x2, 4),
                                  "y2": round(y2, 4), "w": w, "layer": layer})

    def inside(g) -> Point | None:
        """A via in the fill of group `g` that lands in the main group's fill on another layer."""
        best = None
        for i in [i for i, x in enumerate(grp) if x == g]:
            la, pa = nodes[i]
            for ly, pb in landing.items():
                if ly == la:
                    continue
                ok = pa.buffer(-(r + 0.05)).intersection(pb.buffer(-r)).intersection(inner)
                for piece in getattr(ok, "geoms", [ok]):
                    if piece.is_empty or piece.area <= 0 or (best and piece.area <= best[1]):
                        continue
                    q = next((q for q in _candidates(piece) if space.via(q, own) == set()), None)
                    if q is not None:
                        best = (q, piece.area)
        return best[0] if best else None

    def in_pad(p) -> Point | None:
        """A via in pad `p` that lands in the main fill on another layer. The pad holds the via with 0.15 mm to spare
        and has no paste (an exposed pad with its paste in windows, a test pad) or is at least two vias wide: solder
        on a smaller pasted pad would wick into the hole."""
        paste = any(x.endswith(".Paste") for x in p["layers"])
        if (min(p["w"], p["h"]) < (4 * r if paste else 2 * r + 0.3) or p["kind"] != "smd"
                or len(_cu(p["layers"], copper)) != 1):
            return None
        q = Point(p["x"], p["y"])
        pl = _cu(p["layers"], copper)
        others = unary_union([_box(o, r + 0.1) for o in pads if o is not p]) if len(pads) > 1 else None
        if not inner.contains(q) or not lands(q, pl) or space.via(q, others) != set():
            return None
        return q

    def stub(p, allow: bool):
        """The stub from pad `p` to a via that lands in the main fill: (line, layer, width, via, nets crossed). The
        shortest free one; with `allow` the one whose crossed routed nets have the fewest pads in all (a large net
        such as a supply rail takes the re-route past its budget), then the fewest nets, then the shortest."""
        c = Point(p["x"], p["y"])
        half = math.hypot(p["w"], p["h"]) / 2
        rad = min(p["w"], p["h"]) / 2 + r + 0.1
        best = None
        while rad <= half + REACH_MM + 1e-9:
            for a in range(ANGLES):
                ang = 2 * math.pi * a / ANGLES
                q = Point(p["x"] + rad * math.cos(ang), p["y"] + rad * math.sin(ang))
                if not inner.contains(q) or not lands(q):
                    continue
                hv = space.via(q, own)
                if hv is None or (hv and not allow) or hv & no_rip:
                    continue
                line = LineString([(c.x, c.y), (q.x, q.y)])
                for layer in _cu(p["layers"], copper):
                    for w in widths:
                        hs = space.stub(line, layer, w)
                        if hs is None or (hs and not allow) or hs & no_rip:
                            continue
                        nets = hv | hs
                        if len(nets) > MAX_RIP:
                            continue
                        if not nets:
                            return line, layer, w, q, nets
                        if best is None or cost(nets, rad) < best[5]:
                            best = (line, layer, w, q, nets, cost(nets, rad))
                        break
            rad += STEP_MM
        return best[:5] if best else None

    def join(pidx: list[int]) -> bool:
        for k in pidx:
            q = in_pad(pads[k])
            if q is not None:
                take(q)
                stats["in_pad"] += 1
                return True
        for allow in (False, True) if rip else (False,):
            for k in pidx:
                s = stub(pads[k], allow)
                if s:
                    line, layer, w, q, nets = s
                    take(q, line, layer, w)
                    stats["rip" if nets else "stub"] += 1
                    out["rip"] += [n for n in sorted(nets) if n not in out["rip"]]
                    return True
        return False

    merged = {main}
    for g in sorted((g for g in area if g != main), key=lambda g: -area[g]):
        gp = [k for k, gs in enumerate(pads_of) if g in gs]
        if not gp and area[g] < MIN_AREA_MM2:
            continue
        if any(pads_of[k] & merged for k in gp):  # a pad joins it to a group already joined
            merged.add(g)
            continue
        stats["groups"] += 1
        q = inside(g)
        if q is not None:
            take(q)
            stats["inside"] += 1
            merged.add(g)
        elif join(gp):
            merged.add(g)
        else:
            stats["left"] += 1
    for k, gs in enumerate(pads_of):  # pads in a pour hole: no fill touches them
        if not gs:
            stats["pads"] += 1
            if not join([k]):
                stats["left"] += 1
    return out


def split(d, drc: list[dict]) -> list[str]:
    """The pour nets the DRC reports as unconnected."""
    pour = set(d.ground_nets()) if d.board["gnd_pour"] else set()
    out = []
    for x in repair.errors(drc):
        if x["type"] == "unconnected":
            for s in x["items"]:
                n = repair.parse_item(s, None)["net"]
                if n in pour and n not in out:
                    out.append(n)
    return out


def open_nets(drc: list[dict]) -> set[str]:
    """Nets of the unconnected items of a DRC."""
    return {n for x in repair.errors(drc) if x["type"] == "unconnected"
            for n in (repair.parse_item(s, None)["net"] for s in x["items"]) if n}


def _other(drc: list[dict]) -> Counter:
    return Counter(x["type"] for x in repair.errors(drc) if x["type"] != "unconnected")


def better(new: list[dict], old: list[dict]) -> bool:
    """Fewer errors (repair.score), and no error type other than unconnected counts more."""
    o, n = _other(old), _other(new)
    return repair.score(new) < repair.score(old) and all(n[k] <= o[k] for k in n)


def stitch(d, libs, parts, board: Path, tracks: list, vias: list, drc: list[dict], check,
           deadline: float | None = None) -> dict:
    """Join the split pours of the routed board. `check(tracks, vias) -> drc` writes `board` and runs the DRC with
    refill (the board then holds the fill). Every pour net is planned (a floating plane is no DRC error), in at most
    ROUNDS rounds of free vias and stubs, then up to DISPLACE_ROUNDS rounds that displace routed nets
    (repair.reroute, within the repair budget settings.repair.budget_s). A free round is kept only when `better`.
    A displacing round changes the copper around the island: the re-routed nets take new paths, so the new via can
    land in another pocket of the fill and a new free via can become possible. It is therefore kept provisionally
    (no more errors) and followed by free rounds on the re-routed board; the whole sequence is kept only when it ends
    `better` than the board before it, else that board is restored. The board left in `board` is the one returned.
    Returns {tracks, vias, drc, log, added}."""
    import time
    pour = list(d.ground_nets()) if d.board["gnd_pour"] else []
    log, added = [], [0, 0]
    if not pour:
        return {"tracks": tracks, "vias": vias, "drc": drc, "log": log, "added": tuple(added)}
    if not repair.zone_fill(board):
        drc = check(tracks, vias)
    snap = board.read_bytes()

    def note(net, st, kind):
        log.append(f"{kind} {net}: {st['groups']} split groups, {st['pads']} pads in pour holes -> {st['inside']} vias "
                   f"in the fill, {st['in_pad']} in pads, {st['stub']} stub vias, {st['rip']} displacing, "
                   f"{st['left']} left")

    tried: set[str] = set()  # displaced nets of the displacing rounds that did not pay off
    last: list[str] = []

    def one(rip: bool, label: str, provisional: bool = False) -> bool:
        """One round: plan every pour net, check, keep or restore. True when the round was kept. `provisional`
        keeps a displacing round that is not worse (the caller judges it after the free rounds that follow)."""
        nonlocal tracks, vias, drc, snap
        tr, vi, nets = [], [], []
        for net in pour:
            p = plan(d, board, net, rip=rip, avoid=tried)
            if p["tracks"] or p["vias"] or p["stats"]["left"]:
                note(net, p["stats"], label)
            tr += p["tracks"]
            vi += p["vias"]
            nets += [n for n in p["rip"] if n not in nets]
        last[:] = nets
        if not (tr or vi):
            return False
        keep_t = [t for t in tracks if t["net"] not in nets] + tr
        keep_v = [v for v in vias if v["net"] not in nets] + vi
        try:
            if nets:
                from ...core.settings import get_settings
                stop = time.time() + get_settings().repair.budget_s  # the re-route of the displaced nets: the repair budget
                stop = stop if deadline is None else min(stop, deadline)
                keep_t, keep_v = repair.reroute(d, libs, parts, board.parent, keep_t, keep_v, nets, 0, 0, stop)
                log.append(f"re-routed {len(nets)} displaced nets ({', '.join(nets[:4])}{'...' if len(nets) > 4 else ''})")
            cand = check(keep_t, keep_v)
        except Exception as e:  # noqa: BLE001 - a router failure rejects the round
            log.append(f"{label}: {type(e).__name__}: {str(e)[:100]}")
            board.write_bytes(snap)
            return False
        broken = sorted((open_nets(cand) - open_nets(drc)) & set(nets))
        if broken:  # a pour island traded for an open signal net: the repair rarely closes it, other nets may do
            log.append(f"{label} rejected: the displaced {', '.join(broken)} left open")
            board.write_bytes(snap)
            return False
        o = _other(drc)
        same = repair.score(cand) <= repair.score(drc) and all(n <= o[t] for t, n in _other(cand).items())
        if better(cand, drc) or ((not nets or provisional) and same):  # free vias that only join a floating plane count too
            tracks, vias, drc, snap = keep_t, keep_v, cand, board.read_bytes()
            added[0] += len(tr)
            added[1] += len(vi)
            log.append(f"{label} kept: {repair.score(drc)[0]} errors")
            return True
        log.append(f"{label} rejected: {repair.score(cand)[0]} errors ({dict(_other(cand))})")
        board.write_bytes(snap)
        return False

    def free(tag: str = ""):
        for k in range(ROUNDS):
            if (deadline is not None and time.time() > deadline) or not one(False, f"{tag}round {k + 1}"):
                break

    def plane_round():
        """KiCadRoutingTools' plane repair (`planes`) for the pockets the free vias and stubs leave: it traces a pad
        to the plane with a via, ripping and re-routing the signal nets in its way in the same run; a ripped net it
        leaves open is routed again once (repair.reroute). Kept only when `better`, with no net of fixed_nets moved
        and no other net left open."""
        nonlocal tracks, vias, drc, snap
        left = split(d, drc)
        if not left or libs is None or (deadline is not None and time.time() > deadline):
            return
        from ...core.settings import get_settings
        stop = time.time() + get_settings().repair.budget_s
        stop = stop if deadline is None else min(stop, deadline)
        try:
            res = planes(d, libs, parts, board, left, stop)
            if res is None:
                return
            tr, vi = res
            cand = check(tr, vi)
            opened = sorted((open_nets(cand) - open_nets(drc)) - set(left))
            if opened:  # nets the plane repair ripped and could not put back: one re-route on its board
                tr, vi = repair.reroute(d, libs, parts, board.parent, [t for t in tr if t["net"] not in opened],
                                        [v for v in vi if v["net"] not in opened], opened, 0, 0, stop)
                cand = check(tr, vi)
        except Exception as e:  # noqa: BLE001 - a router failure leaves the board as it was
            log.append(f"plane repair: {type(e).__name__}: {str(e)[:100]}")
            board.write_bytes(snap)
            return
        moved = _changed(tracks, tr) - set(left)  # a net with a layer rule or in an isolation group stays put
        bad = sorted((moved & fixed_nets(d)) | ((open_nets(cand) - open_nets(drc)) - set(left)))
        if not bad and better(cand, drc):
            added[0] += max(0, sum(t["net"] in left for t in tr) - sum(t["net"] in left for t in tracks))
            added[1] += max(0, sum(v["net"] in left for v in vi) - sum(v["net"] in left for v in vias))
            tracks, vias, drc, snap = tr, vi, cand, board.read_bytes()
            log.append(f"plane repair kept: {repair.score(drc)[0]} errors")
        else:
            why = f"moved or left open {', '.join(bad)}" if bad else \
                f"{repair.score(cand)[0]} errors ({dict(_other(cand))})"
            log.append(f"plane repair rejected: {why}")
            board.write_bytes(snap)

    free()
    plane_round()
    for k in range(DISPLACE_ROUNDS):
        if not split(d, drc) or libs is None or (deadline is not None and time.time() > deadline):
            break
        base = (tracks, vias, drc, snap, list(added))
        kept = one(True, f"displace {k + 1}", provisional=True)
        moved = list(last)  # before the free rounds overwrite it
        if kept:
            free(f"displace {k + 1} ")
            if better(drc, base[2]):
                continue
            # the displacement did not pay off (a displaced net left open, the island still cut off): back to the
            # board before it
            tracks, vias, drc, snap, added = base[0], base[1], base[2], base[3], base[4]
            board.write_bytes(snap)
            log.append(f"displace {k + 1} undone: {repair.score(drc)[0]} errors")
        if not moved:  # nothing left to displace
            break
        tried |= set(moved)  # the next round displaces other nets
    return {"tracks": tracks, "vias": vias, "drc": drc, "log": log, "added": tuple(added)}


def _changed(old: list, new: list) -> set[str]:
    """Nets whose routed tracks differ between two track lists."""
    def key(ts):
        out: dict[str, set] = {}
        for t in ts:
            out.setdefault(t["net"], set()).add((round(t["x1"], 3), round(t["y1"], 3), round(t["x2"], 3),
                                                 round(t["y2"], 3), t["layer"]))
        return out
    a, b = key(old), key(new)
    return {n for n in set(a) | set(b) if a.get(n) != b.get(n)}


def planes(d, libs, parts, board: Path, nets: list[str], stop: float):
    """KiCadRoutingTools' plane repair (py_router/repair_planes.py) on the refilled `board`, for the pour
    `nets`: a pad of the pour in a pocket of the fill gets a trace and a via to the plane, the signal nets in its way
    ripped and re-routed in the same run (at most MAX_RIP per pad), at the board's sizes (no escalation, no via in a
    pad). Returns (tracks, vias) of the result, or None when the router changed nothing."""
    import shutil
    import sys
    import time
    from . import krt
    b = d.board
    wd = board.parent
    src = wd / f"{d.name}_planes.kicad_pcb"
    dst = wd / f"{d.name}_planes_out.kicad_pcb"
    shutil.copy(board, src)
    for suf in (".kicad_pro", ".kicad_dru"):  # the router reads net-class clearances and rules from the siblings
        f = wd / f"{d.name}{suf}"
        if f.exists():
            shutil.copy(f, src.with_suffix(suf))
    script = krt.krt_dir() / "py_router" / "repair_planes.py"
    if not script.exists():
        return None
    args = [sys.executable, str(script), str(src), str(dst), "--nets", *nets,
            "--clearance", str(b["clearance_mm"]), "--track-width", str(b["track_mm"]),
            "--min-track-width", str(routing.neck_floor(b)), "--via-size", str(b["via_dia_mm"]),
            "--via-drill", str(b["via_drill_mm"]), "--same-net-pad-clearance", "0.15", "--fab-tier", "standard",
            "--escalation", "off", "--no-fix-drc-settings", "--rip-blocker-nets", "--max-rip-nets", str(MAX_RIP),
            "--no-kicad-recheck"]  # the caller's DRC judges the result
    dst.unlink(missing_ok=True)
    try:
        krt.run_group(args, timeout=max(1.0, stop - time.time()), cwd=krt.krt_dir(),
                      env=krt.router_env())
        if not dst.exists():
            return None
        rb = pcb.read_board(dst)
    finally:
        for f in wd.glob(f"{d.name}_planes*"):
            f.unlink()
    skip = repair.thermal_keys(d, libs, parts)  # build_board adds the declared thermal vias itself
    tr = [dict(s) for s in rb["segments"] if s["net"]]
    vi = [dict(v) for v in rb["vias"] if v["net"] and (round(v["x"], 2), round(v["y"], 2)) not in skip]
    return tr, vi
