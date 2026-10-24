"""Cut capacity: a proof that no router can route a board, from a cut that more nets must cross than fit across it.

A cut is a curve across the routable area (a straight chord from edge to edge, or the boundary of a grown courtyard
clipped to the board). It splits the board into faces. A net with pads in two faces must cross the cut at least once
on some copper layer. The check compares, for each cut,

    demand  = sum over the crossing nets of (w_net + c)          a LOWER bound of the copper the crossings need
    capacity = sum over copper layers and free intervals of (len + c)   an UPPER bound of the room there is

and reports the cut when demand > capacity.

Why each crossing costs at least w + c along the cut. Copper of a net on one layer is tracks (a centre line buffered
by at least w/2) and vias (a disk of via_dia/2 >= w/2), joined at shared centre points, as every router writes them.
The skeleton (centre lines and via centres) of a net that connects pads in two faces crosses the cut at a point P,
and the disk of radius w/2 around P is copper of that net on that layer. The cut passes through P and leaves that disk
on both sides (the disk lies inside the board, since copper keeps the edge clearance), so the cut runs at least w
inside it. Two crossings of different nets keep the clearance c between their copper, so their pieces of the cut are
at least c apart along it. k crossings in one free interval of length L therefore need sum(w_i) + (k - 1) c <= L,
that is sum(w_i + c) <= L + c, and an interval shorter than the narrowest w holds none. Summing over intervals and
layers (each net counted once, on one layer) gives the bound. A net with a pad that touches the cut crosses it through
that pad: it needs no room and is not counted.

The lower-bound quantities (each chosen so the demand can only come out smaller than the truth):
  - w_net = min(net class width, board minimum track width): DRC accepts a neck to the minimum width anywhere along a
    net (routing.widen keeps necks up to NECK_MAX_MM long, wherever they are), so a crossing may be narrow.
  - c = the smallest clearance any two nets may keep: the net classes, board.clearance_mm and every clearance rule
    in board.drc_rules except rules that apply only to the items of named footprints (pads, never tracks).
  - Ground nets with board.gnd_pour are left out (a pour may connect them across), and so are nets with a pad on
    the cut, nets with a pad of unknown position (before placement) and nets with fewer than two faces.
  - One crossing per net, even when its pads span more than two faces.
The upper-bound quantities (each chosen so the capacity can only come out larger than the truth):
  - The cut inside the copper area (board inset by the DRC copper-to-edge clearance), minus only what certainly
    blocks a track: pads on that layer (their exact copper shape: rounded corners and ovals kept, custom pads by
    their anchor pad, which is smaller than the copper), non-plated holes, and rule areas that forbid both tracks and
    vias on that layer. A rule area that forbids tracks but allows vias blocks nothing (a via may cross there).
  - No clearance is taken off at the ends of an interval (to a pad or a keep-out), and the GND pour is free room.
  - Isolation (board.isolation clearance_mm): a second, separate test counts only the nets outside the group and
    also blocks the band of that width around the group's pads, which those nets may not enter. The first test
    (all nets, no band) is always made.

Which cuts. Before placement (parts=None) only fixed geometry: straight chords across the board (an x and y grid,
lines at the extents of the keep-outs and fixed parts, and the line through the nearest points of every pair of
keep-outs and fixed parts, and of each one with each board edge), and the grown courtyards of the fixed parts; the
demand counts only nets whose pads are all on fixed parts or on parts that a floorplan region holds inside one face.
After placement: the same chords and grid, the grown courtyard of every part, and of every tight cluster of parts
(the grown courtyards that touch), with all pads at that placement.

A failing cut around a single part goes back to `parts` (a package with more room, or narrower nets: circuit); a
failing chord across the board or a cluster goes back to `architecture` (board size, layers, fewer nets across that
line, or the fixed geometry moved).
"""

from __future__ import annotations

import math
import re

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box
from shapely.ops import linemerge, nearest_points, polygonize, unary_union
from shapely.strtree import STRtree

from ..board import pcb
from ..route import routing
from .placer import rot

NAME = "cut"
EDGE_MM = 0.3            # min_copper_edge_clearance that pcb.write_project gives KiCad DRC
GROW_MM = (0.5, 1.0, 2.0)  # courtyard growths of the cuts around parts and clusters
GRID_MM = 2.0            # spacing of the x and y chords
ANCHOR_MM2 = 20.0        # a fixed part this large (courtyard area) anchors the chords between obstacles
EPS = 1e-6
FP_ONLY = re.compile(r"^\s*(A\.memberOfFootprint\('[^']*'\)\s*(\|\|\s*)?)+$")


