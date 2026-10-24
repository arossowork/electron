"""Simulated-annealing placement with net length, decoupling, grouping and edge constraints."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

from .design import Design


def rot(x: float, y: float, deg: float) -> tuple[float, float]:
    """KiCad rotation (counter-clockwise on screen, y axis down)."""
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c


@dataclass
class Part:
    ref: str
    w: float          # courtyard size at rot 0 (with margin)
    h: float
    cx: float         # courtyard center offset from footprint origin at rot 0
    cy: float
    pads: dict        # pad number -> (x, y) at rot 0
    x: float = 0.0
    y: float = 0.0
    r: float = 0.0
    fixed: bool = False
    edge: str | None = None
    group: str | None = None
    decouples: str | None = None
    pad_size: dict | None = None  # pad number -> (w, h) at rot 0, for the copper-to-edge check
    sides: dict | None = None     # "front"/"back" -> (w, h, cx, cy) courtyard per board side
    tht: frozenset = frozenset()  # numbers of the plated through-hole pads (KiCad: not inside another courtyard)
    nocu: frozenset = frozenset()  # numbers of the pads without copper (a keep-out marker's dummy pad)
    cu: dict | None = None        # pad number -> its copper layers (frozenset, "*" = every layer); None: every layer
    round: frozenset = frozenset()  # numbers of the circular pads (the corridor term measures them as circles)

    def side_box(self, side: str):
        if not self.sides or side not in self.sides:
            return None
        w, h, cx, cy = self.sides[side]
        ox, oy = rot(cx, cy, self.r)
        if self.r % 180:
            w, h = h, w
        x, y = self.x + ox, self.y + oy
        return x - w / 2, y - h / 2, x + w / 2, y + h / 2

    def box(self):
        cx, cy = rot(self.cx, self.cy, self.r)
        w, h = (self.w, self.h) if self.r % 180 == 0 else (self.h, self.w)
        x, y = self.x + cx, self.y + cy
        return x - w / 2, y - h / 2, x + w / 2, y + h / 2

    def pad(self, n):
        px, py = self.pads[n]
        dx, dy = rot(px, py, self.r)
        return self.x + dx, self.y + dy


class Parts(dict):
    """ref -> Part, with the rule areas of the design's keep-out footprints (`areas`, see keepout.placement_areas)
    and the layout blocks that move as one unit (`blocks`, see blocks.units)."""
    areas: list = ()
    blocks: list = ()
    libs = None


@dataclass
class Rigid:
    """Parts that move as one unit (a layout block): `members` are (ref, dx, dy, drot) from the unit origin, `yard` is
    the virtual part that holds the unit courtyard (ref `self.ref`, not a component). x, y, r place the origin."""
    name: str
    ref: str
    members: list
    yard: Part
    x: float = 0.0
    y: float = 0.0
    r: float = 0.0
    fixed: bool = False

    def refs(self) -> list[str]:
        return [m[0] for m in self.members] + [self.ref]

    def sync(self, parts):
        """Move the members and the courtyard to the unit origin."""
        for ref, dx, dy, dr in self.members:
            ox, oy = rot(dx, dy, self.r)
            p = parts[ref]
            p.x, p.y, p.r = self.x + ox, self.y + oy, (dr + self.r) % 360
        self.yard.x, self.yard.y, self.yard.r = self.x, self.y, self.r

    def pull(self, parts):
        """The unit origin from its first member (a saved placement), then every member from the origin."""
        ref, dx, dy, dr = self.members[0]
        p = parts[ref]
        self.r = (p.r - dr) % 360
        ox, oy = rot(dx, dy, self.r)
        self.x, self.y = p.x - ox, p.y - oy
        self.sync(parts)


COURTYARD_MARGIN = 0.25  # mm around each courtyard in the placer's boxes


def build_parts(design: Design, libs, margin: float = COURTYARD_MARGIN) -> dict[str, Part]:
    parts = Parts()
    from . import keepout
    parts.areas = keepout.placement_areas(design, libs)
    for c in design.components.values():
        if c.get("virtual"):
            continue
        fp = libs.footprint(c.get("footprint"))
        x0, y0, x1, y1 = fp["bbox"]
        pads, sizes, cu, rnd = {}, {}, {}, set()
        for p in fp["pads"]:
            if p["number"] and p["number"] not in pads:
                pads[p["number"]] = (p["x"], p["y"])
                w, h = (p["w"], p["h"]) if p["rot"] % 180 == 0 else (p["h"], p["w"])
                sizes[p["number"]] = (w, h)
                lays = [l for l in p["layers"] if l.endswith(".Cu")]
                cu[p["number"]] = frozenset({"*"} if p["kind"] == "thru_hole" or "*.Cu" in lays else lays)
                if p.get("shape") == "circle":
                    rnd.add(p["number"])
        pl = c.get("placement") or {}
        sides = {k: (b[2] - b[0] + 2 * margin, b[3] - b[1] + 2 * margin, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
                 for k, b in fp.get("sides", {}).items()}
        if not any(l.endswith(".Cu") for p in fp["pads"] for l in p["layers"]):
            sides = {}  # nothing to place: a keep-out or cutout marker has no copper and no body
        part = Part(c.ref, x1 - x0 + 2 * margin, y1 - y0 + 2 * margin, (x0 + x1) / 2, (y0 + y1) / 2, pads,
                    edge=pl.get("edge"), group=c.get("group"), decouples=c.get("decouples"), pad_size=sizes,
                    sides=sides, tht=frozenset(p["number"] for p in fp["pads"] if p["number"] and p["kind"] == "thru_hole"),
                    nocu=frozenset(p["number"] for p in fp["pads"]
                                   if p["number"] and not any(l.endswith(".Cu") for l in p["layers"])),
                    cu=cu, round=frozenset(rnd))
        if "x" in pl and "y" in pl:
            part.x, part.y, part.r = float(pl["x"]), float(pl["y"]), float(pl.get("rot", 0))
            part.fixed = bool(pl.get("fixed", True))
        elif "rot" in pl:
            part.r = float(pl["rot"])
        parts[c.ref] = part
    from . import blocks
    parts.blocks = blocks.units(design, libs, parts, margin)
    parts.libs = libs  # the corridor proof of corridor_blocks reads the footprints again
    return parts


def _side_overlap(pa: "Part", pb: "Part", shrink: float = 0.0) -> float:
    """Courtyard overlap on the same board side only (a back-side holder may sit under top parts), plus the area of
    the through-hole pads of one part inside a courtyard of the other on either side (KiCad pth_inside_courtyard).
    `shrink` takes that much off each courtyard side (COURTYARD_MARGIN gives the footprint's own courtyard)."""
    def yard(p, side):
        b = p.side_box(side)
        if b is None or not shrink:
            return b
        return (b[0] + shrink, b[1] + shrink, b[2] - shrink, b[3] - shrink)
    total = 0.0
    for side in ("front", "back"):
        ba, bb = yard(pa, side), yard(pb, side)
        if ba and bb:
            total += _overlap(ba, bb)
    for a, b in ((pa, pb), (pb, pa)):
        if a.tht and b.sides:
            for n in a.tht:
                pad = _pad_box(a, n)
                total += sum(_overlap(pad, c) for c in (yard(b, "front"), yard(b, "back")) if c)
    return total


def _pad_box(p: "Part", n):
    x, y = p.pad(n)
    w, h = (p.pad_size or {}).get(n, (0.0, 0.0))
    if p.r % 180:
        w, h = h, w
    return x - w / 2, y - h / 2, x + w / 2, y + h / 2


def _corners(b):
    return [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]


def _poly_overlap(b, poly) -> float:
    from shapely.geometry import box
    x0, y0, x1, y1 = poly.bounds
    if b[2] <= x0 or b[0] >= x1 or b[3] <= y0 or b[1] >= y1:
        return 0.0
    return box(*b).intersection(poly).area


def _overlap(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    w = min(ax1, bx1) - max(ax0, bx0)
    h = min(ay1, by1) - max(ay0, by0)
    return w * h if w > 0 and h > 0 else 0.0


def _pt_seg(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(ax + t * dx - px, ay + t * dy - py)


def _seg_box(ax, ay, bx, by, b) -> float:
    """Distance from segment a-b to box b (0 when they meet; Liang-Barsky clip, then endpoints and corners)."""
    x0, y0, x1, y1 = b
    dx, dy = bx - ax, by - ay
    t0, t1, hit = 0.0, 1.0, True
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0:
            if q < 0:
                hit = False
                break
        elif p < 0:
            t0 = max(t0, q / p)
        else:
            t1 = min(t1, q / p)
        if t0 > t1:
            hit = False
            break
    if hit:
        return 0.0
    d = min(math.hypot(max(x0 - x, 0.0, x - x1), max(y0 - y, 0.0, y - y1)) for x, y in ((ax, ay), (bx, by)))
    return min([d] + [_pt_seg(cx, cy, ax, ay, bx, by) for cx, cy in _corners(b)])


def _seg_pad(s, b, rnd) -> float:
    """Distance from segment s (ax, ay, bx, by) to a pad: its box, or the circle inscribed in it when `rnd`."""
    if rnd:
        return max(0.0, _pt_seg((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, *s) - (b[2] - b[0]) / 2)
    return _seg_box(*s, b)


REGION_PAD_W = 10.0  # floorplan region term: weight of the pad area of a region's nets outside the region
ISO_MARGIN = 0.5  # mm added to the creepage (else clearance) of a board.isolation group
CLEAR_MARGIN = 0.1  # mm added to the net class clearance between the pads of two nets
CONG_UTIL = 0.4  # share of the theoretical track capacity of a cell the router can really use
CORRIDOR_CAP = 2.0  # mm beyond the track width: a channel that wide is open, copper farther away is not looked at
CORRIDOR_MAX_PADS = 16  # a constrained net with more pads has no corridor term (a spread net, not a channel)


STAGES: list = []  # per placement stage of the layout flow: {stage, s, hpwl, overflow, overlap} (docs/layout-flow.md)
DP_PASSES = 4  # detailed placement passes at most
DP_STEPS = (0.25, 0.5, 1.0, 2.0)  # detailed placement shifts, in track pitches (board track + clearance)
DP_RADIUS = 3.0  # detailed placement swap and window neighbourhood (Manhattan), in typical part sizes (_unit_size)
ANNEAL = {"moves": 0, "s": 0.0}  # annealing moves and seconds in this process (the anneal rate, docs/asic-ideas.md)
_REPORTED: set = set()  # fixed-part violations already printed, once per process
_BLOCKS: dict = {}  # corridor block verdicts per net and fixed-part placement (the corridor proof is slow)
REFINE = 0.2  # refine pass length, as a share of the per-seed iterations


class Placer:
    def __init__(self, design: Design, parts: dict[str, Part], width: float, height: float, seed: int = 1,
                 weights: dict | None = None, keepouts: list | None = None):
        self.units = list(getattr(parts, "blocks", ()))
        self.unit_of = {r: u for u in self.units for r in u.refs()}
        if self.units:  # the cost sees the block courtyards too; the caller's `parts` keeps the components only
            areas, parts = getattr(parts, "areas", ()), Parts(parts)
            parts.areas = areas
            for u in self.units:
                parts[u.ref] = u.yard
                u.pull(parts)
        self.d, self.parts, self.W, self.H = design, parts, width, height
        self.rng = random.Random(seed)
        self.keepouts = list(keepouts or [])  # (x0, y0, x1, y1, capacity fraction) boxes where tracks are not allowed
        self.wt = {"wire": 1.0, "overlap": 400.0, "bounds": 400.0, "decouple": 6.0, "group": 0.6, "edge": 30.0,
                   "congestion": 1.0, "keepout": 400.0, "isolation": 400.0, "separation": 4.0, "thermal": 4.0, "antenna": 4.0,
                   "corridor": 30.0}
        self.wt.update(weights or {})
        gnds = design.ground_nets()
        self.nets = []
        self._net_rudy = []
        for net, conns in design.nets().items():
            conns = [(r, p) for r, p in conns if r in parts and p in parts[r].pads]
            if len(conns) < 2:
                continue
            info = design.net_info(net)
            w = 0.15 if net in gnds and design.board["gnd_pour"] else float(info.get("place_weight", 1.0))
            if info.get("class") == "power":
                w *= 1.3
            self.nets.append((w, conns))
            self._net_rudy.append(not (net in gnds and design.board["gnd_pour"]))  # a pour needs no tracks
        # decoupling pairs: (cap ref, cap pad, ic ref, ic pad, cap ground pad, ic ground pads) on the shared supply
        # net; the loop is the supply distance plus the distance from the cap's ground pad to the nearest IC ground pad
        self.decap = []
        for p in parts.values():
            if p.decouples and p.decouples in parts:
                cap = design.components[p.ref]
                ic = design.components[p.decouples]
                cg = next((c for c, n in cap.pins.items() if n in gnds and c in p.pads), None)
                ig = [i for i, n in ic.pins.items() if n in gnds and i in parts[ic.ref].pads]
                first = True
                for cpin, net in cap.pins.items():
                    if net in gnds:
                        continue
                    for ipin, inet in ic.pins.items():
                        if inet == net and ipin in parts[ic.ref].pads:
                            self.decap.append((p.ref, cpin, ic.ref, ipin, cg if first and ig else None, ig))
                            first = False
        # indexes for the incremental cost
        self.order = {r: i for i, r in enumerate(parts)}
        self.part_nets = {r: [] for r in parts}
        for k, (_, conns) in enumerate(self.nets):
            for r in {r for r, _ in conns}:
                self.part_nets[r].append(k)
        self.part_decs = {r: [] for r in parts}
        for k, (cr, _, ir, *_) in enumerate(self.decap):
            self.part_decs[cr].append(k)
            self.part_decs[ir].append(k)
        self.group_members: dict[str, list] = {}
        for p in parts.values():
            if p.group:
                self.group_members.setdefault(p.group, []).append(p)
        self.bucket = 4.0  # mm, spatial hash cell for the overlap pairs
        self._init_rules(getattr(parts, "areas", ()))
        self._init_seps()
        self._init_corridors()

    # ---- separation terms (L12): aggressor/sensitive, heat/sensor, antenna/relay+mains --------------------------

    SEP_MM = 3.0       # aggressor pad to sensitive pad
    HOT_W = 0.05       # a part above this dissipation is a heat source

    def _roles(self):
        """ref -> 'temp_sensor' | 'antenna' | None. `role` wins; `role: none` switches the inference off."""
        out = {}
        for r in self.parts:
            c = self.d.components.get(r)
            if c is None:  # a block courtyard
                continue
            role = c.get("role")
            if role in ("temp_sensor", "antenna"):
                out[r] = role
            elif role == "none":
                continue
            else:
                sym, fp = str(c.get("symbol") or ""), str(c.get("footprint") or "")
                if sym.startswith("Sensor_Temperature:") or sym.startswith("Device:Thermistor"):
                    out[r] = "temp_sensor"
                elif sym.startswith("RF_Module:") or "antenna" in fp.lower():
                    out[r] = "antenna"
        return out

    def _hot_parts(self):
        """Refs that dissipate above HOT_W, from the derating table of build/status.json (none when no sim ran)."""
        import json
        try:
            st = json.loads((self.d.dir / "build" / "status.json").read_text())
            rows = (st.get("sim") or {}).get("derating") or []
            return {x["ref"] for x in rows if abs(x.get("p_w") or 0) > self.HOT_W and x["ref"] in self.parts}
        except Exception:  # noqa: BLE001 - no build yet: the term is skipped
            return set()

    def _init_seps(self):
        """self.seps: (ref a, pads a or None, ref b, pads b or None, need mm, weight key). With pads the distance is
        the smallest pad to pad distance, else the gap between the two courtyards."""
        d, P = self.d, self.parts
        self.seps = []
        nets = d.nets()
        agg, vic = {}, {}
        for n, conns in nets.items():
            info = d.net_info(n)
            for r, p in conns:
                if r not in P or p not in P[r].pads:
                    continue
                if info.get("class") in ("clock", "switching", "high_speed"):
                    agg.setdefault(r, []).append(p)
                if info.get("sensitive"):
                    vic.setdefault(r, []).append(p)
        gn = d.ground_nets()
        shared = {r: {n for n, conns in nets.items() if n not in gn and any(x == r for x, _ in conns)} for r in set(agg) | set(vic)}
        for a, pa in agg.items():
            for b, pb in vic.items():
                if a != b and not (shared[a] & shared[b]):  # parts wired to each other are meant to sit together
                    self.seps.append((a, pa, b, pb, self.SEP_MM, "separation"))
        roles = self._roles()
        sensors = [r for r, v in roles.items() if v == "temp_sensor"]
        antennas = [r for r, v in roles.items() if v == "antenna"]
        tc = float(d.board.get("thermal_clearance_mm", 5))
        for h in self._hot_parts():
            for s_ in sensors:
                if h != s_:
                    self.seps.append((h, None, s_, None, tc, "thermal"))
        if antennas:
            from . import pcb
            mains = {n for g in pcb.isolation_needs(d) for n in g["members"]}
            noisy = {r for r, c in d.components.items() if r in P and (
                (c.kind == "K" and r[:1] == "K") or str(c.get("symbol") or "").split(":")[-1].startswith("Relay")
                or str(c.get("symbol") or "").startswith("Relay"))}
            noisy |= {r for n in mains for r, p in nets.get(n, []) if r in P}
            ac = float(d.board.get("antenna_clearance_mm", 10))
            for a in antennas:
                for b in sorted(noisy):
                    if a != b:
                        self.seps.append((a, None, b, None, ac, "antenna"))
        self.part_seps = {r: [] for r in P}
        for k, e in enumerate(self.seps):
            self.part_seps[e[0]].append(k)
            self.part_seps[e[2]].append(k)

    def _sep_val(self, k):
        """Weighted shortfall (mm) of separation k below its distance."""
        a, pa, b, pb, need, key = self.seps[k]
        P = self.parts
        if pa is not None:
            pts_a = [P[a].pad(n) for n in pa]
            pts_b = [P[b].pad(n) for n in pb]
            gap = min(math.hypot(x0 - x1, y0 - y1) for x0, y0 in pts_a for x1, y1 in pts_b)
        else:
            A, B = P[a].box(), P[b].box()
            gap = math.hypot(max(0.0, A[0] - B[2], B[0] - A[2]), max(0.0, A[1] - B[3], B[1] - A[3]))
        return self.wt[key] * max(0.0, need - gap)

    def _init_rules(self, areas):
        """Keep-out areas (courtyards and pads forbidden, tracks removed from the congestion capacity) and the pad
        groups of board.isolation (a pad of a group keeps creepage + ISO_MARGIN to the pads of every other net)."""
        nl = max(1, int(self.d.board.get("layers", 2)))
        self.ko_fp, self.ko_pad = [], []  # (box, board sides, owner, name)
        self.ko_poly = {}  # (owner, name) -> area polygon, to confirm a box overlap before it is reported
        for a in areas:
            if a.get("poly") is not None:
                self.ko_poly[(a["owner"], a["name"])] = a["poly"]
            lay = a["layers"]
            sides = {s for s, L in (("front", "F.Cu"), ("back", "B.Cu")) if L in lay or "*.Cu" in lay}
            if "footprints" in a["forbids"]:
                self.ko_fp.append((a["box"], sides, a["owner"], a["name"]))
            elif "pads" in a["forbids"]:
                self.ko_pad.append((a["box"], sides, a["owner"], a["name"]))
            if "tracks" in a["forbids"]:
                n = nl if "*.Cu" in lay else len([L for L in lay if L.endswith(".Cu")])
                self.keepouts.append((*a["box"], max(0.0, 1 - n / nl)))
        from . import pcb
        net_of = {(r, p): n for n, conns in self.d.nets().items() for r, p in conns}
        self.iso_need, self.iso_name, self.iso_between, gid = [], [], [], {}
        for g in pcb.isolation_needs(self.d):
            for n in g["members"]:
                gid.setdefault(n, len(self.iso_need))
            self.iso_need.append(g["mm"] + ISO_MARGIN)
            self.iso_name.append(g.get("name") or f"isolation group {len(self.iso_name) + 1}")
            # between_mm: two different member nets keep it (functional insulation), with the clearance margin
            self.iso_between.append(g["between"] + CLEAR_MARGIN if g.get("between") is not None else 0.0)
        base = float(self.d.board["clearance_mm"])  # a net class with a larger clearance keeps it to every other net
        cls = {n: v["clearance"] + CLEAR_MARGIN for n, v in pcb.net_classes(self.d).items() if v["clearance"] > base}
        self.pad_grp, self.pad_net, self.pad_cl = {}, {}, {}  # (ref, pad) -> group index or -1, net, class need (0: none)
        self.iso_parts = set()
        for (r, p), n in net_of.items():  # a pad without copper (a keep-out marker's dummy pad) keeps no distance
            if r in self.parts and p in self.parts[r].pads and p not in self.parts[r].nocu:
                self.pad_grp[(r, p)], self.pad_net[(r, p)], self.pad_cl[(r, p)] = gid.get(n, -1), n, cls.get(n, 0.0)
                if n in gid or n in cls:
                    self.iso_parts.add(r)
        # The area enclosed by an isolation group's pads, where its tracks run: a part without a pad of the group
        # stays out of it, or every route to it crosses the isolation distance. Only for a group whose parts are all
        # fixed (the area is then known before placement).
        self.iso_hulls = []
        from shapely.geometry import MultiPoint
        for gi in range(len(self.iso_need)):
            keys = [k for k, g in self.pad_grp.items() if g == gi]
            refs = {k[0] for k in keys}
            if refs and all(self.parts[r].fixed for r in refs):
                pts = [c for k in keys for c in _corners(_pad_box(self.parts[k[0]], k[1]))]
                hull = MultiPoint(pts).convex_hull
                if hull.area > 1.0:
                    self.iso_hulls.append((hull, refs, f"isolation group {gi + 1} area"))
        if not self.iso_parts:
            self.pad_grp = {}
        self.reach = max(self.iso_need + self.iso_between + list(cls.values()) + [0.0]) + 1.0
        self.part_pads = {r: [k for k in self.pad_grp if k[0] == r] for r in self.parts} if self.pad_grp else {}
        from . import floorplan
        self.regions, self.region_start = floorplan.placer_regions(self.d, self.parts)  # design.yaml floorplan.regions

    # ---- routing corridors of the constrained nets ----------------------------------------------------------------

    def _init_corridors(self):
        """Constrained nets: the members of a board.isolation group (or a drc_rules isolation pattern), the nets with
        `layers` and the nets of a floorplan region with `nets`. self.cor: (net, pads, track width, layers it may use,
        static obstacles [(polygon, spacing, name)]): the conductive keep-outs its region keeps the isolation from and
        the rule areas without tracks on every layer it may use."""
        from . import floorplan, pcb
        d = self.d
        self.cor, self.cor_part = [], {}
        groups = pcb.isolation_needs(d)
        lays = pcb.net_layers(d)
        regs = floorplan.net_regions(d)
        cons = {n for g in groups for n in g["members"]} | set(lays) | {n for r in regs for n in r["nets"]}
        nets = d.nets()
        cons &= set(nets)
        if not cons:
            return
        cls = pcb.net_classes(d)
        self._cor_base = float(d.board["clearance_mm"])
        self._cor_cl = {n: v["clearance"] for n, v in cls.items()}
        self._cor_gid, self._cor_mm, self._cor_btw, self._cor_need = {}, [], [], {}
        for i, g in enumerate(groups):
            for n in g["members"]:
                self._cor_gid.setdefault(n, i)
            self._cor_mm.append(g["mm"])
            self._cor_btw.append(g["between"] or 0.0)
        self._cor_reach = max(self._cor_mm + self._cor_btw + list(self._cor_cl.values()) + [self._cor_base])
        self.cor_net = {(r, p): n for n, conns in nets.items() for r, p in conns}
        allcu = frozenset(pcb.cu_layers(int(d.board.get("layers", 2))))
        cond = floorplan.conductive_needs(d)
        areas = [a for a in getattr(self.parts, "areas", ()) if "tracks" in a["forbids"]]
        from shapely.geometry import box
        for n in sorted(cons):
            P = self.parts
            pads = [(r, p) for r, p in nets[n] if r in P and p in P[r].pads and p not in P[r].nocu]
            if not 2 <= len(pads) <= CORRIDOR_MAX_PADS:
                continue
            L = frozenset(lays.get(n) or allcu)
            static = [(c["poly"], float(c["mm"]), f"conductive keep-out {c['keepout']}") for c in cond if n in c["nets"]]
            static += [(a["poly"] if a.get("poly") is not None else box(*a["box"]), self._cor_base, f"rule area {a['name']}")
                       for a in areas if "*.Cu" in a["layers"] or L <= set(a["layers"])]
            self.cor.append((n, pads, float(cls[n]["width"]), L, static))
        for k, (_, pads, *_) in enumerate(self.cor):
            for r in {r for r, _ in pads}:
                self.cor_part.setdefault(r, []).append(k)

    def _cor_space(self, n, m) -> float:
        """Copper distance a track of constrained net n keeps to copper of net m (None: a pad without a net): the
        between_mm of their isolation group (two different members), the group distance (a member and another
        net), else the larger net class clearance. Router values, without the placer margins."""
        v = self._cor_need.get((n, m))
        if v is None:
            base = self._cor_base
            v = max(self._cor_cl.get(n, base), self._cor_cl.get(m, base) if m else base)
            gn, gm = self._cor_gid.get(n), self._cor_gid.get(m)
            if gn is not None and gn == gm:
                v = max(v, self._cor_btw[gn])
            else:
                v = max([v] + [self._cor_mm[g] for g in (gn, gm) if g is not None])
            self._cor_need[(n, m)] = v
        return v

    def _cor_blocks(self, part, pn, L) -> bool:
        """Pad pn of `part` blocks a track that may only use the layers L (a through-hole pad blocks every layer)."""
        cu = part.cu.get(pn) if part.cu else None
        return cu is None or "*" in cu or L <= cu

    def _cor_eval(self, k, buckets):
        """(shortfall mm, links, bucket cells looked at) of constrained net k. The links are the edges of the minimum
        spanning tree of its pads: (free width, pad a, pad b, limiting copper). The free width of a link is the best
        of the straight and the two L-shaped channels; a channel's width is twice the smallest distance, along it,
        from the track centre line to foreign copper grown by the spacing that copper needs (_cor_space; conductive
        keep-outs by their isolation). The shortfall is the sum over links of what is missing to the track width."""
        n, pads, w, L, static = self.cor[k]
        P = self.parts
        pts = [P[r].pad(p) for r, p in pads]
        tree, rest = [], {i: (math.hypot(pts[i][0] - pts[0][0], pts[i][1] - pts[0][1]), 0) for i in range(1, len(pts))}
        while rest:  # Prim
            i = min(rest, key=lambda j: rest[j][0])
            tree.append((rest.pop(i)[1], i))
            for j in rest:
                dj = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
                if dj < rest[j][0]:
                    rest[j] = (dj, i)
        half = (w + CORRIDOR_CAP) / 2
        G = self._cor_reach + half + 0.5
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        B = self.bucket
        cells = [(i, j) for i in range(int((min(xs) - G) // B), int((max(xs) + G) // B) + 1)
                 for j in range(int((min(ys) - G) // B), int((max(ys) + G) // B) + 1)]
        own = set(pads)
        obs = []  # (pad box, round, spacing, label)
        for q in sorted(set().union(*(buckets.get(c, ()) for c in cells))):
            pq = P[q]
            for pn in pq.pads:
                if pn in pq.nocu or (q, pn) in own:
                    continue
                m = self.cor_net.get((q, pn))
                if m == n or not self._cor_blocks(pq, pn, L):
                    continue
                need = self._cor_space(n, m)
                obs.append((self._pad_box(pq, pn), pn in pq.round, need, f"{q}.{pn} {m or 'no net'} {need:g} mm"))
        total, links = 0.0, []
        for a, b in tree:
            W, lim = self._cor_link(pts[a], pts[b], self._cor_trim(pads[a]), self._cor_trim(pads[b]), w, obs, static)
            total += max(0.0, w - W)
            links.append((W, pads[a], pads[b], lim))
        return total, links, set(cells)

    def _cor_trim(self, key) -> float:
        """Half the smaller side of a pad: the channel starts at the pad edge (the pad's own escape is not placement)."""
        w, h = (self.parts[key[0]].pad_size or {}).get(key[1], (0.0, 0.0))
        return min(w, h) / 2

    @staticmethod
    def _cor_link(a, b, ta, tb, w, obs, static):
        """(free width, limiting copper) of the best channel from a to b: straight, then the two L shapes when the
        straight one is narrower than the track. Segment ends at the pads are trimmed by ta and tb."""
        half = (w + CORRIDOR_CAP) / 2
        paths = [[a, b]]
        if abs(a[0] - b[0]) > 1e-6 and abs(a[1] - b[1]) > 1e-6:
            paths += [[a, (b[0], a[1]), b], [a, (a[0], b[1]), b]]
        best = (-math.inf, None)
        for path in paths:
            pts = [list(p) for p in path]
            for i, t in ((0, ta), (len(pts) - 1, tb)):  # trim the first and the last segment at the pads
                j = 1 if i == 0 else i - 1
                L = math.hypot(pts[j][0] - pts[i][0], pts[j][1] - pts[i][1])
                if L > 1e-9:
                    f = min(t, L / 2 if len(pts) == 2 else L) / L
                    pts[i] = [pts[i][0] + (pts[j][0] - pts[i][0]) * f, pts[i][1] + (pts[j][1] - pts[i][1]) * f]
            segs = [(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1)]
            x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
            y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
            m, lim = half, None
            for bx, rnd, need, label in obs:
                g = need + m
                if bx[0] - g > x1 or bx[2] + g < x0 or bx[1] - g > y1 or bx[3] + g < y0:
                    continue
                v = min(_seg_pad(s, bx, rnd) for s in segs) - need
                if v < m:
                    m, lim = v, label
            if static:
                from shapely.geometry import LineString
                line = None
                for poly, need, name in static:
                    g, (px0, py0, px1, py1) = need + m, poly.bounds
                    if px0 - g > x1 or px1 + g < x0 or py0 - g > y1 or py1 + g < y0:
                        continue
                    line = line or LineString([(s[0], s[1]) for s in segs] + [(segs[-1][2], segs[-1][3])])
                    v = poly.distance(line) - need
                    if v < m:
                        m, lim = v, f"{name} {need:g} mm"
            if 2 * m > best[0]:
                best = (2 * m, lim)
            if best[0] >= w:
                break
        return best

    def _bucket_map(self) -> dict:
        """The spatial hash of the current positions, from scratch (cost_full and the reports)."""
        out: dict = {}
        for r in self.parts:
            for bk in self._buckets(r):
                out.setdefault(bk, set()).add(r)
        return out

    def corridors(self) -> list[dict]:
        """Narrowest channel of each constrained net at the current placement: [{net, a, b, width, track, limit, ok,
        msg}] (width in mm, the best of the straight and L-shaped channels of its narrowest link)."""
        bk = self._bucket_map()
        out = []
        for k, (n, _, w, L, _) in enumerate(self.cor):
            W, a, b, lim = min(self._cor_eval(k, bk)[1], key=lambda x: x[0])
            W = max(0.0, W)
            ok = W >= w - 1e-6
            wtxt = f">= {W:.2f}" if lim is None else f"{W:.2f}"
            out.append({"net": n, "a": f"{a[0]}.{a[1]}", "b": f"{b[0]}.{b[1]}", "width": round(W, 2), "track": w,
                        "limit": lim, "ok": ok,
                        "msg": f"{n} {a[0]}.{a[1]}-{b[0]}.{b[1]} on {'/'.join(sorted(L))}: channel {wtxt} mm for a "
                               f"{w:g} mm track" + (f", limited by {lim}" if lim else "")
                               + ("" if ok else " narrow (a neck may be needed)")})
        return out

    def corridor_blocks(self) -> list[str]:
        """Constrained nets whose pads are all on fixed parts and that the corridor proof (corridor.py, the check of
        `hwflow check`: the same copper model, spacings, between_mm, conductive keep-outs, layers and neck allowance)
        cannot connect past the copper of the fixed parts alone. A track may neck down to the board minimum for at
        most routing.NECK_MAX_MM per stretch, as the router does, so a corridor only a neck passes is no block. No
        placement opens a blocked corridor: it is a placement violation and the layout stage does not route."""
        if not self.cor:
            return []
        P = self.parts
        fixed = sorted(r for r in P if self._unit(P[r]).fixed)
        nets = tuple((n, w) for n, pads, w, *_ in self.cor if all(self._unit(P[r]).fixed for r, _ in pads))
        if not nets:
            return []
        sig = tuple((r, P[r].x, P[r].y, P[r].r) for r in fixed)
        key = (str(getattr(self.d, "dir", "")), nets, self.W, self.H, sig)
        if key not in _BLOCKS:
            _BLOCKS[key] = self._block_check([n for n, _ in nets], fixed)
        return list(_BLOCKS[key])

    def _block_check(self, nets, fixed) -> list[str]:
        from . import corridor
        libs = getattr(self.parts, "libs", None)
        if libs is None:
            from .libs import Libs
            libs = Libs(design=self.d)
        model = corridor.Model(self.d, libs, parts=self.parts, use=set(fixed))
        out = []
        for n in nets:
            nodes = corridor._nodes(model, self.d, n, {})
            if nodes is None or len(nodes) < 2:
                continue
            w = model.width(n)
            if corridor._connects(model, n, nodes, w, w > model.floor + corridor.EPS):
                continue
            f = corridor._finding(model, self.d, n, nodes, w, fixed_only=True)
            out.append(f"corridor {n} blocked between fixed parts: {f['msg']}")
        return out

    # ---- congestion (RUDY) ------------------------------------------------------------------------------------

    def _init_grid(self):
        b = self.d.board
        self.cell = 1.0
        self.nx, self.ny = max(1, math.ceil(self.W / self.cell)), max(1, math.ceil(self.H / self.cell))
        pitch = float(b["track_mm"]) + float(b["clearance_mm"])
        # mm of wire a cell can carry: layers x cell area / pitch, derated for vias, pads and routing inefficiency
        per_cell = max(1, int(b.get("layers", 2))) * self.cell * self.cell / pitch * CONG_UTIL
        self.cap = np.full((self.nx, self.ny), per_cell)
        for x0, y0, x1, y1, frac in self.keepouts:  # frac of the capacity left inside the area (0 = no tracks)
            i0, i1 = max(0, int(x0 // self.cell)), min(self.nx, math.ceil(x1 / self.cell))
            j0, j1 = max(0, int(y0 // self.cell)), min(self.ny, math.ceil(y1 / self.cell))
            self.cap[i0:i1, j0:j1] *= frac
        self.grid = np.zeros((self.nx, self.ny))
        self._blk_pitch, self._blk_layers = pitch, max(1, int(b.get("layers", 2)))  # for _pad_blockage

    def _pad_blockage(self, r):
        """(i0, j0, array) of the capacity (mm of wire per cell) that the pads of `r` take, or None: each copper pad,
        grown by half a track pitch, removes its covered share of the cell from each layer it is on (a through-hole
        pad all layers, an SMD pad one)."""
        p = self.parts[r]
        boxes = []
        h = self._blk_pitch / 2
        for n in p.pads:
            if n in p.nocu:
                continue
            cu = p.cu.get(n) if p.cu else None
            nl = self._blk_layers if cu is None or "*" in cu else min(self._blk_layers, len(cu))
            b = _pad_box(p, n)
            boxes.append((b[0] - h, b[1] - h, b[2] + h, b[3] + h, nl))
        if not boxes:
            return None
        c = self.cell
        i0 = min(self.nx - 1, max(0, int(min(b[0] for b in boxes) // c)))
        i1 = min(self.nx, max(i0 + 1, math.ceil(max(b[2] for b in boxes) / c)))
        j0 = min(self.ny - 1, max(0, int(min(b[1] for b in boxes) // c)))
        j1 = min(self.ny, max(j0 + 1, math.ceil(max(b[3] for b in boxes) / c)))
        ex, ey = np.arange(i0, i1 + 1) * c, np.arange(j0, j1 + 1) * c
        out = np.zeros((i1 - i0, j1 - j0))
        per = c * c / self._blk_pitch * CONG_UTIL / (c * c)  # capacity per mm2 of one layer
        for x0, y0, x1, y1, nl in boxes:
            ox = np.clip(np.minimum(ex[1:], x1) - np.maximum(ex[:-1], x0), 0, None)
            oy = np.clip(np.minimum(ey[1:], y1) - np.maximum(ey[:-1], y0), 0, None)
            out += (nl * per) * np.outer(ox, oy)
        return i0, j0, out

    def _rudy(self, xs, ys):
        """(i0, j0, demand array) of a net: HPWL spread uniformly over its bounding box, in mm of wire per cell."""
        c = self.cell
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        hp = (x1 - x0) + (y1 - y0)
        if x1 - x0 < c:
            x0, x1 = (x0 + x1) / 2 - c / 2, (x0 + x1) / 2 + c / 2
        if y1 - y0 < c:
            y0, y1 = (y0 + y1) / 2 - c / 2, (y0 + y1) / 2 + c / 2
        dens = hp / ((x1 - x0) * (y1 - y0))
        i0 = min(self.nx - 1, max(0, int(x0 // c)))
        i1 = min(self.nx, max(i0 + 1, math.ceil(x1 / c)))
        j0 = min(self.ny - 1, max(0, int(y0 // c)))
        j1 = min(self.ny, max(j0 + 1, math.ceil(y1 / c)))
        ex, ey = np.arange(i0, i1 + 1) * c, np.arange(j0, j1 + 1) * c
        ox = np.clip(np.minimum(ex[1:], x1) - np.maximum(ex[:-1], x0), 0, None)
        oy = np.clip(np.minimum(ey[1:], y1) - np.maximum(ey[:-1], y0), 0, None)
        return i0, j0, dens * np.outer(ox, oy)

    def _grid_add(self, i0, j0, dem, sign):
        sl = (slice(i0, i0 + dem.shape[0]), slice(j0, j0 + dem.shape[1]))
        g, cp = self.grid[sl], self.cap[sl]
        o = np.maximum(g - cp, 0).ravel()
        before = o @ o
        g += sign * dem
        o = np.maximum(g - cp, 0).ravel()
        self.t_cong += o @ o - before

    def congestion(self) -> float:
        """Sum over cells of (wire demand above capacity)^2, from scratch."""
        grid = np.zeros((self.nx, self.ny))
        for k, (w, conns) in enumerate(self.nets):
            if self._net_rudy[k]:
                xs, ys = zip(*(self.parts[r].pad(p) for r, p in conns))
                i0, j0, dem = self._rudy(xs, ys)
                grid[i0:i0 + dem.shape[0], j0:j0 + dem.shape[1]] += dem
        return float(np.square(np.clip(grid - self.cap, 0, None)).sum())

    def max_overflow(self) -> float:
        """Largest per-cell wire demand above capacity (mm of wire), from scratch."""
        self.cost()
        return float(np.clip(self.grid - self.cap, 0, None).max())

    # ---- cost -------------------------------------------------------------------------------------------------

    def _net_len(self, k):
        P = self.parts
        xs, ys = zip(*(P[r].pad(p) for r, p in self.nets[k][1]))
        return self.nets[k][0] * ((max(xs) - min(xs)) + (max(ys) - min(ys))), xs, ys

    def _part_cost(self, r):
        """bounds + edge terms of one part (unweighted pair: (bounds, edge))."""
        p = self.parts[r]
        m = self.d.board["margin_mm"]
        x0, y0, x1, y1 = p.box()
        bnd = max(0, m - x0) + max(0, m - y0) + max(0, x1 - self.W + m) + max(0, y1 - self.H + m)
        edge = 0.0
        if p.edge:
            edge = abs({"left": x0 - m, "right": self.W - m - x1, "top": y0 - m, "bottom": self.H - m - y1}[p.edge])
        return bnd, edge

    def _ko_cost(self, r):
        """Courtyard area of `r` inside the areas that forbid footprints, plus pad area inside those that forbid pads."""
        p = self.parts[r]
        c = 0.0
        for box, sides, owner, _ in self.ko_fp:
            if owner != r and not self._same(owner, r):
                for s in sides:
                    b = p.side_box(s)
                    if b:
                        c += _overlap(b, box)
        for box, sides, owner, _ in self.ko_pad:
            if owner != r and not self._same(owner, r) and sides & set(p.sides or ()):
                for n in p.pads:
                    if n not in p.nocu:
                        c += _overlap(self._pad_box(p, n), box)
        for hull, refs, _ in self.iso_hulls:
            if r not in refs and not any(self._same(r, q) for q in refs):
                c += sum(_poly_overlap(b, hull) for b in (p.side_box("front"), p.side_box("back")) if b)
        for g in self.regions:
            c += sum(self._region_cost(p, g))
        return c

    # ---- floorplan regions (floorplan.placer_regions) -----------------------------------------------------------

    @staticmethod
    def _in_region(b, g) -> float:
        return _overlap(b, g["rect"]) if g["rect"] else _poly_overlap(b, g["poly"])

    def _region_cost(self, p, g) -> tuple[float, float, float]:
        """(courtyard area of a member outside its region, courtyard area of another part inside an exclusive region on
        its sides, REGION_PAD_W x pad area of the region's nets outside it). Courtyards without the placer margin."""
        m = COURTYARD_MARGIN
        out = inside = pads = 0.0
        if p.ref in g["members"]:
            for b in (p.side_box("front"), p.side_box("back")):
                if b:
                    b = (b[0] + m, b[1] + m, b[2] - m, b[3] - m)
                    out += (b[2] - b[0]) * (b[3] - b[1]) - self._in_region(b, g)
        elif g["excl"] and p.ref not in g["allow"]:
            for s in g["sides"]:
                b = p.side_box(s)
                if b:
                    inside += self._in_region((b[0] + m, b[1] + m, b[2] - m, b[3] - m), g)
        for n in g["pads"].get(p.ref, ()):  # a pad outside is an isolation error: weighed like 10 times its area
            b = _pad_box(p, n)
            pads += REGION_PAD_W * ((b[2] - b[0]) * (b[3] - b[1]) - self._in_region(b, g))
        return out, inside, pads

    def _in_area(self, bx, owner, name, courtyard=False) -> bool:
        poly = self.ko_poly.get((owner, name))
        if poly is None:
            return True
        from shapely.geometry import box
        m = COURTYARD_MARGIN if courtyard else 0.0  # courtyard boxes carry this margin; KiCad tests the courtyard
        b = box(bx[0] + m, bx[1] + m, bx[2] - m, bx[3] - m) if bx[2] - bx[0] > 2 * m and bx[3] - bx[1] > 2 * m else box(*bx)
        return b.intersection(poly).area > 0.01

    def _same(self, a, b) -> bool:
        """a and b are parts (or the courtyard) of one block: their relative placement is the block's, not a cost."""
        u = self.unit_of.get(a)
        return u is not None and u is self.unit_of.get(b)

    def _refs(self, p) -> list[str]:
        """Refs a move of `p` (a part or a block) changes, after moving the block's members with it."""
        if isinstance(p, Rigid):
            p.sync(self.parts)
            return p.refs()
        return [p.ref]

    def _unit(self, p):
        """The thing that moves when part `p` moves: its block, or the part itself."""
        return self.unit_of.get(p.ref, p)

    def _shift(self, p, dx, dy):
        u = self._unit(p)
        u.x += dx
        u.y += dy
        if u is not p:
            u.sync(self.parts)

    def _movable(self) -> list:
        """Movable parts and blocks, each block once, at the place of its first member."""
        out, seen = [], set()
        for p in self.parts.values():
            u = self._unit(p)
            if not u.fixed and id(u) not in seen:
                seen.add(id(u))
                out.append(u)
        return out

    def _boxes(self, p):
        return [b for b in (p.side_box("front"), p.side_box("back")) if b] + [self._pad_box(p, n) for n in p.pads]

    _pad_box = staticmethod(_pad_box)

    def _iso_short(self, a, b):
        """Shortfall (mm) of the copper gap between pads a and b (ref, pad) below what they need: the creepage of a
        board.isolation group (pads of different groups), its between_mm (pads of two different member nets) or the
        net class clearance (pads of different nets); 0 on the same part."""
        if a[0] == b[0] or self._same(a[0], b[0]):
            return 0.0
        ga, gb = self.pad_grp[a], self.pad_grp[b]
        need = max([self.iso_need[g] for g in (ga, gb) if g >= 0 and ga != gb] or [0.0])
        if self.pad_net[a] != self.pad_net[b]:
            need = max(need, self.pad_cl[a], self.pad_cl[b])
            if ga == gb >= 0:
                need = max(need, self.iso_between[ga])
        if need <= 0:
            return 0.0
        ba, bb = self._pad_box(self.parts[a[0]], a[1]), self._pad_box(self.parts[b[0]], b[1])
        gap = math.hypot(max(0.0, ba[0] - bb[2], bb[0] - ba[2]), max(0.0, ba[1] - bb[3], bb[1] - ba[3]))
        return max(0.0, need - gap)

    def _iso_rule(self, a, b) -> str:
        """The rule behind _iso_short(a, b), for the message: the isolation group (between a member and a pad outside
        the group), its between_mm (two different member nets) or the net class clearance."""
        ga, gb = self.pad_grp[a], self.pad_grp[b]
        nets = f"{self.pad_net[a]} / {self.pad_net[b]}"
        if ga == gb >= 0 and self.iso_between[ga] >= max(self.pad_cl[a], self.pad_cl[b]) and self.iso_between[ga]:
            return (f"{nets}: isolation {self.iso_name[ga]} between_mm {self.iso_between[ga] - CLEAR_MARGIN:g} mm + "
                    f"{CLEAR_MARGIN:g} mm margin between two of its member nets, pad bounding boxes")
        gs = [g for g in (ga, gb) if g >= 0 and ga != gb]
        if gs:
            g = max(gs, key=lambda k: self.iso_need[k])
            if self.iso_need[g] >= max(self.pad_cl[a], self.pad_cl[b]):
                return (f"{nets}: isolation {self.iso_name[g]} {self.iso_need[g] - ISO_MARGIN:g} mm + {ISO_MARGIN:g} "
                        f"mm placer margin between a member and another net, pad bounding boxes")
        return f"{nets}: net class clearance {max(self.pad_cl[a], self.pad_cl[b]):g} mm (with margin), pad bounding boxes"

    def _iso_scan(self, r, log, skip=()):
        if not self.iso_parts:
            return
        cand = set(self.iso_parts)  # a part without a rule pad only meets the parts that have one
        if r in self.iso_parts or len(cand) > 8:  # the parts within reach, from the spatial hash
            B = self.bucket
            bs = [b for b in (self.parts[r].side_box("front"), self.parts[r].side_box("back")) if b]
            cand = set()
            for i in range(int((min(b[0] for b in bs) - self.reach) // B), int((max(b[2] for b in bs) + self.reach) // B) + 1):
                for j in range(int((min(b[1] for b in bs) - self.reach) // B), int((max(b[3] for b in bs) + self.reach) // B) + 1):
                    cand |= self.buckets.get((i, j), set())
            if r not in self.iso_parts:
                cand &= self.iso_parts
        for q in cand:
            if q == r or q in skip:
                continue
            for a in self.part_pads[r]:
                for b in self.part_pads[q]:
                    v = self._iso_short(a, b)
                    if v > 0:
                        key = (a, b) if (self.order[r], a[1]) < (self.order[q], b[1]) else (b, a)
                        if key not in self.iso_pairs:
                            self.iso_pairs[key] = v
                            self.iso_adj[r].add(key)
                            self.iso_adj[q].add(key)
                            self.t_iso += v
                            if log is not None:
                                log.append(("iso_add", key, v))

    def _decap_len(self, k):
        """Supply distance plus, for the first pair of a cap, the ground return distance."""
        cr, cp, ir, ip, cg, ig = self.decap[k]
        P = self.parts
        ax, ay = P[cr].pad(cp)
        bx, by = P[ir].pad(ip)
        L = math.hypot(ax - bx, ay - by)
        if cg is not None:
            gx, gy = P[cr].pad(cg)
            L += min(math.hypot(gx - x, gy - y) for x, y in (P[ir].pad(n) for n in ig))
        return L

    def _group_cost(self, g):
        grp = self.group_members[g]
        mx = sum(p.x for p in grp) / len(grp)
        my = sum(p.y for p in grp) / len(grp)
        return sum(abs(p.x - mx) + abs(p.y - my) for p in grp)

    def _buckets(self, r):
        p = self.parts[r]
        bs = [b for b in (p.side_box("front"), p.side_box("back")) if b]
        if not bs:
            return ()
        B = self.bucket
        x0, y0 = min(b[0] for b in bs), min(b[1] for b in bs)
        x1, y1 = max(b[2] for b in bs), max(b[3] for b in bs)
        return [(i, j) for i in range(int(x0 // B), int(x1 // B) + 1) for j in range(int(y0 // B), int(y1 // B) + 1)]

    def _init_state(self):
        """Build every cached cost term from the current positions. Totals are unweighted sums per term."""
        P = self.parts
        self._init_grid()
        self.t_cong = 0.0
        self.net_len, self.net_dem = [0.0] * len(self.nets), [None] * len(self.nets)
        for k in range(len(self.nets)):
            self.net_len[k], xs, ys = self._net_len(k)
            if self._net_rudy[k]:
                self.net_dem[k] = self._rudy(xs, ys)
                self._grid_add(*self.net_dem[k], 1)
        self.t_wire = sum(self.net_len)
        self.part_bnd, self.part_edge = {}, {}
        for r in P:
            self.part_bnd[r], self.part_edge[r] = self._part_cost(r)
        self.t_bnd, self.t_edge = sum(self.part_bnd.values()), sum(self.part_edge.values())
        self.dec_len = [self._decap_len(k) for k in range(len(self.decap))]
        self.t_dec = sum(self.dec_len)
        self.sep_val = [self._sep_val(k) for k in range(len(self.seps))]
        self.t_sep = sum(self.sep_val)
        self.grp_cost = {g: self._group_cost(g) for g in self.group_members}
        self.t_grp = sum(self.grp_cost.values())
        self.part_ko = {r: self._ko_cost(r) for r in P}
        self.t_ko = sum(self.part_ko.values())
        self.iso_pairs, self.iso_adj, self.t_iso = {}, {r: set() for r in P}, 0.0
        self.pairs, self.adj, self.t_ov = {}, {r: set() for r in P}, 0.0
        self.buckets, self.part_buckets = {}, {}
        for r in P:
            self.part_buckets[r] = self._buckets(r)
            for bk in self.part_buckets[r]:
                self.buckets.setdefault(bk, set()).add(r)
        for r in P:
            self._pair_scan(r, None)
        for r in P:
            self._iso_scan(r, None)
        self.cor_val, self.cor_zone = [0.0] * len(self.cor), [set() for _ in self.cor]
        for k in range(len(self.cor)):
            self.cor_val[k], _, self.cor_zone[k] = self._cor_eval(k, self.buckets)
        self.t_cor = sum(self.cor_val)

    def _pair_scan(self, r, log, skip=()):
        for q in {q for bk in self.part_buckets[r] for q in self.buckets[bk]}:
            if q == r or q in skip or self._same(r, q):
                continue
            v = _side_overlap(self.parts[r], self.parts[q])
            if v > 0:
                key = (r, q) if self.order[r] < self.order[q] else (q, r)
                if key not in self.pairs:
                    self.pairs[key] = v
                    self.adj[r].add(key)
                    self.adj[q].add(key)
                    self.t_ov += v
                    if log is not None:
                        log.append(("pair_add", key, v))

    def _update(self, moved):
        """Recompute only what the parts in `moved` touch. Returns the new cost; `_undo` reverts it."""
        log = []
        saved = (self.t_wire, self.t_cong, self.t_ov, self.t_bnd, self.t_edge, self.t_dec, self.t_grp, self.t_ko,
                 self.t_iso, self.t_sep, self.t_cor)
        old_bk = {r: self.part_buckets[r] for r in moved} if self.cor else {}
        nets, decs, groups, seps = set(), set(), set(), set()
        for r in moved:
            nets.update(self.part_nets[r])
            decs.update(self.part_decs[r])
            seps.update(self.part_seps[r])
            if self.parts[r].group:
                groups.add(self.parts[r].group)
        for k in nets:
            old_len, old_dem = self.net_len[k], self.net_dem[k]
            self.net_len[k], xs, ys = self._net_len(k)
            self.t_wire += self.net_len[k] - old_len
            if self._net_rudy[k]:
                new_dem = self._rudy(xs, ys)
                if new_dem[:2] != old_dem[:2] or not np.array_equal(new_dem[2], old_dem[2]):
                    self._grid_add(*old_dem, -1)
                    self._grid_add(*new_dem, 1)
                    self.net_dem[k] = new_dem
            log.append(("net", k, old_len, old_dem))
        for r in moved:
            ob, oe = self.part_bnd[r], self.part_edge[r]
            self.part_bnd[r], self.part_edge[r] = self._part_cost(r)
            self.t_bnd += self.part_bnd[r] - ob
            self.t_edge += self.part_edge[r] - oe
            log.append(("part", r, ob, oe))
            if self.ko_fp or self.ko_pad or self.iso_hulls or self.regions:
                ok = self.part_ko[r]
                self.part_ko[r] = self._ko_cost(r)
                self.t_ko += self.part_ko[r] - ok
                log.append(("ko", r, ok))
        for k in decs:
            old = self.dec_len[k]
            self.dec_len[k] = self._decap_len(k)
            self.t_dec += self.dec_len[k] - old
            log.append(("dec", k, old))
        for k in seps:
            old = self.sep_val[k]
            self.sep_val[k] = self._sep_val(k)
            self.t_sep += self.sep_val[k] - old
            log.append(("sep", k, old))
        for g in groups:
            old = self.grp_cost[g]
            self.grp_cost[g] = self._group_cost(g)
            self.t_grp += self.grp_cost[g] - old
            log.append(("grp", g, old))
        for r in moved:  # drop the old pairs and buckets, then rescan
            for key in list(self.iso_adj[r]):
                v = self.iso_pairs.pop(key)
                self.iso_adj[key[0][0]].discard(key)
                self.iso_adj[key[1][0]].discard(key)
                self.t_iso -= v
                log.append(("iso_del", key, v))
            for key in list(self.adj[r]):
                v = self.pairs.pop(key)
                self.adj[key[0]].discard(key)
                self.adj[key[1]].discard(key)
                self.t_ov -= v
                log.append(("pair_del", key, v))
            old_b = self.part_buckets[r]
            for bk in old_b:
                self.buckets[bk].discard(r)
            self.part_buckets[r] = self._buckets(r)
            for bk in self.part_buckets[r]:
                self.buckets.setdefault(bk, set()).add(r)
            log.append(("bucket", r, old_b))
        done = set()
        for r in moved:
            done.add(r)
            self._pair_scan(r, log, skip=done)
        done = set()
        for r in moved:
            done.add(r)
            self._iso_scan(r, log, skip=done)
        if self.cor:  # a corridor changes when a pad of its net moves or a part enters or leaves the cells it looks at
            touched = {bk for r in moved for bk in (*old_bk[r], *self.part_buckets[r])}
            own = {k for r in moved for k in self.cor_part.get(r, ())}
            for k in range(len(self.cor)):
                if k in own or not touched.isdisjoint(self.cor_zone[k]):
                    log.append(("cor", k, self.cor_val[k], self.cor_zone[k]))
                    self.cor_val[k], _, self.cor_zone[k] = self._cor_eval(k, self.buckets)
                    self.t_cor += self.cor_val[k] - log[-1][2]
        self._log, self._saved = log, saved
        return self._total()

    def _undo(self):
        for e in reversed(self._log):
            kind = e[0]
            if kind == "net":
                _, k, ol, od = e
                if self._net_rudy[k] and self.net_dem[k] is not od:
                    self._grid_add(*self.net_dem[k], -1)
                    self._grid_add(*od, 1)
                self.net_len[k], self.net_dem[k] = ol, od
            elif kind == "part":
                self.part_bnd[e[1]], self.part_edge[e[1]] = e[2], e[3]
            elif kind == "ko":
                self.part_ko[e[1]] = e[2]
            elif kind == "iso_add":
                key = e[1]
                del self.iso_pairs[key]
                self.iso_adj[key[0][0]].discard(key)
                self.iso_adj[key[1][0]].discard(key)
            elif kind == "iso_del":
                key = e[1]
                self.iso_pairs[key] = e[2]
                self.iso_adj[key[0][0]].add(key)
                self.iso_adj[key[1][0]].add(key)
            elif kind == "dec":
                self.dec_len[e[1]] = e[2]
            elif kind == "sep":
                self.sep_val[e[1]] = e[2]
            elif kind == "grp":
                self.grp_cost[e[1]] = e[2]
            elif kind == "cor":
                self.cor_val[e[1]], self.cor_zone[e[1]] = e[2], e[3]
            elif kind == "pair_add":
                key = e[1]
                del self.pairs[key]
                self.adj[key[0]].discard(key)
                self.adj[key[1]].discard(key)
            elif kind == "pair_del":
                key = e[1]
                self.pairs[key] = e[2]
                self.adj[key[0]].add(key)
                self.adj[key[1]].add(key)
            elif kind == "bucket":
                r = e[1]
                for bk in self.part_buckets[r]:
                    self.buckets[bk].discard(r)
                self.part_buckets[r] = e[2]
                for bk in e[2]:
                    self.buckets.setdefault(bk, set()).add(r)
        (self.t_wire, self.t_cong, self.t_ov, self.t_bnd, self.t_edge, self.t_dec, self.t_grp, self.t_ko,
         self.t_iso, self.t_sep, self.t_cor) = self._saved

    def _total(self) -> float:
        w = self.wt
        return (w["wire"] * self.t_wire + w["overlap"] * self.t_ov + w["bounds"] * self.t_bnd
                + w["edge"] * self.t_edge + w["decouple"] * self.t_dec + w["group"] * self.t_grp
                + w["congestion"] * self.t_cong + w["keepout"] * self.t_ko + w["isolation"] * self.t_iso
                + self.t_sep + w["corridor"] * self.t_cor)

    def cost(self) -> float:
        """Cost of the current placement; rebuilds the incremental state from scratch."""
        self._init_state()
        return self._total()

    def cost_full(self) -> float:
        """Reference cost, recomputed without the grid, caches or incremental updates (O(n^2) overlap loop)."""
        P = self.parts
        c = 0.0
        for w, conns in self.nets:
            xs, ys = zip(*(P[r].pad(p) for r, p in conns))
            c += self.wt["wire"] * w * ((max(xs) - min(xs)) + (max(ys) - min(ys)))
        refs = list(P)
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                if not self._same(a, b):
                    c += self.wt["overlap"] * _side_overlap(P[a], P[b])
            bnd, edge = self._part_cost(a)
            c += self.wt["bounds"] * bnd + self.wt["edge"] * edge
        for k in range(len(self.decap)):
            c += self.wt["decouple"] * self._decap_len(k)
        for k in range(len(self.seps)):
            c += self._sep_val(k)
        for g in self.group_members.values():
            mx = sum(p.x for p in g) / len(g)
            my = sum(p.y for p in g) / len(g)
            c += self.wt["group"] * sum(abs(p.x - mx) + abs(p.y - my) for p in g)
        for r in P:
            c += self.wt["keepout"] * self._ko_cost(r)
        keys = list(self.pad_grp)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                c += self.wt["isolation"] * self._iso_short(a, b)
        bk = self._bucket_map() if self.cor else {}
        c += self.wt["corridor"] * sum(self._cor_eval(k, bk)[0] for k in range(len(self.cor)))
        return c + self.wt["congestion"] * self.congestion()

    def run(self, iters: int = 30000) -> float:
        movable = self._movable()
        for p in movable:  # a member of a floorplan region starts inside the region's bounding box
            start = self.region_start.get(p.ref) or self.region_start.get(getattr(p, "name", None))
            x0, y0, x1, y1 = start or (self.W * 0.2, self.H * 0.2, self.W * 0.8, self.H * 0.8)
            p.x = self.rng.uniform(x0, x1)
            p.y = self.rng.uniform(y0, y1)
            self._refs(p)
        if not movable:
            return self.cost()
        t0 = max(self.W, self.H) * 2
        from . import asicflow
        if asicflow.on(self.d):  # the layout flow (docs/layout-flow.md)
            return asicflow.place(self, movable, iters, t0)
        # HWFLOW_FLOW=legacy: plain annealing from a random start. Temporary escape hatch, to be removed once two more
        # routed designs confirm the layout flow (docs/layout-flow.md).
        self._anneal(movable, iters, t0, 0.01)
        return self._finish(movable)

    @staticmethod
    def _unit_size(movable) -> float:
        """Typical part size (mm): the square root of the median courtyard area of the movable units."""
        a = sorted(getattr(u, "yard", u).w * getattr(u, "yard", u).h for u in movable) or [1.0]
        return math.sqrt(a[len(a) // 2])

    def _stage(self, name, ts):
        """Record a placement stage: seconds and the proxies (HPWL, RUDY overflow, overlap area of courtyards)."""
        import time
        dt = time.perf_counter() - ts
        wt = dict(self.wt)
        self.cost()
        STAGES.append({"stage": name, "s": round(dt, 2), "hpwl": round(self.t_wire, 1), "overflow": round(self.t_cong, 2),
                       "overlap": round(sum(v for (a, b), v in self.pairs.items()
                                            if not (self._unit(self.parts[a]).fixed and self._unit(self.parts[b]).fixed)), 2)})
        self.wt.update(wt)

    def detailed(self, movable, passes: int = DP_PASSES):
        """Detailed placement in the style of OpenDP: local moves only, greedy acceptance (a move is kept when the
        cost falls). Per unit: shifts of DP_STEPS track pitches in x and y, the three other rotations, a swap with each unit of
        about the same courtyard area within DP_RADIUS typical part sizes; then window reordering: every permutation of three
        same-size units that are neighbours. Passes repeat until nothing improves."""
        import itertools
        cur = self.cost()
        radius = DP_RADIUS * self._unit_size(movable)
        pitch = float(self.d.board["track_mm"]) + float(self.d.board["clearance_mm"])
        steps = [k * pitch for k in DP_STEPS]

        def area(u):
            y = getattr(u, "yard", u)
            return y.w * y.h

        def same(a, b):
            return abs(area(a) - area(b)) <= 0.15 * max(area(a), area(b))

        def near(a, b):
            return abs(a.x - b.x) + abs(a.y - b.y) <= radius

        def trial(units, pos):
            nonlocal cur
            old = [(u.x, u.y, u.r) for u in units]
            for u, (x, y, r) in zip(units, pos):
                u.x, u.y, u.r = x, y, r
            refs = [r for u in units for r in self._refs(u)]
            new = self._update(refs)
            if new < cur - 1e-6:
                cur = new
                return True
            for u, (x, y, r) in zip(units, old):
                u.x, u.y, u.r = x, y, r
                self._refs(u)
            self._undo()
            return False

        for _ in range(passes):
            gain = cur
            for u in movable:
                for d in steps:
                    for dx, dy in ((d, 0), (-d, 0), (0, d), (0, -d)):
                        trial([u], [(min(self.W, max(0.0, u.x + dx)), min(self.H, max(0.0, u.y + dy)), u.r)])
                for dr in (90, 180, 270):
                    trial([u], [(u.x, u.y, (u.r + dr) % 360)])
                for v in movable:
                    if v is not u and same(u, v) and near(u, v):
                        trial([u, v], [(v.x, v.y, u.r), (u.x, u.y, v.r)])
            for u in movable:  # window reordering: three same-size neighbours, every order of their places
                win = [v for v in movable if v is not u and same(u, v) and near(u, v)][:2]
                if len(win) == 2:
                    units = [u] + win
                    spots = [(w.x, w.y) for w in units]
                    for perm in itertools.permutations(range(3)):
                        if perm != (0, 1, 2):
                            trial(units, [(spots[k][0], spots[k][1], w.r) for k, w in zip(perm, units)])
            self.cost()
            cur = self._total()
            if cur > gain - 1e-6 * max(1.0, abs(gain)):
                break
        return cur

    def refine(self, iters: int, t0: float = 0.6) -> float:
        """Short low-temperature anneal from the current placement (weights as the last run left them)."""
        movable = self._movable()
        if not movable or iters <= 0:
            return self.cost()
        self._anneal(movable, iters, t0, 0.01, ramp=False)
        return self._finish(movable)

    def _anneal(self, movable, iters, t0, t1, ramp=True):
        import time
        ts = time.perf_counter()
        try:
            self._anneal_(movable, iters, t0, t1, ramp)
        finally:
            ANNEAL["moves"] += iters
            ANNEAL["s"] += time.perf_counter() - ts

    def _anneal_(self, movable, iters, t0, t1, ramp=True):
        cur = self.cost()
        best, best_state = cur, self._state()
        base = {k: self.wt[k] for k in ("overlap", "bounds", "keepout", "isolation")}
        for i in range(iters):
            t = t0 * (t1 / t0) ** (i / iters)
            if i % 500 == 0:
                if ramp:
                    f = 1 + 20 * i / iters
                    for k, v in base.items():
                        self.wt[k] = v * f
                cur = self.cost()  # also resets the incremental drift
                best, best_state = cur, self._state()  # weights changed: costs are not comparable
            p = self.rng.choice(movable)
            old = (p.x, p.y, p.r)
            mv = self.rng.random()
            other = None
            if mv < 0.75:
                s = max(0.3, t)
                p.x = min(self.W, max(0, p.x + self.rng.gauss(0, s)))
                p.y = min(self.H, max(0, p.y + self.rng.gauss(0, s)))
            elif mv < 0.9:
                p.r = (p.r + self.rng.choice((90, 180, 270))) % 360
            else:
                other = self.rng.choice(movable)
                oold = (other.x, other.y)
                p.x, p.y, other.x, other.y = other.x, other.y, p.x, p.y
            new = self._update(self._refs(p) + (self._refs(other) if other else []))
            if new <= cur or self.rng.random() < math.exp(-(new - cur) / max(t, 1e-9)):
                cur = new
                if cur < best:
                    best, best_state = cur, self._state()
            else:
                p.x, p.y, p.r = old
                self._refs(p)
                if other:
                    other.x, other.y = oold
                    self._refs(other)
                self._undo()
        self._restore(best_state)

    def _finish(self, movable):
        self.legalize()
        before = self._state()
        n0 = len(self._rule_violations()[0])
        for p in movable:  # snap to 0.05 mm grid
            p.x, p.y = round(p.x / 0.05) * 0.05, round(p.y / 0.05) * 0.05
            self._refs(p)
        self.legalize()
        if len(self._rule_violations()[0]) > n0:
            # The snap moved a part up to 0.035 mm into an isolation or keep-out distance that the annealed position met
            # exactly. Which side of the limit the optimum lands on depends on float rounding (the BLAS kernel of the
            # CPU, via the global placement), so keep the unsnapped position rather than a grid position that violates.
            self._restore(before)
            for p in self.parts.values():
                self._refs(p)
        return self.cost()

    def unstick(self, rounds: int = 3, step: float = 1.5, polish: float = 0.25) -> float:
        """Annealing can leave a large part stuck on a violation (two relays nose to nose, a part in a keep-out) when
        every small move costs more than it gains. For each movable part in a violation, try every position on a
        coarse grid in the four rotations and keep the cheapest (HPWL and the other terms count), then polish it on a
        fine grid. Cheap: a few parts, one incremental update per candidate."""
        P = self.parts
        self.cost()
        for _ in range(rounds):
            bad: dict = {}
            for (a, b), v in self.pairs.items():
                bad[a] = bad.get(a, 0.0) + v
                bad[b] = bad.get(b, 0.0) + v
            for (a, b), v in self.iso_pairs.items():
                bad[a[0]] = bad.get(a[0], 0.0) + v
                bad[b[0]] = bad.get(b[0], 0.0) + v
            for r, v in self.part_ko.items():
                if v > 0:
                    bad[r] = bad.get(r, 0.0) + v
            bad = [r for r in sorted(bad, key=bad.get, reverse=True) if not P[r].fixed and bad[r] > 0.01]
            if not bad:
                break
            moved = False
            units = {id(self._unit(P[r])): self._unit(P[r]) for r in bad}  # a block moves as one
            for p in units.values():
                cur = self._total()
                best, here = (cur, p.x, p.y, p.r), (p.x, p.y, p.r)

                def scan(xs, ys, rots):
                    nonlocal best
                    for a in rots:
                        for x in xs:
                            for y in ys:
                                p.x, p.y, p.r = x, y, a
                                c = self._update(self._refs(p))
                                if c < best[0] - 1e-9:
                                    best = (c, x, y, a)
                                self._undo()
                scan([i * step for i in range(int(self.W / step) + 1)], [j * step for j in range(int(self.H / step) + 1)],
                     (0.0, 90.0, 180.0, 270.0))
                scan([best[1] + i * polish for i in range(-6, 7)], [best[2] + j * polish for j in range(-6, 7)], (best[3],))
                p.x, p.y, p.r = best[1], best[2], best[3]
                self._update(self._refs(p))
                if (p.x, p.y, p.r) != here:
                    moved = True
            if not moved:
                break
        return self.cost()

    def legalize(self, rounds: int = 200, refs=None):
        """Push overlapping courtyards apart along the axis of least overlap, then clamp into the board. `refs`: only
        these parts move and only they and the fixed parts count (the macro legalization of asicflow)."""
        P = [p for p in self.parts.values() if refs is None or p.ref in refs or self._unit(p).fixed]
        m = self.d.board["margin_mm"]
        for _ in range(rounds):
            moved = False
            boxes = {p.ref: {s: p.side_box(s) for s in ("front", "back")} for p in P}
            grid: dict = {}
            B = self.bucket
            for i, a in enumerate(P):  # spatial hash, boxes grown by 1 mm for the moves made during the round
                for bx in boxes[a.ref].values():
                    if bx:
                        for gi in range(int((bx[0] - 1) // B), int((bx[2] + 1) // B) + 1):
                            for gj in range(int((bx[1] - 1) // B), int((bx[3] + 1) // B) + 1):
                                grid.setdefault((gi, gj), set()).add(i)
            for i, a in enumerate(P):
                near = set()
                for bx in boxes[a.ref].values():
                    if bx:
                        for gi in range(int((bx[0] - 1) // B), int((bx[2] + 1) // B) + 1):
                            for gj in range(int((bx[1] - 1) // B), int((bx[3] + 1) // B) + 1):
                                near |= grid.get((gi, gj), set())
                for j in sorted(near):
                    if j <= i or self._same(a.ref, P[j].ref):
                        continue
                    b = P[j]
                    pair = None
                    for side in ("front", "back"):  # only courtyards on the same side collide
                        ba, bb = a.side_box(side), b.side_box(side)
                        if ba and bb and _overlap(ba, bb) > 0:
                            pair = (ba, bb)
                            break
                    if pair is None:
                        continue
                    ba, bb = pair
                    ox = min(ba[2], bb[2]) - max(ba[0], bb[0])
                    oy = min(ba[3], bb[3]) - max(ba[1], bb[1])
                    if ox <= 0.001 or oy <= 0.001:
                        continue
                    moved = True
                    mover = b if not b.fixed else a
                    sign_src = (bb[0] + bb[2] - ba[0] - ba[2]) if ox < oy else (bb[1] + bb[3] - ba[1] - ba[3])
                    sgn = 1 if sign_src >= 0 else -1
                    if mover is a:
                        sgn = -sgn
                    if a.fixed and b.fixed:
                        continue
                    if ox < oy:
                        self._shift(mover, sgn * (ox + 0.05), 0.0)
                    else:
                        self._shift(mover, 0.0, sgn * (oy + 0.05))
            for p in P:  # out of the areas that forbid courtyards, along the axis of least penetration
                if p.fixed:
                    continue
                for box, sides, owner, _ in self.ko_fp:
                    for side in sides:
                        b = p.side_box(side)
                        if owner == p.ref or self._same(owner, p.ref) or not b or _overlap(b, box) <= 1e-6:
                            continue
                        moved = True
                        opts = [(box[0] - b[2] - 0.05, 0), (box[2] - b[0] + 0.05, 0),
                                (0, box[1] - b[3] - 0.05), (0, box[3] - b[1] + 0.05)]
                        dx, dy = min(opts, key=lambda o: abs(o[0]) + abs(o[1]))
                        self._shift(p, dx, dy)
            for p in P:
                if p.fixed:
                    continue
                x0, y0, x1, y1 = p.box()
                if x0 < m:
                    self._shift(p, m - x0, 0.0)
                if x1 > self.W - m:
                    self._shift(p, -(x1 - (self.W - m)), 0.0)
                if y0 < m:
                    self._shift(p, 0.0, m - y0)
                if y1 > self.H - m:
                    self._shift(p, 0.0, -(y1 - (self.H - m)))
                for g in self.regions:  # a member of a rectangular floorplan region into its region
                    if p.ref in g["members"] and g["rect"]:
                        c = COURTYARD_MARGIN
                        x0, y0, x1, y1 = p.box()
                        rx0, ry0, rx1, ry1 = g["rect"]
                        self._shift(p, max(0.0, rx0 - x0 - c) - max(0.0, x1 - c - rx1),
                                    max(0.0, ry0 - y0 - c) - max(0.0, y1 - c - ry1))
            if not moved:
                break

    def violations(self) -> list[str]:
        P = self.parts
        out = []
        refs = list(P)
        boxes = {r: P[r].box() for r in refs}
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                # two fixed parts: the design put them there, so only their own courtyards count (KiCad's test);
                # the placer's margin around them is no violation that any placement could repair
                shrink = COURTYARD_MARGIN if P[a].fixed and P[b].fixed else 0.0
                if not self._same(a, b) and _side_overlap(P[a], P[b], shrink) > 0.01:
                    out.append(f"courtyard overlap {a}/{b}")
            # copper must stay inside the board with the copper-to-edge clearance; a courtyard (body)
            # may overhang on purpose, for example a connector flush with the edge
            ce = float(self.d.board.get("copper_edge_mm", 0.3))
            part = P[a]
            for n in part.pads:
                if n in part.nocu:
                    continue
                px, py = part.pad(n)
                w, h = (part.pad_size or {}).get(n, (0.0, 0.0))
                if part.r % 180:
                    w, h = h, w
                if px - w / 2 < ce - 0.01 or py - h / 2 < ce - 0.01 or px + w / 2 > self.W - ce + 0.01 \
                        or py + h / 2 > self.H - ce + 0.01:
                    out.append(f"{a} pad {n} copper closer than {ce} mm to the board edge")
                    break
        mv, fx = self._rule_violations()
        return out + mv + self.corridor_blocks()

    def _rule_violations(self):
        """(movable, fixed-only) keep-out and isolation violations of the current placement. A violation that involves
        only fixed parts cannot be repaired by the placer: it is listed apart and never makes placement fail."""
        P = self.parts
        mv, fx = [], []
        for r in P:
            if self._ko_cost(r) > 0.01:
                yards = [b for b in (P[r].side_box("front"), P[r].side_box("back")) if b]
                pads = [self._pad_box(P[r], k) for k in P[r].pads if k not in P[r].nocu]
                names = [n for box, sides, o, n in self.ko_fp + self.ko_pad if o != r and (
                    any(_overlap(bx, box) > 0 and self._in_area(bx, o, n, courtyard=True) for bx in yards)
                    or any(_overlap(bx, box) > 0 and self._in_area(bx, o, n) for bx in pads))]
                names += [n for hull, refs, n in self.iso_hulls if r not in refs and any(
                    _poly_overlap(b, hull) > 0.01 for b in (P[r].side_box("front"), P[r].side_box("back")) if b)]
                if names:  # the box test is a bound: a round area overlaps its bounding box at the corners only
                    (fx if P[r].fixed else mv).append(f"{r} courtyard or pad in keep-out '{names[0]}'")
                for g in self.regions:
                    out, inside, pads = self._region_cost(P[r], g)
                    for v, what in ((out, "outside"), (inside, "inside"), (pads, "has pads of its nets outside")):
                        if v > 0.01:
                            (fx if P[r].fixed else mv).append(f"{r} {what} floorplan region '{g['name']}' ({v:.1f} mm2)")
        keys = list(self.pad_grp)
        seen = set()
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                v = self._iso_short(a, b)
                if v > 0.01 and (a[0], b[0]) not in seen:
                    seen.add((a[0], b[0]))
                    both_fixed = P[a[0]].fixed and P[b[0]].fixed
                    (fx if both_fixed else mv).append(f"isolation {a[0]}.{a[1]}/{b[0]}.{b[1]}: {v:.2f} mm short "
                                                      f"({self._iso_rule(a, b)})")
        return mv, fx

    def fixed_violations(self) -> list[str]:
        """Keep-out and isolation violations caused only by fixed parts (not moved, not counted as failures)."""
        return self._rule_violations()[1]

    def _state(self):
        return {r: (p.x, p.y, p.r) for r, p in self.parts.items()}

    def _restore(self, s):
        for r, (x, y, a) in s.items():
            self.parts[r].x, self.parts[r].y, self.parts[r].r = x, y, a
        for u in self.units:  # the courtyard part holds the block origin
            u.x, u.y, u.r = s[u.ref]


def place(design: Design, libs, seeds: int = 4, iters: int = 30000, weights=None, seed0: int = 1,
          refine: float = REFINE):
    """Best of several seeds (seed0 .. seed0 + seeds - 1), then a short low-temperature refine pass of
    `refine * iters` moves on the best one (taken out of the seeds' budget). Returns (parts, cost, violations)."""
    b = design.board
    best = None
    extra = int(iters * refine)  # the refine pass is paid for by shortening the seeds, so total moves stay the same
    iters -= extra // seeds
    for seed in range(seed0, seed0 + seeds):
        parts = build_parts(design, libs)
        pl = Placer(design, parts, b["width_mm"], b["height_mm"], seed=seed, weights=weights)
        cost = pl.run(iters)
        v = pl.violations()
        key = (len(v), cost)
        if best is None or key < best[0]:
            best = (key, parts, cost, v, pl)
    key, parts, cost, v, pl = best
    for c in pl.corridors():
        print(f"[place] corridor {c['msg']}")
    for f in pl.fixed_violations():
        if f not in _REPORTED:
            _REPORTED.add(f)
            print(f"[place] fixed parts: {f}")
    if v and any(not p.fixed for p in parts.values()):  # a violation left by the annealing: search it away
        before = pl._state()
        cost2 = pl.unstick()
        v2 = pl.violations()
        if (len(v2), cost2) <= key:
            cost, v, key = cost2, v2, (len(v2), cost2)
        else:
            pl._restore(before)
    from . import asicflow
    if asicflow.on(design):  # the layout flow has its own short anneal: no refine pass
        refine = 0
    if refine > 0 and any(not p.fixed for p in parts.values()):
        before = pl._state()
        cost2 = pl.refine(extra)
        v2 = pl.violations()
        if (len(v2), cost2) <= key:
            cost, v = cost2, v2
        else:
            pl._restore(before)
    return parts, cost, v
