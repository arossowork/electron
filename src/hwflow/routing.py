"""What hwflow adds around each KiCadRoutingTools pass (pcb.krt_route), using only the router's documented inputs:

- Isolation keep-outs. The router keeps creepage track to track only (pcb.isolation_router_rules). Before a pass,
  hwflow writes named rule areas into the pass input: the copper of the other side of every `board.isolation`
  group (pads, tracks, vias), grown by the group distance, with a `disallow track via` rule bound to the nets of
  the pass. The router reads such rules (py_router/net_keepouts.py). The areas and rules exist only in the pass
  input and its rules file. The built board and the DRC rules never contain them.
- Conductive keep-outs. The nets of a floorplan region with `isolation_mm` and `nets` (mains) stay that distance from
  every `conductive: true` keep-out or cut (a screw is accessible metal): a rule area per keep-out, grown by the
  distance, with a `disallow track via` rule bound to those nets (conductive_keepouts), in the pass input only.
- Neck-down widening. A wide net that the router could not route at its width is routed at the board track width,
  then `widen` puts its class width back on every piece of track where the clearances allow it. The narrow
  copper is left only where a pinch (a fine-pitch pad, a gap between parts) needs it.
"""

from __future__ import annotations

import math
import uuid
from pathlib import Path

from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from . import sexpr
from .sexpr import Q

ISO_ZONE = "hwflow_iso"  # name prefix of the rule areas in a pass input
# The router keeps track and via copper `clearance` away from a rule-area edge, so an area grown by
# (distance - clearance) gives the full distance. The extra 0.1 mm covers the router's 0.1 mm grid and the polygon
# chords of the grown pad outlines (8 segments per quarter circle: 6 mm x (1 - cos(pi/32)) = 0.03 mm).
ISO_SLACK = 0.1
PIECE_MM = 0.5  # widening tests a track that does not fit as a whole in pieces of at most this length
NECK_FLOOR = 0.15  # second neck width: the min_track_width pcb.write_project gives KiCad DRC
NECK_MAX_MM = 3.0  # longest continuous narrow stretch (a pad escape) after widening; a longer one leaves the net open


def pad_layers(tokens: list[str], copper: list[str]) -> list[str]:
    toks = set(tokens)
    return [L for L in copper if L in toks or "*.Cu" in toks or (L in ("F.Cu", "B.Cu") and "F&B.Cu" in toks)]


def copper_shapes(board: dict, copper: list[str], via_dia: float) -> list[tuple]:
    """(net, layer, geometry) of every pad, track and via of a pcb.read_board result."""
    out = []
    for fp in board["footprints"].values():
        for p in fp["pads"]:
            if p["kind"] == "np_thru_hole":
                continue
            if p["shape"] == "circle":
                g = Point(p["x"], p["y"]).buffer(p["w"] / 2, quad_segs=8)
            else:
                g = box(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2)
            out += [(p["net"], L, g) for L in pad_layers(p["layers"], copper)]
    for s in board["segments"]:
        line = LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])])
        out.append((s["net"], s["layer"], line.buffer(s["w"] / 2, quad_segs=8)))
    for v in board["vias"]:
        g = Point(v["x"], v["y"]).buffer(via_dia / 2, quad_segs=8)
        out += [(v["net"], L, g) for L in copper]
    return out


def _zone(name: str, layer: str, ring) -> list:
    pts = ["pts"] + [["xy", round(x, 4), round(y, 4)] for x, y in list(ring.coords)[:-1]]
    allowed = [[k, "allowed"] for k in ("tracks", "vias", "pads", "copperpour", "footprints")]
    return ["zone", ["net", 0], ["net_name", Q("")], ["layers", Q(layer)], ["uuid", Q(str(uuid.uuid4()))],
            ["name", Q(name)], ["hatch", "edge", 0.5], ["connect_pads", ["clearance", 0]], ["min_thickness", 0.25],
            ["filled_areas_thickness", "no"], ["keepout"] + allowed,
            ["fill", ["thermal_gap", 0.5], ["thermal_bridge_width", 0.5]], ["polygon", pts]]


