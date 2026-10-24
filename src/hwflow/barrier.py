"""Layout feasibility proofs per net: can the net's copper connect its pads at all (`barrier`), and does its track
width fit through the gaps its copper must pass (`channel`)?

Both checks build, per net and per copper layer, the region the net's copper may occupy under the design rules:

    F_L(net) = board outline inset by the copper-to-edge clearance, minus the board cutouts (panel slots) grown by
               the same clearance,
             - the rule areas on L that forbid tracks (footprint keep-outs: antenna, KO_* parts, notches) and the
               `.kicad_dru` `disallow track` rules (board.drc_rules) that name the net,
             - every pad of another net on L, grown by the clearance the pair keeps (net classes; the distance of a
               board.isolation group or a `A.NetName == 'P' && B.NetName != 'P'` clearance rule between a member and
               a non-member),
             - every non-plated hole, grown by the hole clearance.

Every subtracted quantity is a LOWER bound of what the rules forbid, so F_L is an UPPER bound of the room there is:
  - clearances are the smallest any rule gives the pair (a `memberOfFootprint` clearance rule lowers the clearance
    of that part's pads; a clearance rule this module cannot read lowers it for every pair; creepage is not used,
    only the straight clearance it can never be under),
  - obstacle pads are their copper outline or less (a custom pad counts with its anchor only), only pads of fixed
    parts before placement (movable parts can go anywhere), tracks and vias of other nets never (they are router
    choices), GND pours never (a pour yields),
  - a rule (keep-out area or `.kicad_dru` rule) whose condition this module cannot read is left out.

The centre line of a track of width w lies in C_L(w) = F_L shrunk by w/2. A via (through, all layers) can go where
its disc fits in every layer: V = intersection over L of (F_L minus the areas that forbid vias) shrunk by via_dia/2.
The net's own pads always count as reached within their outline; a through-hole pad joins all its layers.

barrier: the pads must lie in one connected component of the union over layers of (C_L(w_min) + own pads + V), two
  layers joined only through V or a through-hole pad. w_min is the smallest track any rule allows (board
  min_track_mm, routing.NECK_FLOOR). If the pads fall in separate components, no router and (before placement) no
  placement can connect them: a proof, since every room bound above is an upper bound.

channel: the same at the net's class width w (budget widths included), where a track may neck down to w_min for at
  most routing.NECK_MAX_MM per continuous narrow stretch (routing.widen accepts a net when no narrow stretch is
  longer). The narrow region N_L = C_L(w_min) - (C_L(w) + own pads + V) joins two wide components A and B only when
  A.distance(B) <= NECK_MAX_MM: any path from A to B through N_L is at least that long, so the test can only join
  more than the real rule does (a lower bound of the neck length; a via inside the narrow region is counted as
  wide everywhere, also an over-count of connectivity). The largest width that still connects the pads is found by
  bisection and reported as the channel width.

Interface: findings(design, libs, parts=None, board=None) -> [{check, back_to, refs, nets, need_mm, have_mm, msg,
at, fixed}]. `fixed` is True when the finding holds with fixed copper only (it does not depend on the placement).
"""

from __future__ import annotations

import fnmatch
import math
import re

from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

from . import keepout, pcb, routing
from .core import sexpr
from .placer import rot

EDGE_MM = 0.3      # min_copper_edge_clearance written by pcb.write_project
HOLE_MM = 0.25     # min_hole_clearance written by pcb.write_project
EPS = 1e-6
QS = 8             # quad segments of the buffers (chord error 0.2 % of the radius, inside the 0.01 mm bisection step)
BISECT_MM = 0.01   # resolution of the channel width


# ------------------------------------------------------------------ geometry of the parts

def _xf(pts, x, y, r):
    """Footprint-local points to board coordinates (KiCad rotation, placer.rot)."""
    out = []
    for px, py in pts:
        dx, dy = rot(px, py, r)
        out.append((x + dx, y + dy))
    return out


