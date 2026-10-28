"""Pour-pad taps before signal routing (settings.layout.pour_taps, HWFLOW_POUR_TAPS; on in the fast preset).

The router never routes a pour net: a pad of it reaches the net through the fill of its own layer, and the layers
join at the net's vias and through-hole pads. Signal tracks routed later can wall a surface-mount pad of the pour net
in a pocket of the fill that no via can reach (pourstitch then has to displace routed nets, which a dense board often
cannot re-route). `plan` gives every such pad its tap first, on the unrouted board, and the router keeps the taps as
fixed copper (krt_route keep_first, pre-existing copper not ripped):

0. a pad whose copper already reaches another layer (a track of a layout block to a via or a through-hole pad, or a
   pad joined by such a track to a pad that got its tap) needs none;
1. a pad of a footprint that already has a tap on the net (or a through-hole pad of it) joins it by a straight track
   when that track is free (a module's grid of ground pads becomes one group with one via);
2. else a short stub (at most REACH_MM beyond the pad) on the pad's layer to a via that keeps every clearance of
   pourstitch._Space (other nets' pads and their isolation distances, rule areas, the net's own pads: no via in a pad,
   so a small pasted pad keeps its paste), the shortest first, outward from the footprint before inward.

A via never lies inside the courtyard of a surface-mount footprint of COURTYARD_MODULE_MM2 or more (a module, whose
underside holds pads or metal) unless that component sets `vias_under: true`. Through-hole pads, pads with declared
thermal vias and pads of a footprint without copper on another layer's pour are left as they are. A pad that gets no
tap is listed (the pour stitching after routing stays the fallback)."""

from __future__ import annotations

import math
from pathlib import Path

from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from ...core import sexpr
from ...core.geometry import COURTYARD_MARGIN
from ..board import keepout, pcb, routing

REACH_MM = 2.5  # longest stub beyond the pad edge
LINK_MM = 3.0  # longest pad-to-pad link inside a footprint
STEP_MM = 0.1
ANGLES = 24
COURTYARD_MODULE_MM2 = 25.0  # a surface-mount footprint this large is a module: no via under it


def on(design) -> bool:
    from ...core.settings import get_settings
    return bool(get_settings().layout.pour_taps and design.board.get("gnd_pour"))


def _box(p, grow=0.0):
    return box(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2).buffer(grow)


def _modules(design, libs, parts) -> list:
    """Courtyard boxes of the modules (surface-mount footprints of COURTYARD_MODULE_MM2 or more) without
    `vias_under`."""
    out = []
    for ref, p in (parts or {}).items():
        c = design.components.get(ref)
        if c is None or c.get("vias_under") or not c.get("footprint"):
            continue
        try:
            if not libs.footprint(c.get("footprint"))["smd"]:
                continue
        except Exception:  # noqa: BLE001 - no footprint: nothing to keep vias from
            continue
        for side in ("front", "back"):
            b = p.side_box(side)
            if b is None:
                continue
            b = (b[0] + COURTYARD_MARGIN, b[1] + COURTYARD_MARGIN, b[2] - COURTYARD_MARGIN, b[3] - COURTYARD_MARGIN)
            if (b[2] - b[0]) * (b[3] - b[1]) >= COURTYARD_MODULE_MM2:
                out.append(box(*b))
    return out