# ------------------------------------------------------------------ the bound's quantities

def min_width(design) -> float:
    """The narrowest track DRC accepts (a lower bound of every crossing's width)."""
    b = design.board
    return min(float(b.get("min_track_mm", routing.NECK_FLOOR)), routing.NECK_FLOOR, float(b["track_mm"]))


def min_clearance(design, classes: dict) -> float:
    """The smallest copper distance two nets' tracks may keep: net classes, the board clearance and every clearance
    rule except those whose condition names only footprints (they apply to pads, never to a track)."""
    c = min([float(design.board["clearance_mm"])] + [float(v["clearance"]) for v in classes.values()])
    for r in design.board.get("drc_rules") or []:
        m = re.search(r"\(constraint\s+clearance\s*\(min\s+([0-9.]+)\s*mm\)", r)
        cond = re.search(r'\(condition\s+"([^"]*)"', r)
        if m and not (cond and FP_ONLY.match(cond.group(1))):
            c = min(c, float(m.group(1)))
    return c


def _pad_geom(p: dict, x: float, y: float, r: float, small: bool):
    """Pad copper in board coordinates. small=True: never larger than the copper (custom pads by their anchor);
    small=False: never smaller (custom pads by their hull)."""
    w, h = p["w"], p["h"]
    shape = p.get("shape", "rect")
    if shape == "circle":
        g = Point(0, 0).buffer(w / 2, quad_segs=8)
    elif shape == "custom" and not small and p.get("hull"):
        # the hull leaves out round and line primitives: grow it by the anchor size so it is never smaller
        g = Polygon(p["hull"]).convex_hull.buffer(max(w, h), quad_segs=8)
    else:
        g = box(-w / 2, -h / 2, w / 2, h / 2)
        rad = {"oval": min(w, h) / 2, "roundrect": float(p.get("rratio", 0.25)) * min(w, h)}.get(shape, 0.0)
        if small and rad > EPS:
            g = g.buffer(-rad + EPS, quad_segs=8).buffer(rad - EPS, quad_segs=8)
    ang = (p.get("rot", 0.0) + r) % 360
    # placer.rot is KiCad's rotation (y down): x' = x cos + y sin, which is shapely's rotation by -ang
    px, py = rot(p["x"], p["y"], r)
    return affinity.translate(affinity.rotate(g, -ang, origin=(0, 0)), x + px, y + py)


# ------------------------------------------------------------------ what is where