def isolation_keepouts(groups: list[dict], board: dict, copper: list[str], via_dia: float, clearance: float,
                       nets: set[str]) -> tuple[list, list[str]]:
    """Rule areas and `.kicad_dru` rules for one pass that routes `nets`. `groups` is pcb.isolation_needs:
    [{members, mm}]. A pass with members of a group gets the copper of every other net as an area its members may
    not enter; a pass with other nets gets the members' copper as an area those nets may not enter. Holes in a
    grown outline are filled (a closed ring of the other side's copper leaves no way in anyway)."""
    shapes = copper_shapes(board, copper, via_dia) if groups else []
    zones, rules = [], []
    for gi, g in enumerate(groups):
        M = set(g["members"])
        grow = max(g["mm"] - clearance, 0.0) + ISO_SLACK
        for side, bound, src in (("out", sorted(nets & M), lambda n: n not in M),
                                 ("in", sorted(nets - M), lambda n: n in M)):
            if not bound:
                continue
            name = f"{ISO_ZONE}{gi}_{side}"
            for L in copper:
                geo = [s for n, lay, s in shapes if lay == L and src(n)]
                if not geo:
                    continue
                area = unary_union(geo).buffer(grow, quad_segs=8)
                for poly in getattr(area, "geoms", [area]):
                    zones.append(_zone(name, L, poly.exterior))
            if side == "out":
                who = " || ".join(f"A.NetName == '{n}'" for n in bound)
                cond = f"A.intersectsArea('{name}') && ({who})"
            else:
                cond = f"A.intersectsArea('{name}') && " + " && ".join(f"A.NetName != '{n}'" for n in sorted(M))
            if any(z[5][1] == name for z in zones):
                rules.append(f'(rule "{name}" (constraint disallow track via) (condition "{cond}"))')
    return zones, rules


def conductive_keepouts(needs: list[dict], copper: list[str], clearance: float, nets: set[str]) -> tuple[list, list[str]]:
    """Rule areas and `.kicad_dru` rules that keep the nets of a region with isolation_mm out of every conductive
    keep-out or cut (floorplan.conductive_needs) grown by that distance, on every copper layer, for a pass that routes
    `nets`. The router keeps `clearance` from the area edge, so the area is grown by mm - clearance + ISO_SLACK."""
    zones, rules = [], []
    for i, nd in enumerate(needs):
        bound = sorted(nets & set(nd["nets"]))
        if not bound:
            continue
        name = f"{ISO_ZONE}c{i}"
        area = nd["poly"].buffer(max(nd["mm"] - clearance, 0.0) + ISO_SLACK, quad_segs=8)
        for L in copper:
            for poly in getattr(area, "geoms", [area]):
                zones.append(_zone(name, L, poly.exterior))
        who = " || ".join(f"A.NetName == '{n}'" for n in bound)
        rules.append(f'(rule "{name}" (constraint disallow track via) (condition "A.intersectsArea(\'{name}\') && ({who})"))')
    return zones, rules


def pass_input(src: Path, dst: Path, zones: list) -> Path:
    """`src` without the isolation areas of an earlier pass, plus `zones`, written to `dst`."""
    t = sexpr.loads(src.read_text())
    t = [x for x in t if not (isinstance(x, list) and x and x[0] == "zone"
                              and str(sexpr.value(x, "name", "")).startswith(ISO_ZONE))]
    dst.write_text(sexpr.dumps(t + zones) + "\n")
    return dst


# ------------------------------------------------------------------ neck-down widening

def _segment_net(node, nets: dict) -> str | None:
    n = sexpr.find(node, "net")
    if n is None or len(n) < 2:
        return None
    if len(n) > 2:
        return str(n[2])
    v = n[1]
    return nets.get(int(v)) if str(v).isdigit() and not isinstance(v, Q) else str(v)


def longest_neck(lines: list[tuple]) -> float:
    """Length of the longest connected stretch of `lines` [((x1, y1), (x2, y2), layer)], in mm. Pieces that share an
    end on the same layer are one stretch (a via between layers ends it)."""
    out = 0.0
    for L in {ln[2] for ln in lines}:
        segs = [LineString([a, b]) for a, b, lay in lines if lay == L and a != b]
        if not segs:
            continue
        blobs = unary_union([g.buffer(1e-3) for g in segs])
        for blob in getattr(blobs, "geoms", [blobs]):
            out = max(out, sum(g.length for g in segs if blob.contains(g.centroid)))
    return out