def plan(design, libs, parts, board: Path) -> dict:
    """Taps of every surface-mount pad of the pour nets on the unrouted `board`: {tracks, vias, untapped [ref.pad],
    stats}. The pads, rule areas and copper already on `board` are the obstacles."""
    from ...core import floorplan
    from . import pourstitch
    b = design.board
    out = {"tracks": [], "vias": [], "untapped": [], "stats": {"pads": 0, "joined": 0, "linked": 0, "stub": 0}}
    if not b.get("gnd_pour"):
        return out
    copper = pcb.cu_layers(b["layers"])
    rb = pcb.read_board(board)
    tree = sexpr.loads(Path(board).read_text())
    r = b["via_dia_mm"] / 2
    inner = floorplan.outline(design).buffer(-(pcb.EDGE_CLEARANCE_MM + r + 0.1), join_style="mitre")
    modules = _modules(design, libs, parts)
    skip_layers = set(b.get("pour_skip_layers", []))
    pour_ko = [a for a in keepout.rule_areas(tree, ("copperpour",)) if "copperpour" in a["forbids"]]
    thermal = {(ref, str((design.components[ref].get("thermal_vias") or {}).get("pad", "")))
               for ref in design.components if design.components[ref].get("thermal_vias")}
    classes = pcb.net_classes(design)
    for net in sorted(design.ground_nets()):
        pads = [(ref, p) for ref, fp in sorted(rb["footprints"].items()) for p in fp["pads"] if p["net"] == net]
        if not pads:
            continue
        space = pourstitch._Space(design, rb, tree, net, copper)
        own = unary_union([_box(p, r + 0.1) for _, p in pads])
        w0 = float(classes.get(net, {}).get("width", b["track_mm"]))
        widths = sorted({min(w0, float(b["track_mm"])), routing.neck_floor(b)}, reverse=True)

        def lands(q: Point, layer: str) -> bool:
            """A via at q reaches the net's pour on a layer other than `layer`."""
            if not inner.contains(q) or any(m.contains(q) for m in modules):
                return False
            for L in copper:
                if L == layer or L in skip_layers:
                    continue
                if not any(a["poly"].contains(q) and L in routing.pad_layers(a["layers"], copper) for a in pour_ko):
                    return True
            return False

        tapped: dict[str, list] = {}  # ref -> [(layer, point)] of the net's copper on that footprint (pads, vias)
        for ref, p in pads:
            if p["kind"] == "thru_hole":
                tapped.setdefault(ref, []).append(("*", Point(p["x"], p["y"])))
        group, anchored = _groups(rb, net, pads, copper)
        done = set(anchored)  # copper groups that reach another layer (a via, a through-hole pad, a tap)
        todo = []
        for ref, p in pads:
            ls = routing.pad_layers(p["layers"], copper)
            if p["kind"] != "smd" or len(ls) != 1 or (ref, p["number"]) in thermal:
                continue
            todo.append((ref, p, ls[0]))
        # outer pads of a footprint first: the inner ones (a module's ground grid) link to them
        cen = {ref: (fp["x"], fp["y"]) for ref, fp in rb["footprints"].items()}
        todo.sort(key=lambda t: (t[0], -math.hypot(t[1]["x"] - cen[t[0]][0], t[1]["y"] - cen[t[0]][1])))
        def link(ref, p, layer) -> bool:
            """A straight track from pad `p` to the net's copper already on its footprint (nearest first)."""
            c = Point(p["x"], p["y"])
            for L, q in sorted(tapped.get(ref, []), key=lambda t: c.distance(t[1])):
                if c.distance(q) > LINK_MM or L not in ("*", layer) or c.distance(q) < 1e-6:
                    continue
                line = LineString([(c.x, c.y), (q.x, q.y)])
                w = next((w for w in widths if space.stub(line, layer, w) == set()), None)
                if w is not None:
                    out["tracks"].append(_track(net, line, layer, w))
                    out["stats"]["linked"] += 1
                    tapped[ref].append((layer, c))
                    return True
            return False

        left = []
        for ref, p, layer in todo:
            out["stats"]["pads"] += 1
            c = Point(p["x"], p["y"])
            g = group[(ref, p["number"])]
            if g in done:  # 0. its copper (a layout block's track, a pad's own tap) already reaches another layer
                out["stats"]["joined"] += 1
                tapped.setdefault(ref, []).append((layer, c))
                continue
            if link(ref, p, layer):  # 1. joined to the net's copper already on this footprint
                done.add(g)
                continue
            # 2. a stub to a via
            half = math.hypot(p["w"], p["h"]) / 2
            rad = min(p["w"], p["h"]) / 2 + r + 0.1
            fx, fy = cen[ref]
            best = None
            while rad <= half + REACH_MM + 1e-9 and best is None:
                cands = []
                for a in range(ANGLES):
                    ang = 2 * math.pi * a / ANGLES
                    q = Point(p["x"] + rad * math.cos(ang), p["y"] + rad * math.sin(ang))
                    out_ = (q.x - fx) * (p["x"] - fx) + (q.y - fy) * (p["y"] - fy)  # > 0: away from the centre
                    cands.append((0 if out_ > 0 else 1, a, q))
                for _, _, q in sorted(cands, key=lambda t: (t[0], t[1])):
                    if not lands(q, layer) or space.via(q, own) != set():
                        continue
                    line = LineString([(c.x, c.y), (q.x, q.y)])
                    w = next((w for w in widths if space.stub(line, layer, w) == set()), None)
                    if w is not None:
                        best = (q, line, w)
                        break
                rad += STEP_MM
            if best is None:
                left.append((ref, p, layer))
                continue
            q, line, w = best
            out["tracks"].append(_track(net, line, layer, w))
            out["vias"].append({"net": net, "x": round(q.x, 4), "y": round(q.y, 4)})
            space.add_via(q)  # the taps of a net share copper: only the via-to-via spacing between them
            tapped.setdefault(ref, []).append(("*", q))
            tapped[ref].append((layer, c))
            out["stats"]["stub"] += 1
            done.add(g)
        while left:  # a pad left without a tap joins one its neighbours got (a module's inner ground grid)
            more = [t for t in left if group[(t[0], t[1]["number"])] not in done and not link(*t)]
            for t in left:
                if t not in more:
                    done.add(group[(t[0], t[1]["number"])])
            if len(more) == len(left):
                break
            left = more
        out["untapped"] += [f"{ref}.{p['number']}" for ref, p, _ in left]
        rb["segments"] += [dict(t) for t in out["tracks"] if t["net"] == net]  # the next pour net routes around them
        rb["vias"] += [dict(v) for v in out["vias"] if v["net"] == net]
    return out