class Layout:
    """Pads, holes and keep-outs of the parts with a known position, and the parts a floorplan region holds."""

    def __init__(self, design, libs, parts=None):
        from . import keepout, placer
        b = design.board
        self.design = design
        self.W, self.H = float(b["width_mm"]), float(b["height_mm"])
        self.board = box(0, 0, self.W, self.H)
        self.area = box(EDGE_MM, EDGE_MM, self.W - EDGE_MM, self.H - EDGE_MM)
        self.copper = pcb.cu_layers(int(b["layers"]))
        self.classes = pcb.net_classes(design)
        self.c = min_clearance(design, self.classes)
        self.wmin = min_width(design)
        all_parts = placer.build_parts(design, libs)
        self.post = parts is not None
        placed = parts if parts is not None else {r: p for r, p in all_parts.items() if p.fixed}
        self.placed = {r: p for r, p in placed.items() if r in design.components}
        self.fixed = {r for r, p in all_parts.items() if p.fixed}
        gnd = design.ground_nets() if b.get("gnd_pour") and set(self.copper) - set(b.get("pour_skip_layers") or []) \
            else set()
        self.nets = {n: c for n, c in design.nets().items() if n not in gnd}
        self.pads = []      # {ref, net, layers, big, small}
        self.blocked = {L: [] for L in self.copper}
        for ref, part in self.placed.items():
            comp = design.components[ref]
            for p in libs.footprint(comp.get("footprint"))["pads"]:
                layers = routing.pad_layers(p["layers"], self.copper)
                if not layers:
                    continue
                small = _pad_geom(p, part.x, part.y, part.r, True)
                if p["kind"] == "np_thru_hole":  # a hole: no net, blocks every layer
                    for L in self.copper:
                        self.blocked[L].append(small)
                    continue
                net = comp.pins.get(p["number"]) if p["number"] else None
                net = None if net is None or str(net).upper() in ("NC", "~") else net
                self.pads.append({"ref": ref, "net": net, "layers": layers, "small": small,
                                  "big": _pad_geom(p, part.x, part.y, part.r, False)})
                for L in layers:
                    self.blocked[L].append(small)
        # rule areas of the placed parts that forbid tracks and vias (a board cutout drawn on Edge.Cuts is left out:
        # its outline is a set of unordered points, and a hull of them could be larger than the slot)
        self.keepouts = []
        areas = []
        for ref, part in self.placed.items():
            tree = libs.footprint_tree(design.components[ref].get("footprint"))
            for a in keepout._rule_areas(tree, ("tracks", "vias")):
                if {"tracks", "vias"} <= set(a["forbids"]):
                    areas.append((a["name"], ref, a["layers"], a["poly"].exterior.coords, part))
        for name, ref, alayers, coords, part in areas:
            pts = [rot(x, y, part.r) for x, y in coords]
            poly = Polygon([(part.x + x, part.y + y) for x, y in pts]).buffer(0)
            a = {"name": name, "owner": ref, "poly": poly}
            layers = routing.pad_layers(alayers, self.copper)
            for L in layers:
                self.blocked[L].append(a["poly"])
            if layers:
                self.keepouts.append({"name": a["name"], "owner": a["owner"], "poly": a["poly"],
                                      "all": set(layers) == set(self.copper)})
        self.blocked = {L: unary_union(g) if g else Polygon() for L, g in self.blocked.items()}
        # isolation: a band of clearance_mm around the group's pads that the other nets may not enter
        self.iso = []
        for g in pcb.isolation_groups(design):
            if not g["members"] or g.get("clearance_mm") is None:
                continue
            mem = set(g["members"])
            band = {L: unary_union([p["small"].buffer(float(g["clearance_mm"]), quad_segs=8) for p in self.pads
                                    if p["net"] in mem and L in p["layers"]] or [Polygon()]) for L in self.copper}
            self.iso.append({"name": g["name"], "members": mem, "mm": float(g["clearance_mm"]),
                             "blocked": {L: unary_union([self.blocked[L], band[L]]) for L in self.copper}})
        self.tree = STRtree([p["big"] for p in self.pads]) if self.pads else None
        self.centres = [p["small"].centroid for p in self.pads]
        # before placement: parts a floorplan region holds (their courtyard stays inside the region polygon)
        self.held = {}
        if not self.post:
            from . import floorplan
            try:
                fp = floorplan.parse(design)
            except floorplan.FloorplanError:
                fp = None
            if fp is not None and fp.get("construction") != "stacked":
                rules, _ = floorplan.placer_regions(design, all_parts)
                for rule in rules:
                    for ref in rule["members"]:
                        if ref not in self.fixed and ref in design.components:
                            poly = rule["poly"].intersection(self.board)
                            prev = self.held.get(ref)
                            self.held[ref] = poly if prev is None else prev.intersection(poly)
        self.stacked = False
        try:
            from . import floorplan
            fp = floorplan.parse(design)
            self.stacked = bool(fp and fp.get("construction") == "stacked")
        except Exception:  # an invalid floorplan: the board outline is still the rectangle
            pass

    # -------------------------------------------------------------- one cut

    def evaluate(self, cut, every: bool = False) -> list[dict]:
        """Demand and capacity of one cut. Returns the failing tests (every test with every=True)."""
        cut = cut.intersection(self.board)
        if cut.is_empty or cut.length < EPS:
            return []
        faces = list(polygonize(unary_union([self.board.exterior, cut])))
        if len(faces) < 2:
            return []
        on_cut = set()
        if self.tree is not None:
            for i in self.tree.query(cut):
                if self.pads[i]["big"].intersects(cut):
                    on_cut.add(self.pads[i]["net"])
        face_tree = STRtree(faces)

        def face_of(pt):
            for i in face_tree.query(pt, predicate="within"):
                return int(i)
            return None

        pad_face = {}
        if self.pads:
            pi, fi = face_tree.query(self.centres, predicate="within")
            where = dict(zip(pi.tolist(), fi.tolist()))
            for k, p in enumerate(self.pads):
                if p["net"] is not None and p["net"] not in on_cut:
                    pad_face.setdefault((p["ref"], p["net"]), set()).add(where.get(k))
        held_face = {}
        for ref, poly in self.held.items():
            if poly.is_empty or poly.intersects(cut):
                continue
            f = face_of(poly.representative_point())
            if f is not None and all(faces[f].covers(g) for g in [poly]):
                held_face[ref] = f
        crossing = []
        for net, conns in self.nets.items():
            if net in on_cut:
                continue
            fs, known = set(), True
            for ref, _pin in conns:
                if ref in self.placed:
                    fs |= pad_face.get((ref, net), set())
                elif ref in held_face:
                    fs.add(held_face[ref])
                elif ref in self.design.components and not self.design.components[ref].get("virtual"):
                    known = False
                    break
            fs.discard(None)
            if known and len(fs) >= 2:
                crossing.append(net)
        if not crossing:
            return []
        width = {n: min(float(self.classes.get(n, {}).get("width", self.wmin)), self.wmin) for n in crossing}
        line = cut.intersection(self.area)
        out = []
        tests = [("all", crossing, {L: self.blocked[L] for L in self.copper})]
        for g in self.iso:
            others = [n for n in crossing if n not in g["members"]]
            if others:
                tests.append((g["name"], others, g["blocked"]))
        for name, nets, blocked in tests:
            need = sum(width[n] + self.c for n in nets)
            wlo = min(width[n] for n in nets)
            per, have = {}, 0.0
            for L in self.copper:
                free = line.difference(blocked[L]) if not blocked[L].is_empty else line
                cap = sum(s.length + self.c for s in _pieces(free) if s.length >= wlo - EPS)
                per[L] = round(cap, 2)
                have += cap
            if every or need > have + EPS:
                out.append({"test": name, "nets": sorted(nets), "need": need, "have": have, "per_layer": per,
                            "cut_mm": round(line.length, 2)})
        return out