def _pieces(x1, y1, x2, y2) -> list[tuple]:
    n = max(1, math.ceil(math.hypot(x2 - x1, y2 - y1) / PIECE_MM))
    pts = [(x1 + (x2 - x1) * k / n, y1 + (y2 - y1) * k / n) for k in range(n + 1)]
    return list(zip(pts, pts[1:]))


def widen(path: Path, board: dict, widths: dict[str, float], clearance, copper: list[str], via_dia: float,
          outline, keepouts: dict[str, list]) -> dict[str, tuple[float, float]]:
    """Fit every track of the nets of `widths`, in place in the board file `path`: each piece gets the widest of
    (the net's width, its own width, NECK_FLOOR) that keeps its clearances, so narrow copper is widened where it
    fits and copper the router shipped too wide at a pinch is narrowed. `clearance(a, b)` is the copper distance
    two nets keep; `outline` the area copper may use (board inset by the edge clearance); `keepouts` layer ->
    geometries no track may enter. A segment that does not fit as a whole is split into pieces of at most
    PIECE_MM. Returns net -> (length narrower than the net's width, total length, longest narrow stretch) in mm."""
    shapes = copper_shapes(board, copper, via_dia)
    t = sexpr.loads(path.read_text())
    nets = {int(n[1]): str(n[2]) for n in sexpr.find_all(t, "net") if len(n) > 2}
    out, stats, necks = [], {}, {}
    for node in t:
        if not (isinstance(node, list) and node and node[0] == "segment"):
            out.append(node)
            continue
        net = _segment_net(node, nets)
        w = widths.get(net)
        if w is None:
            out.append(node)
            continue
        cur = float(sexpr.value(node, "width"))
        layer = str(sexpr.value(node, "layer"))
        st, en = sexpr.find(node, "start"), sexpr.find(node, "end")
        x1, y1, x2, y2 = float(st[1]), float(st[2]), float(en[1]), float(en[2])
        obst = [g.buffer(clearance(net, n), quad_segs=4) for n, lay, g in shapes if lay == layer and n != net]
        obst = unary_union(obst + list(keepouts.get(layer, [])))

        def fits(a, b, width):
            g = LineString([a, b]).buffer(width / 2, quad_segs=8)
            return outline.contains(g) and not g.intersects(obst)

        cands = sorted({w, cur} | {f for f in (NECK_FLOOR,) if f < cur}, reverse=True)
        whole = next((c for c in cands if c >= w and fits((x1, y1), (x2, y2), c)), None)
        if whole is not None:
            runs = [[(x1, y1), (x2, y2), whole]]
        else:
            runs = []  # neighbouring pieces with the same width merge
            for a, b in _pieces(x1, y1, x2, y2):
                c = next((c for c in cands if fits(a, b, c)), cands[-1])  # nothing fits: the least copper, DRC reports it
                if runs and runs[-1][2] == c:
                    runs[-1][1] = b
                else:
                    runs.append([a, b, c])
        if len(runs) == 1 and runs[0][2] == cur:
            out.append(node)
        else:
            for a, b, c in runs:
                seg = [x for x in node if not (isinstance(x, list) and x and x[0] in ("start", "end", "width", "uuid"))]
                seg += [["start", round(a[0], 4), round(a[1], 4)], ["end", round(b[0], 4), round(b[1], 4)],
                        ["width", c], ["uuid", Q(str(uuid.uuid4()))]]
                out.append(seg)
        narrow, total = stats.get(net, (0.0, 0.0))
        for a, b, c in runs:
            shapes.append((net, layer, LineString([a, b]).buffer(c / 2, quad_segs=8)))  # other nets keep off it
            n = math.hypot(b[0] - a[0], b[1] - a[1])
            total += n
            if c < w - 1e-6:
                narrow += n
                necks.setdefault(net, []).append((tuple(a), tuple(b), layer))
        stats[net] = (narrow, total)
    path.write_text(sexpr.dumps(out) + "\n")
    return {n: (a, b, longest_neck(necks.get(n, []))) for n, (a, b) in stats.items()}