def _groups(rb: dict, net: str, pads: list, copper: list) -> tuple[dict, set]:
    """The net's copper already on the board in connected groups: ({(ref, pad number): group}, the groups that hold
    a via or a through-hole pad). Pads join through the net's tracks on a shared layer and through its vias."""
    from shapely.strtree import STRtree
    items = []  # (shape, layers, ref-pad key or None, reaches another layer)
    for ref, p in pads:
        ls = set(routing.pad_layers(p["layers"], copper))
        items.append((_box(p), ls, (ref, p["number"]), p["kind"] == "thru_hole" or len(ls) > 1))
    for s in rb["segments"]:
        if s["net"] == net:
            items.append((LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2), {s["layer"]},
                          None, False))
    for v in rb["vias"]:
        if v["net"] == net:
            items.append((Point(v["x"], v["y"]).buffer(0.1), set(copper), None, True))
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    tree = STRtree([it[0] for it in items])
    for i, (g, ls, _, _) in enumerate(items):
        for j in tree.query(g, predicate="intersects"):
            if j != i and ls & items[j][1]:
                parent[find(i)] = find(int(j))
    group = {it[2]: find(i) for i, it in enumerate(items) if it[2] is not None}
    return group, {find(i) for i, it in enumerate(items) if it[3]}


def _track(net, line, layer, w) -> dict:
    (x1, y1), (x2, y2) = line.coords
    return {"net": net, "x1": round(x1, 4), "y1": round(y1, 4), "x2": round(x2, 4), "y2": round(y2, 4), "w": w,
            "layer": layer}


def preseed(design, libs, parts, src: Path, workdir: Path, stem: str) -> tuple[Path, list, list, dict]:
    """The board `src` with the taps written in (a copy in `workdir`), for the router to keep. Returns (board, tracks,
    vias, plan)."""
    p = plan(design, libs, parts, src)
    if not (p["tracks"] or p["vias"]):
        return Path(src), [], [], p
    import uuid
    from ...core.sexpr import Q
    tree = sexpr.loads(Path(src).read_text())  # the unrouted board: its taps added as copper (no thermal vias yet)
    idx = {str(n[2]): n[1] for n in sexpr.find_all(tree, "net") if len(n) > 2}
    b = design.board
    for t in p["tracks"]:
        tree.append(["segment", ["start", t["x1"], t["y1"]], ["end", t["x2"], t["y2"]], ["width", t["w"]],
                     ["layer", Q(t["layer"])], ["net", idx[t["net"]]], ["uuid", Q(str(uuid.uuid4()))]])
    for v in p["vias"]:
        tree.append(["via", ["at", v["x"], v["y"]], ["size", b["via_dia_mm"]], ["drill", b["via_drill_mm"]],
                     ["layers", Q("F.Cu"), Q("B.Cu")], ["net", idx[v["net"]]], ["uuid", Q(str(uuid.uuid4()))]])
    dst = Path(workdir) / f"{stem}taps.kicad_pcb"
    dst.write_text(sexpr.dumps(tree) + "\n")
    s = p["stats"]
    print(f"[layout] pour taps: {s['pads']} pads, {s['joined']} already joined, {s['linked']} linked on their footprint, "
          f"{s['stub']} stub vias, "
          f"{len(p['untapped'])} without a tap" + (f" ({', '.join(p['untapped'][:8])})" if p["untapped"] else ""))
    return dst, p["tracks"], p["vias"], p