def _pieces(g) -> list:
    if g.is_empty:
        return []
    if isinstance(g, LineString):
        return [g]
    lines = [x for x in getattr(g, "geoms", []) if isinstance(x, LineString)]
    if not lines:
        return []
    m = linemerge(MultiLineString(lines))
    return [m] if isinstance(m, LineString) else list(m.geoms)


# ------------------------------------------------------------------ the cuts

def _chord(lay: Layout, p, q):
    """The straight line through p and q, clipped to the board (None when p and q coincide)."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    n = math.hypot(dx, dy)
    if n < 1e-3:
        return None
    L = 2 * (lay.W + lay.H)
    return LineString([(p[0] - dx / n * L, p[1] - dy / n * L), (q[0] + dx / n * L, q[1] + dy / n * L)])


def chords(lay: Layout) -> list[tuple[str, object]]:
    out = []
    xs = {round(GRID_MM * k, 3) for k in range(1, int(lay.W / GRID_MM) + 1) if GRID_MM * k < lay.W}
    ys = {round(GRID_MM * k, 3) for k in range(1, int(lay.H / GRID_MM) + 1) if GRID_MM * k < lay.H}
    anchors = [(k["name"] or k["owner"], k["poly"]) for k in lay.keepouts if k["all"]]
    for ref in sorted(lay.fixed & set(lay.placed)):  # the large fixed parts (connectors, relays, cells, modules)
        x0, y0, x1, y1 = lay.placed[ref].box()
        pads = [p["small"] for p in lay.pads if p["ref"] == ref]
        if pads and (x1 - x0) * (y1 - y0) >= ANCHOR_MM2:
            anchors.append((ref, unary_union(pads).convex_hull))
    for _, g in anchors:  # the narrowest places along x and y: at the extents of each obstacle
        x0, y0, x1, y1 = g.bounds
        xs |= {round(v, 3) for v in (x0 + 0.01, (x0 + x1) / 2, x1 - 0.01) if 0 < v < lay.W}
        ys |= {round(v, 3) for v in (y0 + 0.01, (y0 + y1) / 2, y1 - 0.01) if 0 < v < lay.H}
    out += [(f"x = {x:g}", LineString([(x, 0), (x, lay.H)])) for x in sorted(xs)]
    out += [(f"y = {y:g}", LineString([(0, y), (lay.W, y)])) for y in sorted(ys)]
    edges = [("left edge", LineString([(0, 0), (0, lay.H)])), ("right edge", LineString([(lay.W, 0), (lay.W, lay.H)])),
             ("top edge", LineString([(0, 0), (lay.W, 0)])), ("bottom edge", LineString([(0, lay.H), (lay.W, lay.H)]))]
    for i, (na, ga) in enumerate(anchors):  # between two obstacles, and between an obstacle and each edge
        for nb, gb in anchors[i + 1:] + edges:
            if ga.intersects(gb):
                continue
            p, q = nearest_points(ga, gb)
            c = _chord(lay, (p.x, p.y), (q.x, q.y))
            if c is not None:
                out.append((f"{na} to {nb}", c))
    seen, uniq = set(), []
    for name, c in out:  # the same line twice (an obstacle's extent on the grid, a nearest point along an axis)
        k = tuple(sorted(tuple(round(v, 2) for v in xy) for xy in c.intersection(lay.board).coords))
        if k not in seen:
            seen.add(k)
            uniq.append((name, c))
    return uniq


def rings(lay: Layout) -> list[tuple[str, list[str], object]]:
    """(name, refs, cut): the grown courtyard of each placed part, and of each cluster of parts whose grown
    courtyards touch (after placement)."""
    out = []
    boxes = {r: box(*p.box()) for r, p in lay.placed.items() if p.sides or p.pads}
    for g in GROW_MM:
        grown = {r: b.buffer(g, join_style=2) for r, b in boxes.items()}
        for r, b in sorted(grown.items()):
            out.append((f"{r} courtyard + {g:g} mm", [r], b.exterior))
        if not lay.post:
            continue
        merged = unary_union(list(grown.values()))
        for poly in getattr(merged, "geoms", [merged]):
            refs = sorted(r for r, b in grown.items() if poly.intersects(b.representative_point()))
            if len(refs) >= 2 and len(refs) < len(grown):
                out.append((f"cluster {','.join(refs[:4])}{'...' if len(refs) > 4 else ''} + {g:g} mm", refs,
                            poly.exterior))
    return out


# ------------------------------------------------------------------ findings

def findings(design, libs, parts=None, board=None) -> list[dict]:
    """Cuts whose crossing demand exceeds their capacity (see the module docstring for the proof). `parts`: placer
    parts at a placement, or None before placement (fixed geometry only). `board` (pcb.read_board) is not needed:
    the check is about every routing, not the one on the board; when given without `parts`, its placement is used."""
    if parts is None and board is not None:
        parts = _parts_from(design, libs, board)
    lay = Layout(design, libs, parts)
    if lay.stacked:  # a panel of stacked boards: nets pass between boards through connectors, not across the panel
        return []
    worst: dict[tuple, dict] = {}
    for name, refs, cut in rings(lay):
        for t in lay.evaluate(cut):
            single = len(refs) == 1
            key = ("ring", tuple(refs), t["test"])
            if key not in worst or t["need"] - t["have"] > worst[key]["need_mm"] - worst[key]["have_mm"]:
                worst[key] = _finding(lay, "parts" if single else "architecture", refs, name, t)
    for name, cut in chords(lay):
        for t in lay.evaluate(cut):
            key = ("chord", t["test"])
            if key not in worst or t["need"] - t["have"] > worst[key]["need_mm"] - worst[key]["have_mm"]:
                worst[key] = _finding(lay, "architecture", [], name, t)
    return sorted(worst.values(), key=lambda f: f["have_mm"] - f["need_mm"])


def _finding(lay: Layout, back: str, refs: list[str], name: str, t: dict) -> dict:
    n = len(t["nets"])
    when = "at this placement" if lay.post else "with the fixed parts and keep-outs alone"
    iso = "" if t["test"] == "all" else f" outside isolation group {t['test']} (its band blocked)"
    if back == "parts":
        opts = (f"a package for {refs[0]} with more room around its pads, fewer or narrower nets on it (circuit), "
                "or more copper layers")
    else:
        opts = "a larger board, more copper layers, fewer nets across this line, or the fixed parts and keep-outs moved"
    msg = (f"cut {name} ({t['cut_mm']} mm long, {len(t['per_layer'])} layers, {when}): {n} nets{iso} must cross it "
           f"and need at least {t['need']:.2f} mm (each net {lay.wmin:g} mm track + {lay.c:g} mm clearance or wider) "
           f"where at most {t['have']:.2f} mm is free; no router can route this. Options: {opts}.")
    return {"check": NAME, "back_to": back, "refs": refs, "nets": t["nets"], "need_mm": round(t["need"], 2),
            "have_mm": round(t["have"], 2), "msg": msg, "cut": name, "per_layer": t["per_layer"]}


def margin(design, libs, parts=None, board=None, top: int = 5) -> list[dict]:
    """Calibration helper: the `top` tests with the highest demand over capacity, failing or not."""
    if parts is None and board is not None:
        parts = _parts_from(design, libs, board)
    lay = Layout(design, libs, parts)
    rows = []
    for name, refs, cut in rings(lay):
        rows += [dict(r, cut=name) for r in lay.evaluate(cut, every=True)]
    for name, cut in chords(lay):
        rows += [dict(r, cut=name) for r in lay.evaluate(cut, every=True)]
    rows.sort(key=lambda r: -r["need"] / max(r["have"], EPS))
    return rows[:top]


def _parts_from(design, libs, board):
    from . import placer
    parts = placer.build_parts(design, libs)
    for ref, fp in board["footprints"].items():
        if ref in parts:
            parts[ref].x, parts[ref].y, parts[ref].r = fp["x"], fp["y"], fp["r"]
    return parts