def _pad_poly(p: dict, own: bool):
    """Pad copper outline in footprint coordinates. `own`: as the net's own pad (an over-estimate is safe: the custom
    pad's convex hull); else as an obstacle (an under-estimate is safe: the custom pad's anchor only, a roundrect
    with its rounded corners)."""
    w, h = p["w"], p["h"]
    if p.get("hull") and own:
        g = Polygon(p["hull"]) if len(p["hull"]) >= 3 else box(-w / 2, -h / 2, w / 2, h / 2)
    elif p["shape"] == "circle":
        g = Point(0, 0).buffer(w / 2, quad_segs=QS)
    elif p["shape"] == "oval":
        r = min(w, h) / 2
        a, b = ((w / 2 - r, 0), (-(w / 2 - r), 0)) if w >= h else ((0, h / 2 - r), (0, -(h / 2 - r)))
        g = LineString([a, b]).buffer(r, quad_segs=QS) if a != b else Point(0, 0).buffer(r, quad_segs=QS)
    elif p["shape"] == "roundrect":
        r = float(p.get("rratio", 0.25)) * min(w, h)
        g = box(-w / 2, -h / 2, w / 2, h / 2)
        if r > EPS:
            g = g.buffer(-r).buffer(r, quad_segs=QS)
    else:
        g = box(-w / 2, -h / 2, w / 2, h / 2)
    g = Polygon(_xf(g.exterior.coords, 0, 0, p.get("rot", 0.0)))
    return affinity.translate(g, p["x"], p["y"])


def _zones(tree) -> list[dict]:
    """Every rule area (keepout zone) of a footprint tree: {name, layers, forbids, poly} (local coordinates)."""
    out = []
    for z in sexpr.find_all(tree, "zone"):
        ko = sexpr.find(z, "keepout")
        if not ko:
            continue
        forbids = {k for k in ("tracks", "vias", "pads") if (sexpr.find(ko, k) or [0, 0])[1] == "not_allowed"}
        lay = sexpr.find(z, "layers") or sexpr.find(z, "layer") or []
        pts = keepout._pts(sexpr.find(z, "polygon") or [])
        if len(pts) >= 3:
            out.append({"name": str(sexpr.value(z, "name", "")), "layers": [str(x) for x in lay[1:]],
                        "forbids": forbids, "poly": pts})
    return out


def _cutout(tree):
    """Closed Edge.Cuts outline of a footprint (a panel slot) as a polygon in local coordinates, or None."""
    lines = []
    for g in tree:
        if not (isinstance(g, list) and g and str(g[0]) in ("fp_line", "fp_rect")):
            continue
        if str(sexpr.value(g, "layer")) != "Edge.Cuts":
            continue
        s, e = sexpr.find(g, "start"), sexpr.find(g, "end")
        a, b = (float(s[1]), float(s[2])), (float(e[1]), float(e[2]))
        if str(g[0]) == "fp_rect":
            return box(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
        lines.append(LineString([a, b]))
    polys = list(polygonize(lines)) if lines else []
    return unary_union(polys) if polys else None  # an outline that does not close is left out (more room)


# ------------------------------------------------------------------ design rules

def _unwrap(t: str) -> str:
    """A condition term without surrounding whitespace and enclosing parentheses."""
    t = t.strip()
    while t.startswith("(") and t.endswith(")") and t[1:-1].count("(") == t[1:-1].count(")"):
        t = t[1:-1].strip()
    return t


def _drc_rules(design):
    """Read board.drc_rules: (disallow, clearance) where disallow = [{area, nets(pred), track, via}] for rules of the
    shape `A.intersectsArea('X') [&& A.NetClass == 'C' | A.NetName == 'P' ...] (constraint disallow track/via)`, and
    clearance = [(value, refs or None)] for clearance rules: refs when the condition is only memberOfFootprint terms,
    None (every pair) otherwise. Rules of other shapes are left out (they can only forbid, never allow)."""
    dis, clr = [], []
    for r in design.board.get("drc_rules") or []:
        cond = re.search(r'\(condition\s+"([^"]*)"', r)
        cond = cond.group(1) if cond else ""
        m = re.search(r"\(constraint\s+disallow\s+([^)]*)\)", r)
        if m:
            kinds = set(m.group(1).split())
            terms = [_unwrap(t) for t in cond.split("&&")]
            area, preds, ok = None, [], "||" not in cond
            for t in terms:
                a = re.fullmatch(r"A\.(?:intersectsArea|insideArea|enclosedByArea)\('([^']+)'\)", t)
                c = re.fullmatch(r"A\.NetClass\s*==\s*'([^']+)'", t)
                n = re.fullmatch(r"A\.NetName\s*==\s*'([^']+)'", t)
                if a and area is None:
                    area = a.group(1)
                elif c:
                    preds.append(("class", c.group(1)))
                elif n:
                    preds.append(("name", n.group(1)))
                else:
                    ok = False
            if ok and area:
                dis.append({"area": area, "preds": preds, "track": bool(kinds & {"track", "tracks"}),
                            "via": bool(kinds & {"via", "vias", "through_via"})})
            continue
        m = re.search(r"\(constraint\s+clearance\s*\(min\s+([0-9.]+)\s*mm\)", r)
        if m:
            v = float(m.group(1))
            refs = re.findall(r"A\.memberOfFootprint\('([^']+)'\)", cond)
            rest = re.sub(r"A\.memberOfFootprint\('[^']+'\)|\|\||[()\s]", "", cond)
            clr.append((v, set(refs) if refs and not rest else None))
    return dis, clr


def _iso_groups(design) -> list[tuple[set, float]]:
    """(members, straight clearance) of every isolation requirement: board.isolation entries with clearance_mm, and
    board.drc_rules clearance rules shaped `A.NetName == 'P' && B.NetName != 'P'`. Creepage is left out: it can be
    met by a slot, so it is no lower bound of the straight distance."""
    nets = sorted(set(design.nets()) | set(design.raw.get("nets") or {}))
    out = [(set(g["members"]), float(g["clearance_mm"])) for g in pcb.isolation_groups(design)
           if g["members"] and g["clearance_mm"] is not None]
    pat = re.compile(r"A\.NetName\s*==\s*'([^']+)'\s*&&\s*B\.NetName\s*!=\s*'([^']+)'")
    for r in design.board.get("drc_rules") or []:
        mm = re.search(r"\(constraint\s+clearance\s*\(min\s+([0-9.]+)\s*mm\)", r)
        cond = re.search(r'\(condition\s+"([^"]*)"', r)
        m = pat.fullmatch(cond.group(1).strip()) if cond else None
        if mm and m and m.group(1) == m.group(2):
            mem = {n for n in nets if fnmatch.fnmatchcase(n, m.group(1))}
            if mem:
                out.append((mem, float(mm.group(1))))
    return out


def _net_classes_of(design, classes) -> dict[str, set]:
    """net -> names of the KiCad net classes it is in (write_project: W<w>_C<c> classes and iso_<name> classes)."""
    b = design.board
    out = {n: set() for n in classes}
    for n, v in classes.items():
        if (v["width"], v["clearance"]) == (b["track_mm"], b["clearance_mm"]):
            out[n].add("Default")
        else:
            out[n].add(f"W{v['width']:g}_C{v['clearance']:g}")
    for x in pcb.isolation_router_rules(design):
        for n in x["members"]:
            out.setdefault(n, set()).add(x["class"])
    return out


# ------------------------------------------------------------------ the board model

class Model:
    """The copper rules of one design at one placement (or before placement: fixed parts only)."""

    def __init__(self, design, libs, parts=None, board=None, use=None):
        b = design.board
        self.design = design
        self.copper = pcb.cu_layers(int(b["layers"]))
        self.classes = pcb.net_classes(design)
        self.floor = min(float(b.get("min_track_mm", routing.NECK_FLOOR)), routing.NECK_FLOOR)
        self.via_r = float(b["via_dia_mm"]) / 2
        self.base_clr = float(b["clearance_mm"])
        self.iso = _iso_groups(design)
        self.disallow, clr_rules = _drc_rules(design)
        self.clr_low = min([v for v, refs in clr_rules if refs is None] + [math.inf])
        self.clr_ref = {}
        for v, refs in clr_rules:
            for r in refs or ():
                self.clr_ref[r] = min(self.clr_ref.get(r, math.inf), v)
        self.netcls = _net_classes_of(design, self.classes)
        self.pre = parts is None and board is None
        pos = self._positions(design, parts, board)
        if use is not None:
            pos = {r: v for r, v in pos.items() if r in use}
        self.pos = pos
        self.pads, self.holes, self.areas, cuts = [], [], [], []
        for ref, (x, y, r, fixed) in pos.items():
            c = design.components[ref]
            fpid = c.get("footprint")
            fp = libs.footprint(fpid)
            pins = c.pins
            for p in fp["pads"]:
                cu = routing.pad_layers(p["layers"], self.copper)
                if p["kind"] == "np_thru_hole":
                    d = p.get("drill") or min(p["w"], p["h"])
                    hx, hy = _xf([(p["x"], p["y"])], x, y, r)[0]
                    self.holes.append(Point(hx, hy).buffer(d / 2, quad_segs=QS))
                    continue
                if not cu:
                    continue
                net = pins.get(p["number"]) if p["number"] else None
                net = None if net is None or net.upper() in ("NC", "~") else net
                own = Polygon(_xf(_pad_poly(p, True).exterior.coords, x, y, r))
                obst = Polygon(_xf(_pad_poly(p, False).exterior.coords, x, y, r))
                self.pads.append({"ref": ref, "number": p["number"], "net": net, "layers": cu, "own": own,
                                  "obst": obst, "fixed": fixed})
            tree = libs.footprint_tree(fpid)
            for z in _zones(tree):
                lay = routing.pad_layers(z["layers"], self.copper)
                self.areas.append({"name": z["name"], "owner": ref, "layers": lay, "forbids": z["forbids"],
                                   "poly": Polygon(_xf(z["poly"], x, y, r)).buffer(0)})
            cut = _cutout(tree)
            if cut is not None and not cut.is_empty:
                for g in getattr(cut, "geoms", [cut]):
                    cuts.append(Polygon(_xf(g.exterior.coords, x, y, r)))
        if design.raw.get("blocks") and parts is not None:
            from .core import blocks
            for z in _zones(["root"] + list(blocks.keepout_zones(design, parts))):  # board coordinates already
                self.areas.append({"name": z["name"], "owner": None, "forbids": z["forbids"],
                                   "layers": routing.pad_layers(z["layers"], self.copper), "poly": Polygon(z["poly"]).buffer(0)})
        from . import floorplan
        cuts += [c["poly"] for c in floorplan.cuts(design)]  # floorplan cut regions: holes and notches of the board
        W, H = float(b["width_mm"]), float(b["height_mm"])
        self.outline = box(EDGE_MM, EDGE_MM, W - EDGE_MM, H - EDGE_MM)
        if cuts:
            self.outline = self.outline.difference(unary_union(cuts).buffer(EDGE_MM, quad_segs=QS))
        self.cuts = cuts
        self._free = {}

    def _positions(self, design, parts, board) -> dict:
        """ref -> (x, y, rot, fixed) of the parts whose copper is known: before placement the fixed parts, else every
        placed part (a routed board's footprint positions win over `parts`)."""
        out = {}
        if board is not None:
            for ref, fp in board["footprints"].items():
                if ref in design.components and not design.components[ref].get("virtual"):
                    p = (parts or {}).get(ref)
                    out[ref] = (fp["x"], fp["y"], fp["r"], bool(getattr(p, "fixed", False) or self._fixed(design, ref)))
            return out
        if parts is not None:
            for ref, p in parts.items():
                if ref in design.components and not design.components[ref].get("virtual"):
                    out[ref] = (p.x, p.y, p.r, bool(p.fixed))
            return out
        for ref in design.components:
            pl = self._fixed(design, ref)
            if pl:
                out[ref] = (float(pl["x"]), float(pl["y"]), float(pl.get("rot", 0)), True)
        return out

    @staticmethod
    def _fixed(design, ref):
        c = design.components[ref]
        if c.get("virtual"):
            return None
        pl = c.get("placement") or {}
        if design.raw.get("blocks"):
            from .core import blocks
            pl = blocks.anchor(design, ref) or pl
        if "x" in pl and "y" in pl and pl.get("fixed", True):
            return pl
        return None

    # -------------------------------------------------- rules per net

    def width(self, net) -> float:
        return float(self.classes.get(net, {}).get("width", self.design.board["track_mm"]))

    @staticmethod
    def neck_len(a, b, k) -> float:
        """A lower bound of the length of a narrow stretch from wide area `a` to wide area `b` through the narrow
        component `k`: the distance of a and b."""
        return a.distance(b)

    def net_copper(self, net) -> list[str]:
        """The copper layers `net` may use (every layer here; corridor.Model narrows it to nets.<name>.layers)."""
        return self.copper

    def clearance(self, net, other, ref=None, iso=True) -> float:
        """Smallest clearance the rules allow between `net` copper and a pad of `other` (None: no net) of part `ref`."""
        a = self.classes.get(net, {}).get("clearance", self.base_clr)
        c = max(a, self.classes.get(other, {}).get("clearance", self.base_clr) if other else self.base_clr)
        if iso:
            for mem, mm in self.iso:
                if (net in mem) != (other in mem):
                    c = max(c, mm)
        c = min(c, self.clr_low, self.clr_ref.get(ref, math.inf))
        return c

    def _rule_hits(self, net, preds) -> bool:
        for kind, v in preds:
            if kind == "class" and v not in self.netcls.get(net, set()):
                return False
            if kind == "name" and not fnmatch.fnmatchcase(net, v):
                return False
        return True

    def keepouts(self, net, layer, what="tracks", skip=()):
        """Areas on `layer` that forbid `what` (tracks or vias) for `net`: rule areas and .kicad_dru disallow rules."""
        out = []
        for a in self.areas:
            if a["owner"] in skip:
                continue
            if layer in a["layers"] and what in a["forbids"]:
                out.append(a["poly"])
        for d in self.disallow:
            if not d["track" if what == "tracks" else "via"] or not self._rule_hits(net, d["preds"]):
                continue
            for a in self.areas:
                if a["name"] == d["area"] and a["owner"] not in skip and (layer in a["layers"] or what == "vias"):
                    out.append(a["poly"])  # a via spans every layer: it intersects the area on the area's layers
        return out

    def free(self, net, layer, what="tracks", drop=frozenset()):
        """F_L: where `net` copper may lie on `layer`. `drop` leaves out a cause (for the message): 'keepout', 'iso',
        'copper', ('owner', ref)."""
        key = (net, layer, what, drop)
        if key in self._free:
            return self._free[key]
        skip = {d[1] for d in drop if isinstance(d, tuple)}
        cut = [] if "keepout" in drop else self.keepouts(net, layer, what, skip)
        if "copper" not in drop:
            for p in self.pads:
                if p["net"] == net or layer not in p["layers"]:
                    continue
                cut.append(p["obst"].buffer(self.clearance(net, p["net"], p["ref"], "iso" not in drop), quad_segs=QS))
            cut += [h.buffer(HOLE_MM, quad_segs=QS) for h in self.holes]
        g = self.outline.difference(unary_union(cut)) if cut else self.outline
        self._free[key] = g
        return g

    def vias(self, net, drop=frozenset()):
        """V: centres where a through via of `net` fits on every layer."""
        g = None
        for L in self.net_copper(net):
            f = self.free(net, L, "tracks", drop)
            ko = [] if "keepout" in drop else self.keepouts(net, L, "vias", {d[1] for d in drop if isinstance(d, tuple)})
            if ko:
                f = f.difference(unary_union(ko))
            g = f if g is None else g.intersection(f)
        return g.buffer(-self.via_r, quad_segs=QS) if g is not None else Polygon()

    # -------------------------------------------------- connectivity

    def groups(self, net, nodes, w, necks=False, drop=frozenset()) -> list[list[int]]:
        """Partition of `nodes` (own pads: {layers, own}) into the sets the net's copper of width `w` can connect.
        `necks`: a track may narrow to self.floor for at most routing.NECK_MAX_MM per stretch."""
        V = self.vias(net, drop)
        par = {}

        def find(a):
            while par.setdefault(a, a) != a:
                par[a] = par[par[a]]
                a = par[a]
            return a

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                par[ra] = rb

        comps = {}
        layers = self.net_copper(net)
        for L in layers:
            F = self.free(net, L, "tracks", drop)
            Cw = F.buffer(-w / 2, quad_segs=QS)
            own = [n["own"] for n in nodes if L in n["layers"]]
            Wg = unary_union([Cw, V] + own)
            cs = [g for g in getattr(Wg, "geoms", [Wg]) if not g.is_empty]
            comps[L] = cs
            if necks and w > self.floor + EPS and len(cs) > 1:
                tree = STRtree(cs)
                Cf = F.buffer(-self.floor / 2, quad_segs=QS)
                N = Cf.difference(Wg)
                for K in getattr(N, "geoms", [N]):
                    if K.is_empty:
                        continue
                    Kb = K.buffer(1e-4)
                    adj = [int(i) for i in tree.query(Kb) if cs[int(i)].intersects(Kb)]
                    for i in range(len(adj)):
                        for j in range(i + 1, len(adj)):
                            a, b = adj[i], adj[j]
                            if find((L, a)) != find((L, b)) and self.neck_len(cs[a], cs[b], Kb) <= routing.NECK_MAX_MM:
                                union((L, a), (L, b))

        def comp_of(L, g):
            for i, c in enumerate(comps.get(L, ())):  # a layer the net may not use reaches nothing
                if c.intersects(g):
                    return (L, i)
            return None

        for k, n in enumerate(nodes):
            for L in n["layers"]:
                c = comp_of(L, n["own"].representative_point() if not n.get("region") else n["own"])
                if c is not None:
                    union(("n", k), c)
            find(("n", k))
        for v in getattr(V, "geoms", [V]):
            if v.is_empty:
                continue
            pt = v.representative_point()
            cs = [comp_of(L, pt) for L in layers]
            cs = [c for c in cs if c is not None]
            for c in cs[1:]:
                union(cs[0], c)
        out = {}
        for k in range(len(nodes)):
            out.setdefault(find(("n", k)), []).append(k)
        return sorted(out.values(), key=lambda g: (-len(g), g))

    def gap(self, net, nodes, groups, w, drop=frozenset()):
        """Where two groups come closest at width w: (x, y) midway between their components, for the message."""
        best = None
        for L in self.net_copper(net):
            F = self.free(net, L, "tracks", drop)
            Wg = unary_union([F.buffer(-w / 2, quad_segs=QS)] + [n["own"] for n in nodes if L in n["layers"]])
            cs = [g for g in getattr(Wg, "geoms", [Wg])]
            side = []
            for grp in groups[:2]:
                gg = [n["own"] for k, n in enumerate(nodes) if k in grp and L in n["layers"]]
                side.append(unary_union([c for c in cs if any(c.intersects(x) for x in gg)]) if gg else None)
            if side[0] is None or side[1] is None or side[0].is_empty or side[1].is_empty:
                continue
            d = side[0].distance(side[1])
            if best is None or d < best[0]:
                from shapely.ops import nearest_points
                a, b = nearest_points(side[0], side[1])
                best = (d, ((a.x + b.x) / 2, (a.y + b.y) / 2), L)
        return best


# ------------------------------------------------------------------ the checks

def _nodes(model: Model, design, net: str, regions: dict) -> list[dict]:
    """Own pads of `net` with known position, plus (before placement) one node per floorplan region a pad of the net
    must lie in (the whole region counts as reached: an over-estimate, so a finding still holds)."""
    nodes = [dict(p, region=False) for p in model.pads if p["net"] == net]
    own = {(r, p) for r, p in design.nets().get(net, [])} if regions else set()  # once, not per region pad
    for (ref, pin), poly in regions.items():
        if ref in model.pos:
            continue
        if (ref, pin) in own:
            nodes.append({"ref": ref, "number": pin, "net": net, "layers": list(model.copper),
                          "own": poly.intersection(model.outline), "obst": None, "fixed": False, "region": True})
    return [n for n in nodes if not n["own"].is_empty]


def _forced(design, libs) -> dict:
    """(ref, pin) -> region polygon for the pads a floorplan region forces before placement (members of a region and
    pads of a region's nets)."""
    from . import floorplan, placer
    try:
        parts = placer.build_parts(design, libs)
        rules, _ = floorplan.placer_regions(design, parts)
    except Exception:  # noqa: BLE001 - no floorplan, or one that does not parse: nothing forced
        return {}
    out = {}
    for r in rules:
        if r["name"].startswith("board "):
            continue
        for ref in r["members"]:
            for pin in parts[ref].pads if ref in parts else ():
                out[(ref, pin)] = out[(ref, pin)].intersection(r["poly"]) if (ref, pin) in out else r["poly"]
        for ref, pins in r["pads"].items():
            for pin in pins:
                out[(ref, pin)] = out[(ref, pin)].intersection(r["poly"]) if (ref, pin) in out else r["poly"]
    return out


def _label(nodes, grp) -> str:
    xs = [f"{nodes[k]['ref']}.{nodes[k]['number']}" + (" (region)" if nodes[k].get("region") else "") for k in grp]
    return ", ".join(sorted(set(xs))[:4]) + (" ..." if len(set(xs)) > 4 else "")


def _cause(model: Model, net, nodes, w, necks) -> tuple[str, str, list[str]]:
    """Which geometry separates the pads: (text, back_to, refs). Each kind of obstacle is left out in turn; the one
    whose removal connects the pads is named."""
    off = sorted({f"{n['ref']}.{n['number']}" for n in nodes if not n["own"].intersects(model.outline)})
    if off:
        return f"the board edge (pads {', '.join(off)} lie outside the copper area)", "architecture", []
    tests = [("iso", "the isolation distance to the other side of the isolation group"),
             ("keepout", "the keep-out rule areas"), ("copper", "the pads of other nets")]
    found = []
    for d, text in tests:
        if len(model.groups(net, nodes, w, necks, frozenset({d}))) == 1:
            found.append((d, text))
    owners = sorted({a["owner"] for a in model.areas if a["owner"]})
    own_refs = {n["ref"] for n in nodes}
    who = []
    if any(d == "keepout" for d, _ in found) or not found:
        for o in owners:
            if len(model.groups(net, nodes, w, necks, frozenset({("owner", o)}))) == 1:
                who.append(o)
    if who:
        back = "parts" if set(who) <= own_refs else "architecture"  # a part's own keep-out cuts its own pad off
        return f"the keep-out areas of {', '.join(who)}", back, who
    if found:
        return " or ".join(t for _, t in found), "architecture", []
    return "several obstacles together (no single kind removes it)", "architecture", []


def findings(design, libs, parts=None, board=None) -> list[dict]:
    """Barrier and channel findings for every net with at least two pads of known position (see the module text)."""
    return with_budgets(design, parts, lambda: _findings(design, libs, parts, board))


def with_budgets(design, parts, fn):
    """fn() with the budget widths of this placement set on `design` (the layout stage sets them the same way), for
    this check only."""
    saved = getattr(design, "budget_widths", None)
    if parts is not None and not saved and design.raw.get("budgets"):
        try:
            from . import budgets
            design.budget_widths = budgets.widths(design, parts)[0]
        except Exception:  # noqa: BLE001 - a budget that cannot be estimated adds no width
            pass
    try:
        return fn()
    finally:
        design.budget_widths = saved if saved is not None else {}


def _findings(design, libs, parts, board) -> list[dict]:
    model = Model(design, libs, parts, board)
    forced = _forced(design, libs) if model.pre else {}
    out = []
    for net in sorted(design.nets()):
        nodes = _nodes(model, design, net, forced)
        if len(nodes) < 2:
            continue
        g = model.groups(net, nodes, model.floor)
        if len(g) > 1:
            out.append(_barrier(model, net, nodes, g))
            continue
        w = model.width(net)
        if w <= model.floor + EPS:
            continue
        g = model.groups(net, nodes, w, necks=True)
        if len(g) > 1:
            out.append(_channel(model, net, nodes, g, w))
    if not model.pre and out:  # does a finding hold with fixed copper only (the placement of movable parts aside)?
        fm = Model(design, libs, parts, board, use={r for r, v in model.pos.items() if v[3]})
        base = fm.pads
        for f in out:
            net = f["nets"][0]
            nodes = _nodes(model, design, net, {})
            fm.pads, fm._free = [p for p in base if p["net"] != net] + nodes, {}
            w = model.floor if f["check"] == "barrier" else f["width_mm"]
            f["fixed"] = len(fm.groups(net, nodes, w, necks=f["check"] == "channel")) > 1
    return out


def _barrier(model, net, nodes, g) -> dict:
    cause, back, who = _cause(model, net, nodes, model.floor, False)
    gap = model.gap(net, nodes, g, model.floor)
    at = [round(gap[1][0], 2), round(gap[1][1], 2)] if gap else None
    refs = sorted({nodes[k]["ref"] for grp in g for k in grp} | set(who))
    a, b = _label(nodes, g[0]), _label(nodes, g[1])
    where = f" near ({at[0]}, {at[1]})" if at else ""
    msg = (f"{net}: pads {a} and {b} are cut apart by {cause}{where}: no track of the {model.floor} mm minimum "
           f"connects them on any layer or through any via site. Options: move "
           f"{'the part or change its footprint' if back == 'parts' else 'the keep-out, region, connector or fixed part'}"
           f" so one copper path stays open, or another footprint for "
           f"{', '.join(sorted({nodes[k]['ref'] for k in g[1]}))}")
    return {"check": "barrier", "back_to": back, "refs": refs, "nets": [net], "need_mm": model.floor,
            "have_mm": 0.0, "msg": msg, "at": at, "fixed": model.pre, "width_mm": model.floor}


def _channel(model, net, nodes, g, w) -> dict:
    lo, hi = model.floor, w  # connected at lo (the barrier test passed), not at hi
    while hi - lo > BISECT_MM:
        mid = (lo + hi) / 2
        if len(model.groups(net, nodes, mid, necks=True)) == 1:
            lo = mid
        else:
            hi = mid
    clr = model.classes.get(net, {}).get("clearance", model.base_clr)
    gap = model.gap(net, nodes, g, w)
    at = [round(gap[1][0], 2), round(gap[1][1], 2)] if gap else None
    refs = sorted({nodes[k]["ref"] for grp in g for k in grp})
    where = f" at ({at[0]}, {at[1]}) on {gap[2]}" if gap else ""
    msg = (f"{net} ({w:g} mm class width): pads {_label(nodes, g[0])} and {_label(nodes, g[1])} connect only through "
           f"a channel{where} where a track of at most {lo:.2f} mm fits (need {w + 2 * clr:.2f} mm with clearance, "
           f"have {lo + 2 * clr:.2f} mm) for longer than the {routing.NECK_MAX_MM:g} mm neck. Options: a narrower "
           f"net ({lo:.2f} mm: lower current or a looser copper budget), or move the geometry that makes the "
           f"channel (fixed parts, keep-outs, board size)")
    return {"check": "channel", "back_to": "circuit", "refs": refs, "nets": [net], "need_mm": round(w + 2 * clr, 3),
            "have_mm": round(lo + 2 * clr, 3), "msg": msg, "at": at, "fixed": model.pre, "width_mm": w,
            "channel_mm": round(lo, 3)}
