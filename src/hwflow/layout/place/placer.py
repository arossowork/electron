"""Simulated-annealing placement with net length, decoupling, grouping and edge constraints."""

from __future__ import annotations

import math
import random

import numpy as np

from ...core.design import Design
from ...core.geometry import COURTYARD_MARGIN, Part, Parts, Rigid, rot  # noqa: F401  (re-exported)
from ...core.settings import get_settings


def build_parts(design: Design, libs, margin: float = COURTYARD_MARGIN, areas=None) -> dict[str, Part]:
    """`areas`: more rule areas for the placer (roads.areas: the road reservations)."""
    parts = Parts()
    from ..board import keepout
    parts.areas = keepout.placement_areas(design, libs) + list(areas or [])
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
                    cu=cu, round=frozenset(rnd),
                    npth=tuple((p["x"], p["y"], *((p["w"], p["h"]) if p["rot"] % 180 == 0 else (p["h"], p["w"])))
                               for p in fp["pads"] if p["kind"] == "np_thru_hole"))
        if "x" in pl and "y" in pl:
            part.x, part.y, part.r = float(pl["x"]), float(pl["y"]), float(pl.get("rot", 0))
            part.fixed = bool(pl.get("fixed", True))
        elif "rot" in pl:
            part.r = float(pl["rot"])
        parts[c.ref] = part
    from ...core import blocks
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
        if (a.tht or a.npth) and b.sides:
            for pad in hole_boxes(a):
                total += sum(_overlap(pad, c) for c in (yard(b, "front"), yard(b, "back")) if c)
    return total


def hole_boxes(p: "Part") -> list:
    """Boxes of everything of part `p` that goes through the board: its plated through-hole pads (the pad) and its
    non-plated holes (mounting pegs): no courtyard of another part on either side may cover them."""
    return [part_pad_box(p, n) for n in p.tht] + p.npth_boxes()


def part_pad_box(p: "Part", n):
    x, y = p.pad(n)
    w, h = (p.pad_size or {}).get(n, (0.0, 0.0))
    if p.r % 180:
        w, h = h, w
    return x - w / 2, y - h / 2, x + w / 2, y + h / 2


def _tht_pair(pa: "Part", pb: "Part"):
    """(box of pa, box of pb) of the deepest through-hole pad of one part inside a courtyard of the other (on either
    side), or None: the overlap `_side_overlap` counts across sides, as boxes `legalize` can push apart."""
    best = None
    for a, b, flip in ((pa, pb, False), (pb, pa, True)):
        if not (a.tht or a.npth) or not b.sides:
            continue
        yards = [y for y in (b.side_box("front"), b.side_box("back")) if y]
        for pad in hole_boxes(a):
            for y in yards:
                o = _overlap(pad, y)
                if o > 0 and (best is None or o > best[0]):
                    best = (o, (y, pad) if flip else (pad, y))
    return best[1] if best else None


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
BLOCK_CU = "~cu"  # pad-name prefix of a block's copper on its courtyard part (Placer._block_copper_pads)


def block_copper_on() -> bool:
    return bool(get_settings().layout.place_block_copper)


BUDGET_FILL = 0.7  # a copper_len budget leg's soft pad distance limit in placement, as a share of its max_mm (detour)
BUDGET_HARD_W = 400.0  # per mm of straight-line pad distance past a copper_len budget leg's max_mm (the isolation weight)
FIXED_LEG_RISK = "within max_mm itself"  # floorplan._fixed_severity: a fixed leg with this note is a risk
BUDGET_TOL = 0.01  # mm: a budget leg this much over its limit is a placement violation


def budgets_on() -> bool:
    return bool(get_settings().layout.place_budgets)


def decap_hard_on() -> bool:
    return bool(get_settings().layout.place_decap_hard)


EDGE_TOL = 1.0  # mm: a movable part with placement.edge whose courtyard is farther from that edge is a violation
SEP_HARD_KEYS = {"antenna": "antenna_clearance_mm", "thermal": "thermal_clearance_mm"}  # hard when the board states them


def edge_hard_on() -> bool:
    return bool(get_settings().layout.place_edge_hard)


def sep_hard_on() -> bool:
    return bool(get_settings().layout.place_sep_hard)


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
UNSTICK_POINTS = 1500  # unstick: most grid points per rotation (a full-board 1.5 mm scan of a 440 mm board took hours)
UNSTICK_UNITS = 50  # unstick: most parts moved per round, worst violation first
SETTLE_CELLS = 4e6    # Placer.settle: occupancy raster cells over the board (cell side 0.05-0.25 mm)
SETTLE_TURN_MM = 0.5  # Placer.settle: a rotated spot must be this much nearer than one in the current rotation
PACK_UNITS = 5        # Placer.settle: the largest units of a tight spot whose put-back order the packing search permutes
PACK_EVALS = 240      # Placer.settle: at most this many orders x rotations tried there (it stops at the first legal one)
PACK_S = 45.0         # Placer.settle: and at most this many seconds
SETTLE_WINDOW = 4.0   # Placer.settle: first search a window of this many unit sizes around the unit, then the board


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
                   "corridor": 30.0, "hotspot": 0.0}
        self.wt.update(weights or {})
        self.hot = None  # hotspot.HotMap: the routability window cuts as a local penalty (hotspot.py; off: None)
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
        self.dec_lim = [None] * len(self.decap)  # per pair: the copper_len budget limit (mm) of a budget leg, else None
        self._init_budget_pairs()
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
        from . import escape
        self.esc = escape.Escape(self) if escape.on(design) else None  # escape slots (prototype, off by default)
        self.rot_ok: dict = {}  # ref -> allowed rotations (escape.forward, step 3); absent: all four
        if self.esc is not None:
            self.wt.setdefault("escape", get_settings().layout.place_escape_w)
        # signal pads per component for the hotspot term: (pad, net) with copper, not on a poured net
        pour = gnds if design.board["gnd_pour"] else set()
        self.sig_pads = {}
        for r, p in parts.items():
            c = design.components.get(r)
            if c is not None:
                self.sig_pads[r] = [n for n, net in c.pins.items() if n in p.pads and n not in p.nocu
                                    and net and net not in pour]

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
            from ..board import pcb
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
        # a distance the design states (board.antenna_clearance_mm, board.thermal_clearance_mm written in
        # design.yaml) is a hard rule: the isolation weight and a placement violation; the defaults stay soft
        stated = d.raw.get("board") or {}
        self.sep_hard = {k for k, e in enumerate(self.seps)
                         if sep_hard_on() and e[5] in SEP_HARD_KEYS and SEP_HARD_KEYS[e[5]] in stated}
        for key in {self.seps[k][5] for k in self.sep_hard}:
            self.wt[key] = max(self.wt[key], self.wt["isolation"])
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
        self.ko_exempt = {}  # (owner, name) -> refs the area does not apply to (roads.areas: the road's own parts)
        for a in areas:
            if a.get("exempt"):
                self.ko_exempt[(a["owner"], a["name"])] = frozenset(a["exempt"])
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
        from ..board import pcb
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
        if (gid or cls) and self.units and block_copper_on():
            self._block_copper_pads(gid, cls)
        # The area enclosed by an isolation group's pads, where its tracks run: a part without a pad of the group
        # stays out of it, or every route to it crosses the isolation distance. Only for a group whose parts are all
        # fixed (the area is then known before placement).
        self.iso_hulls = []
        from shapely.geometry import MultiPoint
        for gi in range(len(self.iso_need)):
            keys = [k for k, g in self.pad_grp.items() if g == gi]
            refs = {k[0] for k in keys}
            if refs and all(self.parts[r].fixed for r in refs):
                pts = [c for k in keys for c in _corners(part_pad_box(self.parts[k[0]], k[1]))]
                hull = MultiPoint(pts).convex_hull
                if hull.area > 1.0:
                    self.iso_hulls.append((hull, refs, f"isolation group {gi + 1} area"))
        if not self.iso_parts:
            self.pad_grp = {}
        self.reach = max(self.iso_need + self.iso_between + list(cls.values()) + [0.0]) + 1.0
        self.part_pads = {r: [k for k in self.pad_grp if k[0] == r] for r in self.parts} if self.pad_grp else {}
        from ...core import floorplan
        self.regions, self.region_start = floorplan.placer_regions(self.d, self.parts)  # design.yaml floorplan.regions

    def _block_copper_pads(self, gid, cls):
        """The copper of each layout block (port stubs, internal tracks, vias) as copper pads of the block's
        courtyard part, so the isolation and net-class terms keep it the distance a pad keeps. The router gets this
        copper as pre-existing (blocks.copper): before this, a block's 1.45 mm port stub could sit 5.0 mm from a mains
        pad that the block's own pads cleared by 6 mm."""
        from ...core import blocks
        via = float(self.d.board.get("via_dia_mm", 0.6))
        for u in self.units:
            yard = self.parts.get(u.ref)
            if yard is None:
                continue
            boxes = blocks.local_copper(self.d, u.name, via)
            if not boxes:
                continue
            yard.pads = dict(yard.pads)
            yard.pad_size = dict(yard.pad_size or {})
            for k, (net, (x0, y0, x1, y1)) in enumerate(boxes):
                n = f"{BLOCK_CU}{k}"
                yard.pads[n] = ((x0 + x1) / 2, (y0 + y1) / 2)
                yard.pad_size[n] = (x1 - x0, y1 - y0)
                self.pad_grp[(u.ref, n)], self.pad_net[(u.ref, n)] = gid.get(net, -1), net
                self.pad_cl[(u.ref, n)] = cls.get(net, 0.0)
                if net in gid or net in cls:
                    self.iso_parts.add(u.ref)

    # ---- routing corridors of the constrained nets ----------------------------------------------------------------

    def _init_corridors(self):
        """Constrained nets: the members of a board.isolation group (or a drc_rules isolation pattern), the nets with
        `layers` and the nets of a floorplan region with `nets`. self.cor: (net, pads, track width, layers it may use,
        static obstacles [(polygon, spacing, name)]): the conductive keep-outs its region keeps the isolation from and
        the rule areas without tracks on every layer it may use."""
        from ...core import floorplan
        from ..board import pcb
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
                obs.append((self.part_pad_box(pq, pn), pn in pq.round, need, f"{q}.{pn} {m or 'no net'} {need:g} mm"))
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
            from ...core.libs import Libs
            libs = Libs(design=self.d)
        model = corridor.Model(self.d, libs, parts=self.parts, use=set(fixed))
        out = []
        for n in nets:
            nodes = corridor.corridor_nodes(model, self.d, n, {})
            if nodes is None or len(nodes) < 2:
                continue
            w = model.width(n)
            if corridor.corridor_connects(model, n, nodes, w, w > model.floor + corridor.EPS):
                continue
            f = corridor.corridor_finding(model, self.d, n, nodes, w, fixed_only=True)
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
            b = part_pad_box(p, n)
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

    def _hot_cost(self, r):
        """The hotspot map summed at the signal pads of `r` (0 without a map)."""
        if self.hot is None:
            return 0.0
        p = self.parts[r]
        return sum(self.hot.at(*p.pad(n), r) for n in self.sig_pads.get(r, ()))

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
        for box, sides, owner, name in self.ko_fp:
            if not self._ko_skip(owner, name, r):
                for s in sides:
                    b = p.side_box(s)
                    if b:
                        c += _overlap(b, box)
        for box, sides, owner, name in self.ko_pad:
            if not self._ko_skip(owner, name, r) and sides & set(p.sides or ()):
                for n in p.pads:
                    if n not in p.nocu:
                        c += _overlap(self.part_pad_box(p, n), box)
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

    def _region_cost(self, p, g, board_rect=None) -> tuple[float, float, float]:
        """(courtyard area of a member outside its region, courtyard area of another part inside an exclusive region on
        its sides, REGION_PAD_W x pad area of the region's nets outside it). Courtyards without the placer margin."""
        m = COURTYARD_MARGIN
        out = inside = pads = 0.0
        if p.ref in g["members"]:
            for b in (p.side_box("front"), p.side_box("back")):
                if b:
                    b = (b[0] + m, b[1] + m, b[2] - m, b[3] - m)
                    if board_rect is not None:
                        # Floorplan checks permit fixed connector bodies to overhang
                        # their board. Copper and on-board region limits still apply.
                        b = (max(b[0], board_rect[0]), max(b[1], board_rect[1]),
                             min(b[2], board_rect[2]), min(b[3], board_rect[3]))
                        if b[2] <= b[0] or b[3] <= b[1]:
                            continue
                    out += (b[2] - b[0]) * (b[3] - b[1]) - self._in_region(b, g)
        elif g["excl"] and p.ref not in g["allow"]:
            for s in g["sides"]:
                b = p.side_box(s)
                if b:
                    inside += self._in_region((b[0] + m, b[1] + m, b[2] - m, b[3] - m), g)
        for n in g["pads"].get(p.ref, ()):  # a pad outside is an isolation error: weighed like 10 times its area
            b = part_pad_box(p, n)
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

    def _ko_skip(self, owner, name, r) -> bool:
        """Area (owner, name) does not apply to part r: its own area, one of its block, or an exempt part."""
        return owner == r or self._same(owner, r) or r in self.ko_exempt.get((owner, name), ())

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
        return [b for b in (p.side_box("front"), p.side_box("back")) if b] + [self.part_pad_box(p, n) for n in p.pads]

    part_pad_box = staticmethod(part_pad_box)

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
        ba, bb = self.part_pad_box(self.parts[a[0]], a[1]), self.part_pad_box(self.parts[b[0]], b[1])
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

    def _init_budget_pairs(self):
        """Distance limits the design states between two pads, as hard placement constraints (`self.bud`):

        - copper_len budgets (design.yaml budgets:, kind copper_len, max_mm): each `pad -> pad` leg with its share
          of max_mm (max_mm / legs);
        - decoupling (`decouples: <IC>`, with place_decap_hard): each supply pad of the capacitor to the nearest IC
          pad on that net, within the component's or the board's `decap_max_mm` (postlayout.decap_paths checks the
          routed copper against the same number).

        The Manhattan pad distance is a lower bound on the routed copper, so a leg farther apart than its limit can
        never meet it:

        - hard: past the limit over the routing allowance place_budget_detour (straight-line pad distance; the routed
          copper is longer by the route's detour), each mm costs BUDGET_HARD_W (the isolation weight) and the leg is a
          placement violation (`_rule_violations`): unstick and settle repair it, the legality gate never routes it;
        - soft (copper_len only; a decoupling pair already pulls its pads): past BUDGET_FILL of the share, each mm
          costs place_budget_w, and the leg pulls its pads together like a decoupling pair (in the global placement
          too, gplace.BUDGET_GAIN);
        - seed: after the global placement the small movable end goes next to its anchor pad (`budget_seed`).

        Before this the budget was only measured after routing, so a TVS that the netlist ties to its connector by a
        ground pad only drifted to its group centre (thermo-e2e-1: 17-20 mm from the terminal against 5 mm in every
        attempt). self.bud: (name, ref a, pad a, [(ref b, pad b)], limit) of every leg on the board, fixed or not; the
        distance is to the nearest b pad. Legs between two fixed parts are only reported (fixed violations, which fail
        the floorplan gate), against the same limit with the routing allowance; legs inside one block against the
        limit itself."""
        self._bud_w = float(get_settings().layout.place_budget_w)
        detour = float(get_settings().layout.place_budget_detour)
        self._bud_detour = detour
        self.bud = []
        if not budgets_on():
            return
        P = self.parts

        def leg(name, ra, pa, bs, lim, pull):
            bs = [(rb, pb) for rb, pb in bs if rb in P and pb in P[rb].pads and rb != ra]
            if ra not in P or pa not in P[ra].pads or not bs:
                return
            rb = bs[0][0]
            if not pull and (self._same(ra, rb) or (P[ra].fixed and P[rb].fixed)):
                return  # a decoupling cap the placement cannot move: postlayout.decap_paths warns on the copper
            if self._same(ra, rb):  # a block's own geometry (its copper is routed): only reported, at the limit
                self.bud.append((name, ra, pa, bs, lim))
                return
            hard = lim / max(detour, 1.0)  # room for the route's detour: a placement that passes it routes in
            self.bud.append((name, ra, pa, bs, hard))
            if P[ra].fixed and P[rb].fixed:  # two fixed ends: only reported (a fixed violation, the floorplan
                return  # gate fails it), with the same allowance: thermo-e2e-1 fixed D2 4.5 mm from J2 routed 5.8
            self.decap.append((ra, pa, rb, bs[0][1], None, []))
            self.dec_lim.append({"soft": min(lim * BUDGET_FILL, hard) if pull else math.inf, "hard": hard,
                                 "alt": bs, "base": pull})

        if self.d.raw.get("budgets"):
            from ...sim import budgets as bmod
            for bd in bmod.budgets(self.d):
                if bd.get("kind", "copper_r") != "copper_len" or bd.get("max_mm") is None:
                    continue
                lg, bad = bmod.legs(self.d, bd)
                lg = [x for x in lg if x["a"] and x["b"]]
                if bad or not lg:
                    continue
                share = float(bd["max_mm"]) / len(lg)
                for x in lg:
                    (ra, pa), (rb, pb) = x["a"][0], x["b"][0]
                    leg(bd["name"], ra, pa, [(rb, pb)], share, True)
        if decap_hard_on():
            gnds = self.d.ground_nets()
            bmax = float(self.d.board.get("decap_max_mm", 4.0))
            for p in list(P.values()):
                ic, c = p.decouples, self.d.components.get(p.ref)
                if not ic or ic not in P or c is None or ic not in self.d.components:
                    continue
                lim = float(c.get("decap_max_mm", bmax))
                icc = self.d.components[ic]
                for cpin, net in c.pins.items():
                    if not net or net in gnds:
                        continue
                    bs = [(ic, ipin) for ipin, inet in icc.pins.items() if inet == net]
                    if bs:
                        leg(f"decap_max_mm {p.ref}", p.ref, cpin, bs, lim, False)

    def _bud_dist(self, k) -> float:
        """Straight-line distance from pad a of leg self.bud[k] to its nearest b pad: the lower bound on its routed
        copper (sim/budgets.measure measures pad centre to pad centre; a 45-degree track is shorter than the Manhattan
        distance: thermo-e2e-1 fuse_stub routed 9.31 mm between pads 10.34 mm apart in Manhattan)."""
        _, ra, pa, bs, _ = self.bud[k]
        ax, ay = self.parts[ra].pad(pa)
        return min(math.hypot(ax - x, ay - y) for x, y in (self.parts[rb].pad(pb) for rb, pb in bs))

    def _bud_near(self, k):
        """(ref, pad) of the b pad of leg k nearest to its pad a."""
        _, ra, pa, bs, _ = self.bud[k]
        ax, ay = self.parts[ra].pad(pa)
        return min(bs, key=lambda e: math.hypot(self.parts[e[0]].pad(e[1])[0] - ax,
                                                self.parts[e[0]].pad(e[1])[1] - ay))

    def budget_violations(self) -> list[tuple[bool, str]]:
        """(both ends fixed or in one block, message) of every leg of self.bud whose pads are farther apart
        (straight line, a lower bound on the routed copper) than its placement limit."""
        out = []
        for k, (name, ra, pa, _, share) in enumerate(self.bud):
            d = self._bud_dist(k)
            if d > share + BUDGET_TOL:
                rb, pb = self._bud_near(k)
                out.append(((self.parts[ra].fixed and self.parts[rb].fixed) or self._same(ra, rb),
                            f"budget {name}: {ra}.{pa} -> {rb}.{pb} pads {d:.2f} mm apart (straight line), over its "
                            f"{share:.2f} mm placement limit (max_mm share or decap_max_mm / routing allowance "
                            f"{self._bud_detour:g}, except inside one block): the routed copper would not meet the "
                            f"budget" + self._fixed_leg_note(ra, rb, d, share)))
        return out

    def _fixed_leg_note(self, ra, rb, d, lim) -> str:
        """The tail of a fixed leg's message: a risk when the pads are within max_mm itself (a straight route still
        meets it: thermo-e2e-1 fuse_stub routed 9.31 mm on pads 9.31 mm apart), else a failure."""
        if not (self.parts[ra].fixed and self.parts[rb].fixed):
            return ""
        if d <= lim * max(self._bud_detour, 1.0) + BUDGET_TOL:
            return (f" (both ends fixed; {FIXED_LEG_RISK}: only a near-straight route meets it, move them closer in "
                    f"design.yaml or check the routed length)")
        return " (both ends fixed, past max_mm itself: move them closer in design.yaml or change the budget)"

    def _bud_mover(self, k):
        """The unit that moves to repair budget leg k: the movable end, the smaller one when both are movable."""
        ra, rb = self.bud[k][1], self.bud[k][3][0][0]
        if self._same(ra, rb):
            return None
        us = [self._unit(self.parts[r]) for r in (ra, rb)]
        us = [u for u in us if not u.fixed]
        if not us:
            return None
        return min(us, key=lambda u: getattr(u, "yard", u).w * getattr(u, "yard", u).h)

    def budget_seed(self, only_violating: bool = True) -> list:
        """Put the movable end of each copper_len budget leg next to its anchor pad: the unit is moved so that its pad
        lands on the anchor pad, pushed out along the anchor's courtyard by the smaller courtyard half; legalize or
        settle then find the free spot nearest to there. Returns the units moved."""
        moved = []
        for k, (_, ra, pa, _, share) in enumerate(self.bud):
            if only_violating and self._bud_dist(k) <= share + BUDGET_TOL:
                continue
            u = self._bud_mover(k)
            if u is None:
                continue
            rb, pb = self._bud_near(k)
            mine = ra if ra in self._refs(u) else rb
            if mine not in self._refs(u):
                continue
            other, opad = (rb, pb) if mine == ra else (ra, pa)
            mpad = pa if mine == ra else pb
            ax, ay = self.parts[other].pad(opad)
            px, py = self.parts[mine].pad(mpad)
            # step out of the anchor's courtyard toward the board centre, on the axis with more room
            ob = self.parts[other].box()
            cx, cy = self.W / 2, self.H / 2
            dx = (ob[2] - ax if cx > ax else ob[0] - ax)
            dy = (ob[3] - ay if cy > ay else ob[1] - ay)
            tx, ty = (ax + dx, ay) if abs(dx) <= abs(dy) else (ax, ay + dy)
            u.x += tx - px
            u.y += ty - py
            if hasattr(u, "sync"):
                u.sync(self.parts)
            moved.append(u)
        if moved:
            self.cost()
        return moved

    def _decap_len(self, k):
        """Supply distance plus, for the first pair of a cap, the ground return distance. A distance leg
        (`_init_budget_pairs`) adds place_budget_w per mm of Manhattan distance past its soft limit and BUDGET_HARD_W
        per mm of straight-line distance to its nearest b pad past its hard limit (both over the decoupling weight,
        the term's own weight); a decoupling-limit leg (`base` False) has only the hard part."""
        cr, cp, ir, ip, cg, ig = self.decap[k]
        P = self.parts
        ax, ay = P[cr].pad(cp)
        bx, by = P[ir].pad(ip)
        L = math.hypot(ax - bx, ay - by)
        lim = self.dec_lim[k] if k < len(self.dec_lim) else None
        if lim is not None:
            wd = max(self.wt["decouple"], 1e-9)
            h = L if len(lim["alt"]) == 1 else min(math.hypot(ax - x, ay - y)
                                                   for x, y in (P[r].pad(n) for r, n in lim["alt"]))
            # soft: Manhattan (room for the route); hard: the straight line to the nearest b pad
            pen = max(0.0, abs(ax - bx) + abs(ay - by) - lim["soft"]) * self._bud_w \
                + max(0.0, h - lim["hard"]) * BUDGET_HARD_W
            L = (L if lim["base"] else 0.0) + pen / wd
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
        self.part_esc, self.t_esc = {}, 0.0
        if self.esc is not None:
            self.esc.move(self.esc.refs)
            self.part_esc = {r: self.esc.part_cost(r) for r in self.esc.refs}
            self.t_esc = sum(self.part_esc.values())
        self.part_hot = {r: self._hot_cost(r) for r in P} if self.hot is not None else {}
        self.t_hot = sum(self.part_hot.values())

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
                 self.t_iso, self.t_sep, self.t_cor, self.t_esc, self.t_hot)
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
            if self.hot is not None:
                oh = self.part_hot.get(r, 0.0)
                self.part_hot[r] = self._hot_cost(r)
                self.t_hot += self.part_hot[r] - oh
                log.append(("hot", r, oh))
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
        if self.esc is not None:
            log.append(("esc_pos", self.esc.save(moved)))
            for r in self.esc.move(moved):
                old = self.part_esc[r]
                self.part_esc[r] = self.esc.part_cost(r)
                self.t_esc += self.part_esc[r] - old
                log.append(("esc", r, old))
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
            elif kind == "hot":
                self.part_hot[e[1]] = e[2]
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
            elif kind == "esc":
                self.part_esc[e[1]] = e[2]
            elif kind == "esc_pos":
                self.esc.load(e[1])
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
         self.t_iso, self.t_sep, self.t_cor, self.t_esc, self.t_hot) = self._saved

    def _total(self) -> float:
        w = self.wt
        return (w["wire"] * self.t_wire + w["overlap"] * self.t_ov + w["bounds"] * self.t_bnd
                + w["edge"] * self.t_edge + w["decouple"] * self.t_dec + w["group"] * self.t_grp
                + w["congestion"] * self.t_cong + w["keepout"] * self.t_ko + w["isolation"] * self.t_iso
                + self.t_sep + w["corridor"] * self.t_cor + w.get("escape", 0.0) * self.t_esc
                + w["hotspot"] * self.t_hot)

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
        if self.esc is not None:
            self.esc.move(self.esc.refs)
            c += self.wt.get("escape", 0.0) * sum(self.esc.part_cost(r) for r in self.esc.refs)
        c += self.wt["hotspot"] * sum(self._hot_cost(r) for r in P)
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
                    if self._rot_ok(u, (u.r + dr) % 360):
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
                if not self._rot_ok(p, p.r):
                    p.r = old[2]
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

    def _rot_ok(self, u, r) -> bool:
        ok = self.rot_ok.get(getattr(u, "ref", None))
        return ok is None or (r % 360) in ok

    def _finish(self, movable):
        self.legalize()
        before = self._state()
        n0 = len(self._rule_violations()[0])
        self.cost()
        for p in movable:  # snap to 0.05 mm grid only when hard constraints do not worsen
            old = (p.x, p.y)
            before_costs = (self.t_ov, self.t_bnd, self.t_ko, self.t_iso)
            p.x, p.y = round(p.x / 0.05) * 0.05, round(p.y / 0.05) * 0.05
            self._update(self._refs(p))
            after_costs = (self.t_ov, self.t_bnd, self.t_ko, self.t_iso)
            if any(a > b + 1e-9 for a, b in zip(after_costs, before_costs)):
                p.x, p.y = old
                self._refs(p)
                self._undo()
        self.legalize()
        if len(self._rule_violations()[0]) > n0:
            # The snap moved a part up to 0.035 mm into an isolation or keep-out distance that the annealed position met
            # exactly. Which side of the limit the optimum lands on depends on float rounding (the BLAS kernel of the
            # CPU, via the global placement), so keep the unsnapped position rather than a grid position that violates.
            self._restore(before)
            for p in self.parts.values():
                self._refs(p)
        # no settle here: place() settles the best seed after unstick (unstick from a settled seed ends worse on an
        # over-full board: synth60 x0.45, 44 violations against 18)
        return self.cost()

    def unstick(self, rounds: int = 3, step: float = 1.5, polish: float = 0.25, deadline: float | None = None) -> float:
        """Annealing can leave a large part stuck on a violation (two relays nose to nose, a part in a keep-out) when
        every small move costs more than it gains. For each movable part in a violation, try every position on a
        coarse grid in the four rotations and keep the cheapest (HPWL and the other terms count), then polish it on a
        fine grid. The grid covers the part's floorplan region (else the board) with at most UNSTICK_POINTS points per
        rotation (a board up to ~58 x 58 mm keeps the 1.5 mm step); the UNSTICK_UNITS worst parts per round; `deadline`
        (time.time()) ends it early. One incremental update per candidate."""
        import time
        P = self.parts
        self.cost()
        for _ in range(rounds):
            if deadline is not None and time.time() >= deadline:
                break
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
            for k in self.sep_hard:  # a stated separation short: both ends (fixed ones are dropped below)
                v = self._sep_val(k)
                if v > 0.01:
                    for r in (self.seps[k][0], self.seps[k][2]):
                        bad[r] = bad.get(r, 0.0) + v
            if edge_hard_on():
                for r, p in P.items():
                    if p.edge and self.part_edge.get(r, 0.0) > EDGE_TOL:
                        bad[r] = bad.get(r, 0.0) + self.part_edge[r]
            for k in range(len(self.bud)):  # a distance leg over its limit: its movable end
                over = self._bud_dist(k) - self.bud[k][4]
                u = self._bud_mover(k) if over > BUDGET_TOL else None
                if u is not None:
                    r = u.ref
                    bad[r] = bad.get(r, 0.0) + over
            # worst first, fixed parts never (a fixed part's pairs and keep-out costs count, but it does not move)
            bad = [r for r in sorted(bad, key=bad.get, reverse=True)
                   if r in P and not P[r].fixed and not self._unit(P[r]).fixed and bad[r] > 0.01]
            if not bad:
                break
            moved = False
            units = {id(self._unit(P[r])): self._unit(P[r]) for r in bad}  # a block moves as one
            for p in list(units.values())[:UNSTICK_UNITS]:  # worst first
                if deadline is not None and time.time() >= deadline:
                    break
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
                x0, y0, x1, y1 = (self.region_start.get(p.ref) or self.region_start.get(getattr(p, "name", None))
                                  or (0.0, 0.0, self.W, self.H))
                st = max(step, math.sqrt((x1 - x0) * (y1 - y0) / UNSTICK_POINTS))
                scan([x0 + i * st for i in range(int((x1 - x0) / st) + 1)],
                     [y0 + j * st for j in range(int((y1 - y0) / st) + 1)],
                     tuple(a for a in (0.0, 90.0, 180.0, 270.0) if self._rot_ok(p, a)) or (p.r,))
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
        these parts move and only they and the fixed parts count (the macro legalization of asicflow). A courtyard is
        also pushed off what goes through the board under it from a part on the other side (through-hole pads,
        non-plated holes: an overlap `violations` counts)."""
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
                    if pair is None:  # a through-hole pad of one inside a courtyard of the other, on
                        pair = _tht_pair(a, b)  # either side (violations counts it: _side_overlap, pth_inside_courtyard)
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
                for box, sides, owner, name in self.ko_fp:
                    for side in sides:
                        b = p.side_box(side)
                        if self._ko_skip(owner, name, p.ref) or not b or _overlap(b, box) <= 1e-6:
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

    def _violators(self, viol) -> list:
        """The movable units to take out and put back for the violations `viol` (strings of `violations`): of a
        courtyard overlap or an isolation short the smaller movable unit, else the unit of the part named first."""
        import re
        P = self.parts
        out, seen = [], set()

        def add(u):
            if not u.fixed and id(u) not in seen:
                seen.add(id(u))
                out.append(u)

        def area(u):
            y = getattr(u, "yard", u)
            return y.w * y.h
        for v in viol:
            m = re.match(r"courtyard overlap (\S+)/(\S+)$", v) or re.match(r"isolation ([^.\s]+)\.\S*/([^.\s]+)\.", v)
            if m:
                us = [self._unit(P[r]) for r in m.groups() if r in P]
                us = [u for u in us if not u.fixed]
                if us:
                    add(min(us, key=area))
                continue
            m = re.match(r"separation (\S+)/(\S+): ", v)
            if m:
                us = [self._unit(P[r]) for r in m.groups() if r in P]
                us = [u for u in us if not u.fixed]
                if us:
                    add(min(us, key=area))
                continue
            m = re.match(r"budget (.+?): (\S+?)\.\S+ -> (\S+?)\.\S+ pads ", v)
            if m:
                k = next((i for i, b in enumerate(self.bud) if b[0] == m.group(1) and b[1] == m.group(2)
                          and any(r == m.group(3) for r, _ in b[3])), None)
                u = self._bud_mover(k) if k is not None else None
                if u is not None:
                    add(u)
                continue
            m = re.match(r"(\S+) (pad |courtyard or pad in keep-out|outside floorplan|inside floorplan|has pads|off its )", v)
            if m and m.group(1) in P:
                add(self._unit(P[m.group(1)]))
        return out

    def settle(self, rounds: int = 3) -> list[str]:
        """Greedy legalizer for what `legalize` leaves: every movable unit in a placement violation (courtyard
        overlap, part off the board edge or in a keep-out, a floorplan region broken, an isolation short) is taken out,
        and the units are put back one by one, largest first, at the free position nearest to where they were, in the
        four rotations (the current one preferred). Free = on an occupancy raster (SETTLE_CELLS cells over the board,
        each cell marked when any courtyard, keep-out or the margin touches it, so a free window is a legal spot for
        the courtyard): inside the board margin, outside every other courtyard on its sides (a through-hole part on
        both sides), outside the areas that forbid courtyards, inside its rectangular floorplan region and outside the
        exclusive regions it is not a member of. A unit with no free window anywhere stays where it was (the board is
        too full for it). Returns the violations left."""
        v = self.violations()
        v_in, s_in = v, self._state()
        for _ in range(rounds):
            bad = self._violators(v)
            if not bad:
                break
            before = self._state()
            if any(x.startswith("budget ") for x in v):  # put a budget leg's end next to its anchor first: the
                self.budget_seed()                       # free spot nearest to there keeps the leg short
            moved = self._settle(bad)
            self.legalize()
            v2 = self.violations()
            if len(v2) >= len(v):  # no better: back to the round's start, and stop
                self._restore(before)
                break
            v = v2
        if v and self._violators(v):
            # a unit found no free spot among the parts left in place (the free area around it is fragmented): take
            # out its neighbours too (the movable units of its floorplan regions and those near it) and put them back,
            # the violating units first (their spot is the scarce one), then largest first
            start = self._state()
            bad = self._violators(v)
            local = self._neighbours(bad)
            bx = [p.box() for u in local for p in self._unit_parts(u)]
            corner = (min(b[0] for b in bx), min(b[1] for b in bx))  # packed from the neighbourhood's corner
            best = (len(v), start, v)
            for mode, first in (("near", bad), ("near", ()), ("corner", ())):
                self._restore(start)
                self._settle(local, mode, first=first, corner=corner)
                self.legalize()
                vv = self.violations()
                if len(vv) < best[0]:
                    best = (len(vv), self._state(), vv)
                if not vv:
                    break
            self._restore(best[1])
            v = best[2]
        if v and self._violators(v):
            v = self._pack_search(v)
        if v and self._violators(v):
            # still no free spot: repack every unit, near where it is, then from the corner; the result with the
            # fewest violations is kept
            start = self._state()
            best = (len(v), start, v)
            for mode in ("near", "corner"):
                self._restore(start)
                self._settle(self._movable(), mode)
                self.legalize()
                vv = self.violations()
                if len(vv) < best[0]:
                    best = (len(vv), self._state(), vv)
                if not vv:
                    break
            self._restore(best[1])
            v = best[2]
        if len(v) > len(v_in):  # never worse than the placement it was given
            self._restore(s_in)
            v = v_in
        self.cost()
        return v

    def _pack_search(self, v: list[str]) -> list[str]:
        """A tight spot the greedy put-back (largest first, nearest free spot) does not solve: the violating units and
        the movable units of their floorplan regions are put back in other orders (each of the PACK_UNITS leading
        units first, then their other permutations; the violating units lead, the units that only sit in the regions
        go back last), the first unit in each of its four rotations, near where they are and packed from the corner of
        their region; the first legal result is kept, else the one with the fewest violations. Bounded by PACK_EVALS
        tries and PACK_S seconds. Returns the violations left."""
        import itertools
        import time
        bad = self._violators(v)
        members = {id(u): u for u in bad}
        sitting = {}
        rects = []
        for u in bad:
            refs = set(self._refs(u))
            for g in self.regions:
                if refs & set(g["members"]):
                    rect = tuple(g["rect"]) if g["rect"] else tuple(g["poly"].bounds)
                    rects.append(rect)
                    for w in self._movable():  # its members, and the units that sit in it (they make the room short)
                        if set(self._refs(w)) & set(g["members"]):
                            members.setdefault(id(w), w)
                        elif any(_overlap(p.box(), rect) > 0 for p in self._unit_parts(w)):
                            sitting.setdefault(id(w), w)

        def area(u):
            y = getattr(u, "yard", u)
            return y.w * y.h
        ids = {id(u) for u in bad}
        # the violating units lead the permutations, then the other members; the units only sitting in the
        # region go back last, wherever there is room
        ms = sorted(bad, key=area, reverse=True) + sorted((u for k, u in members.items() if k not in ids), key=area,
                                                         reverse=True)
        head, rest = ms[:PACK_UNITS], ms[PACK_UNITS:] + sorted((u for k, u in sitting.items() if k not in members),
                                                                 key=area, reverse=True)
        S = head + rest
        bx = [p.box() for u in S for p in self._unit_parts(u)]
        corners = list(dict.fromkeys([(r[0], r[1]) for r in rects]
                                     + [(min(b[0] for b in bx), min(b[1] for b in bx))]))
        start, best = self._state(), (len(v), self._state(), v)
        t0, n = time.time(), 0
        # each unit first (the others in their order), then the other permutations: the unit put back first is the
        # one whose spot and rotation decide the packing
        firsts = [tuple([u] + [w for w in head if w is not u]) for u in head]
        seen = {tuple(map(id, o)) for o in firsts}
        orders = firsts + [o for o in itertools.permutations(head) if tuple(map(id, o)) not in seen]
        for perm in orders:
            for r0 in (0.0, 90.0, 180.0, 270.0):
                for mode, c in [("near", (0.0, 0.0))] + [("corner", c) for c in corners]:
                    if n >= PACK_EVALS or time.time() - t0 > PACK_S:
                        self._restore(best[1])
                        return best[2]
                    n += 1
                    self._restore(start)
                    self._settle(S, mode, corner=c, order=list(perm) + rest, rots={id(perm[0]): r0})
                    vv = self.violations()
                    if len(vv) < best[0]:
                        best = (len(vv), self._state(), vv)
                    if not vv:
                        return vv
        self._restore(best[1])
        return best[2]

    def infeasible(self, viol=None) -> list[dict]:
        """The movable units of the violations `viol` (default: the current ones) that have no legal spot even with
        every other movable part off the board: their own constraints (floorplan regions, fixed parts, keep-outs, the
        board margin) exclude each other, so no placer can place them. [{ref, size_mm, regions, area, fixed,
        keepouts}]: the region rectangles (else the board) they must sit in, and the fixed parts and keep-outs that
        cover that area."""
        viol = self.violations() if viol is None else viol
        bad = self._violators(viol)
        if not bad:
            return []
        s = self._state()
        try:
            none = self._settle(bad, dry=True)
        finally:
            self._restore(s)
        P, out = self.parts, []
        for u in none:
            refs = set(self._refs(u))
            regs = [g for g in self.regions if refs & set(g["members"])]
            rects = [tuple(g["rect"]) if g["rect"] else tuple(g["poly"].bounds) for g in regs
                     if g["rect"] or g.get("poly") is not None]
            m = float(self.d.board["margin_mm"])
            area = (max([r[0] for r in rects] + [m]), max([r[1] for r in rects] + [m]),
                    min([r[2] for r in rects] + [self.W - m]), min([r[3] for r in rects] + [self.H - m]))
            fixed = sorted({r for r, p in P.items() if p.fixed and r not in refs and any(
                _overlap(b, area) > 0 for b in (p.side_box("front"), p.side_box("back")) if b)})
            kos = sorted({n for bx, sides, o, n in self.ko_fp
                          if not any(self._ko_skip(o, n, r) for r in refs) and _overlap(bx, area) > 0})
            ps = self._unit_parts(u)
            bx = [p.box() for p in ps]
            out.append({"ref": u.ref, "size_mm": (round(max(b[2] for b in bx) - min(b[0] for b in bx), 2),
                                                  round(max(b[3] for b in bx) - min(b[1] for b in bx), 2)),
                        "regions": [g["name"] for g in regs], "area": tuple(round(v, 2) for v in area),
                        "fixed": fixed, "keepouts": kos})
        return out

    def _neighbours(self, bad) -> list:
        """The units `bad` and the movable units around them: the members of the floorplan regions a unit of `bad`
        belongs to, and the units whose courtyard comes within SETTLE_WINDOW unit sizes of one of `bad`."""
        out = {id(u): u for u in bad}
        movable = self._movable()
        for u in bad:
            refs = set(self._refs(u))
            regs = [g for g in self.regions if refs & set(g["members"])]
            ps = self._unit_parts(u)
            bx = [p.box() for p in ps]
            x0, y0 = min(b[0] for b in bx), min(b[1] for b in bx)
            x1, y1 = max(b[2] for b in bx), max(b[3] for b in bx)
            R = SETTLE_WINDOW * max(x1 - x0, y1 - y0, 1.0) / 2
            for w in movable:
                if id(w) in out:
                    continue
                wr = set(self._refs(w))
                if any(wr & set(g["members"]) for g in regs):
                    out[id(w)] = w
                    continue
                for p in self._unit_parts(w):
                    b = p.box()
                    if b[2] >= x0 - R and b[0] <= x1 + R and b[3] >= y0 - R and b[1] <= y1 + R:
                        out[id(w)] = w
                        break
        return list(out.values())

    def _unit_parts(self, u) -> list:
        """The parts that move with unit `u`: its members and, for a block, its courtyard part."""
        refs = self._refs(u)
        ps = [self.parts[r] for r in refs if r in self.parts]
        y = getattr(u, "yard", None)
        if y is not None and all(p is not y for p in ps):
            ps.append(y)
        return ps

    @staticmethod
    def _side_boxes(p) -> dict:
        """side -> boxes of part `p` that block that side: its courtyard on its own side, and on both sides everything
        of it that goes through the board (through-hole pads, non-plated holes), the rule `violations` checks."""
        out = {s: [b] if (b := p.side_box(s)) else [] for s in ("front", "back")}
        for b in hole_boxes(p):
            out["front"].append(b)
            out["back"].append(b)
        return out

    def _settle(self, bad, mode: str = "near", first=(), dry: bool = False, corner=(0.0, 0.0), order=None, rots=None):
        """Put the units `bad` back one by one, largest first (the units of `first` before the others), on free spots
        of the occupancy raster (see `settle`). mode `near`: the free spot nearest to where the unit was (searched in a
        window around it first, then the whole board); `corner`: packed from the top left corner (the lowest y, then
        x; from `corner` (x, y) outward when given), for a repack. Returns the number of units moved. `dry`: a feasibility test that moves
        nothing: only the fixed parts, keep-outs, the board margin and the regions count, and it returns the units of
        `bad` with no free spot even then (their constraints exclude each other). `order`: put the units back in this
        order instead; `rots`: id(unit) -> the only rotation tried for that unit (the packing search)."""
        import numpy as np
        m = float(self.d.board["margin_mm"])
        ce = float(self.d.board.get("copper_edge_mm", 0.3)) + 0.02
        W, H = self.W, self.H
        res = min(0.25, max(0.05, math.sqrt(W * H / SETTLE_CELLS)))
        nx, ny = int(math.ceil(W / res)), int(math.ceil(H / res))
        out_ids = {id(p) for u in bad for p in self._unit_parts(u)}

        def cells(b):  # the cells any part of box b touches (conservative)
            return (max(0, int(math.floor(b[0] / res + 1e-9))), max(0, int(math.floor(b[1] / res + 1e-9))),
                    min(nx, int(math.ceil(b[2] / res - 1e-9))), min(ny, int(math.ceil(b[3] / res - 1e-9))))

        occ = {s: np.zeros((nx, ny), bool) for s in ("front", "back")}
        if dry:  # the rules of `violations` only: no board margin for a courtyard (its pads keep copper_edge_mm)
            m = 0.0
        i0, j0 = int(math.ceil(m / res - 1e-9)), int(math.ceil(m / res - 1e-9))
        i1, j1 = int(math.floor((W - m) / res + 1e-9)), int(math.floor((H - m) / res + 1e-9))
        for o in occ.values():  # outside the board margin
            o[:i0, :] = True
            o[i1:, :] = True
            o[:, :j0] = True
            o[:, j1:] = True

        boxes = self._side_boxes  # the rule `violations` checks: courtyards, and through-board items on both sides
        integ: dict = {}  # side -> integral image of occ[side], (nx + 1, ny + 1); dropped when occ changes

        def mark(p):
            integ.clear()
            for s, bs in boxes(p).items():
                for b in bs:
                    a0, b0, a1, b1 = cells(b)
                    occ[s][a0:a1, b0:b1] = True

        def integral(s):
            if s not in integ:
                S = np.zeros((nx + 1, ny + 1), np.int32)
                S[1:, 1:] = occ[s].cumsum(0).cumsum(1)
                integ[s] = S
            return integ[s]
        seen = set()
        for p in list(self.parts.values()) + [u.yard for u in self.units]:
            if id(p) not in out_ids and id(p) not in seen and (not dry or p.fixed):
                seen.add(id(p))
                mark(p)
        owners = {p.ref for u in bad for p in self._unit_parts(u)}
        for bx, sides, owner, _ in self.ko_fp:
            if owner in owners:
                continue
            a0, b0, a1, b1 = cells(bx)
            for s in sides:
                if s in occ:
                    occ[s][a0:a1, b0:b1] = True
        cm = COURTYARD_MARGIN
        # polygon regions: integral images of the cells a region touches (exclusion) and of the cells outside it
        # (members), on the occupancy raster
        rS = {}
        for gi, g in enumerate(self.regions):
            if g["rect"] or g.get("poly") is None or g["poly"].is_empty:
                continue
            import shapely
            from scipy import ndimage
            X, Y = np.meshgrid((np.arange(nx) + 0.5) * res, (np.arange(ny) + 0.5) * res, indexing="ij")
            inside = shapely.contains_xy(g["poly"], X, Y)
            touch = ndimage.binary_dilation(inside, iterations=1)
            out = ~ndimage.binary_erosion(inside, iterations=1, border_value=0)
            ints = []
            for a in (touch, out):
                S = np.zeros((nx + 1, ny + 1), np.int32)
                S[1:, 1:] = a.cumsum(0).cumsum(1)
                ints.append(S)
            rS[gi] = ints

        def shape(u, rot_):
            """The unit at rotation rot_: its boxes per side, bounding box, pad extent (origin at 0, 0)."""
            u.x, u.y, u.r = 0.0, 0.0, rot_
            self._refs(u)
            need = {"front": [], "back": []}
            pads = []
            own = {}  # ref -> bounding box of that part's courtyards: a region holds its members' courtyards only
            for p in self._unit_parts(u):
                pb = []
                for s, bs in boxes(p).items():
                    need[s] += bs
                    pb += bs
                if pb:
                    own[p.ref] = (min(b[0] for b in pb), min(b[1] for b in pb), max(b[2] for b in pb),
                                  max(b[3] for b in pb))
                pads += [part_pad_box(p, n) for n in p.pads if n not in p.nocu]
            allb = need["front"] + need["back"]
            if not allb:
                return None
            bb = (min(b[0] for b in allb), min(b[1] for b in allb), max(b[2] for b in allb), max(b[3] for b in allb))
            return need, bb, pads, own

        def member_box(sh, g):
            """The bounding box of the unit's parts that are members of region g (a block's courtyard part and its
            other members may stick out of the region: `violations` tests each member's courtyard), or None."""
            bs = [b for r, b in sh[3].items() if r in g["members"]]
            if not bs:
                return None
            return min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)

        def search(u, refs, sh, win):
            """Free windows of the unit shape `sh` with its bounding-box corner in cells win = (a0, b0, a1, b1):
            (origins x, origins y) arrays."""
            need, (ux0, uy0, ux1, uy1), pads, _ = sh
            bw, bh = int(math.ceil((ux1 - ux0) / res - 1e-9)), int(math.ceil((uy1 - uy0) / res - 1e-9))
            a0, b0 = max(0, win[0]), max(0, win[1])
            a1, b1 = min(nx - bw, win[2]), min(ny - bh, win[3])
            if a1 < a0 or b1 < b0:
                return None
            ai = np.arange(a0, a1 + 1)[:, None]
            aj = np.arange(b0, b1 + 1)[None, :]
            free = np.ones((a1 - a0 + 1, b1 - b0 + 1), bool)
            na, nb = a1 - a0 + 1, b1 - b0 + 1
            for s, bs in need.items():
                if not bs:
                    continue
                S = integral(s)  # the board's integral image, shared by every search until a unit is placed
                for b in bs:  # each box of the unit at its offset in the unit's bounding box
                    di = int(math.floor((b[0] - ux0) / res + 1e-9))
                    dj = int(math.floor((b[1] - uy0) / res + 1e-9))
                    wi = int(math.ceil((b[2] - ux0) / res - 1e-9)) - di
                    wj = int(math.ceil((b[3] - uy0) / res - 1e-9)) - dj
                    a, c = a0 + di, b0 + dj  # window sums as slices of S (views, not fancy indexing)
                    free &= (S[a + wi:a + wi + na, c + wj:c + wj + nb] - S[a:a + na, c + wj:c + wj + nb]
                             - S[a + wi:a + wi + na, c:c + nb] + S[a:a + na, c:c + nb]) == 0
            lo_x, lo_y = ai * res, aj * res
            hi_x, hi_y = lo_x + (ux1 - ux0), lo_y + (uy1 - uy0)
            if pads:  # copper keeps copper_edge_mm from the board edge (a body may overhang it, a pad may not)
                px0, py0 = min(b[0] for b in pads) - ux0, min(b[1] for b in pads) - uy0
                px1, py1 = max(b[2] for b in pads) - ux0, max(b[3] for b in pads) - uy0
                free &= (lo_x + px0 >= ce) & (lo_x + px1 <= W - ce) & (lo_y + py0 >= ce) & (lo_y + py1 <= H - ce)
            for gi, g in enumerate(self.regions):  # members inside their region; the others out of an exclusive one
                if not g["rect"]:
                    if gi not in rS:
                        continue
                    member = bool(refs & set(g["members"]))
                    if not member and not (g["excl"] and not refs & set(g["allow"])
                                           and any(need[s] for s in g["sides"] if s in need)):
                        continue
                    S = rS[gi][1 if member else 0]
                    e = int(math.ceil(cm / res - 1e-9))  # the courtyard without the placer margin
                    mb = member_box(sh, g) if member else None
                    if mb is None:
                        di = dj = 0
                        wi, wj = max(1, bw - 2 * e), max(1, bh - 2 * e)
                    else:  # a member: only the box of its member parts must be inside
                        di = int(math.floor((mb[0] - ux0) / res + 1e-9))
                        dj = int(math.floor((mb[1] - uy0) / res + 1e-9))
                        wi = max(1, int(math.ceil((mb[2] - ux0) / res - 1e-9)) - di - 2 * e)
                        wj = max(1, int(math.ceil((mb[3] - uy0) / res - 1e-9)) - dj - 2 * e)
                    # window sums at the clipped corners min(a + e, nx - wi): slices over the corner range, then
                    # a 1-D take per axis (the clip repeats the last row or column)
                    ia = np.minimum(np.arange(a0, a1 + 1) + di + e, nx - wi)
                    jc = np.minimum(np.arange(b0, b1 + 1) + dj + e, ny - wj)
                    p0, p1, q0, q1 = int(ia[0]), int(ia[-1]) + 1, int(jc[0]), int(jc[-1]) + 1
                    D = (S[p0 + wi:p1 + wi, q0 + wj:q1 + wj] - S[p0:p1, q0 + wj:q1 + wj]
                         - S[p0 + wi:p1 + wi, q0:q1] + S[p0:p1, q0:q1])
                    free &= D.take(ia - p0, 0).take(jc - q0, 1) == 0
                    continue
                gx0, gy0, gx1, gy1 = g["rect"]
                if refs & set(g["members"]):
                    mb = member_box(sh, g)
                    if mb is None:
                        continue
                    mx0, my0 = lo_x + (mb[0] - ux0), lo_y + (mb[1] - uy0)
                    mx1, my1 = lo_x + (mb[2] - ux0), lo_y + (mb[3] - uy0)
                    free &= (mx0 + cm >= gx0 - 1e-6) & (mx1 - cm <= gx1 + 1e-6) & \
                            (my0 + cm >= gy0 - 1e-6) & (my1 - cm <= gy1 + 1e-6)
                elif g["excl"] and not refs & set(g["allow"]) and any(need[s] for s in g["sides"] if s in need):
                    free &= (hi_x - cm <= gx0 + 1e-6) | (lo_x + cm >= gx1 - 1e-6) | \
                            (hi_y - cm <= gy0 + 1e-6) | (lo_y + cm >= gy1 - 1e-6)
            if not free.any():
                return None
            fi, fj = np.nonzero(free)
            return (fi + a0) * res - ux0, (fj + b0) * res - uy0

        def area(u):
            y = getattr(u, "yard", u)
            return y.w * y.h
        moved = 0
        fid = {id(u) for u in first}
        if order is None:
            order = [u for u in bad if id(u) in fid] + sorted((u for u in bad if id(u) not in fid), key=area,
                                                              reverse=True)
        none = []
        for u in order:
            refs = {p.ref for p in self._unit_parts(u)}
            here = (u.x, u.y, u.r)
            shapes = []
            cand = [u.r, (u.r + 90) % 360, (u.r + 180) % 360, (u.r + 270) % 360]
            if rots and id(u) in rots:
                cand = [rots[id(u)]]
            for k, rot_ in enumerate(cand):
                sh = shape(u, rot_)
                if sh is not None:
                    shapes.append((k, rot_, sh))
            best = None
            if mode == "near":
                size = max(max(sh[1][2] - sh[1][0], sh[1][3] - sh[1][1]) for _, _, sh in shapes) if shapes else 0
                wins = [SETTLE_WINDOW * max(size, 1.0), math.inf]
            else:
                wins = [math.inf]
            for R in wins:
                for k, rot_, sh in shapes:
                    ux0, uy0 = sh[1][0], sh[1][1]
                    cx, cy = here[0] + ux0, here[1] + uy0  # where the bounding-box corner was in this rotation
                    win = (0, 0, nx, ny) if R == math.inf else (int((cx - R) / res), int((cy - R) / res),
                                                                 int((cx + R) / res) + 1, int((cy + R) / res) + 1)
                    got = search(u, refs, sh, win)
                    if got is None:
                        continue
                    ox, oy = got
                    if mode == "near":
                        dist = np.abs(ox - here[0]) + np.abs(oy - here[1]) + SETTLE_TURN_MM * (k > 0)
                    else:
                        ax, ay = corner
                        dist = np.abs(oy + uy0 - ay) * (W + 1) + np.abs(ox + ux0 - ax) + SETTLE_TURN_MM * (k > 0)
                    t = int(np.argmin(dist))
                    if best is None or dist[t] < best[0]:
                        best = (float(dist[t]), float(ox[t]), float(oy[t]), rot_)
                if best is not None:
                    break
            if best is None or dry:  # no free window anywhere: it stays (the board is too full for it)
                u.x, u.y, u.r = here
                self._refs(u)
                if best is None:
                    none.append(u)
                continue
            u.x, u.y, u.r = best[1:]
            self._refs(u)
            for p in self._unit_parts(u):
                mark(p)
            moved += 1
        return none if dry else moved

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

    def _rule_violations(self, region_board_rects=None):
        """(movable, fixed-only) keep-out and isolation violations of the current placement. A violation that involves
        only fixed parts cannot be repaired by the placer: it is listed apart and never makes placement fail."""
        P = self.parts
        mv, fx = [], []
        for r in P:
            if self._ko_cost(r) > 0.01:
                yards = [b for b in (P[r].side_box("front"), P[r].side_box("back")) if b]
                pads = [self.part_pad_box(P[r], k) for k in P[r].pads if k not in P[r].nocu]
                names = [n for box, sides, o, n in self.ko_fp + self.ko_pad if not self._ko_skip(o, n, r) and (
                    any(_overlap(bx, box) > 0 and self._in_area(bx, o, n, courtyard=True) for bx in yards)
                    or any(_overlap(bx, box) > 0 and self._in_area(bx, o, n) for bx in pads))]
                names += [n for hull, refs, n in self.iso_hulls if r not in refs and any(
                    _poly_overlap(b, hull) > 0.01 for b in (P[r].side_box("front"), P[r].side_box("back")) if b)]
                if names:  # the box test is a bound: a round area overlaps its bounding box at the corners only
                    (fx if P[r].fixed else mv).append(f"{r} courtyard or pad in keep-out '{names[0]}'")
                for g in self.regions:
                    board_rect = (region_board_rects or {}).get(g["name"]) if P[r].fixed else None
                    out, inside, pads = self._region_cost(P[r], g, board_rect)
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
        for k in sorted(self.sep_hard):
            a, _, b, _, need, key = self.seps[k]
            short = self._sep_val(k) / max(self.wt[key], 1e-9)
            if short > 0.01:
                (fx if P[a].fixed and P[b].fixed else mv).append(
                    f"separation {a}/{b}: {short:.2f} mm short of board.{SEP_HARD_KEYS[key]} {need:g} mm")
        if edge_hard_on():
            for r, p in P.items():
                if p.edge and not p.fixed and r in self.d.components:
                    e = self._part_cost(r)[1]
                    if e > EDGE_TOL:
                        mv.append(f"{r} off its {p.edge} edge by {e:.2f} mm (placement.edge; at most {EDGE_TOL:g} mm)")
        for both_fixed, msg in self.budget_violations():
            (fx if both_fixed else mv).append(msg)
        return mv, fx

    def fixed_violations(self, region_board_rects=None) -> list[str]:
        """Fixed-part rule findings. Optional region board bounds exempt off-board
        courtyard overhang only; placement costs and copper checks are unchanged."""
        return self._rule_violations(region_board_rects)[1]

    def _state(self):
        return {r: (p.x, p.y, p.r) for r, p in self.parts.items()}

    def _restore(self, s):
        for r, (x, y, a) in s.items():
            self.parts[r].x, self.parts[r].y, self.parts[r].r = x, y, a
        for u in self.units:  # the courtyard part holds the block origin
            u.x, u.y, u.r = s[u.ref]


def place(design: Design, libs, seeds: int = 4, iters: int = 30000, weights=None, seed0: int = 1,
          refine: float = REFINE, deadline: float | None = None, areas=None):
    """Best of several seeds (seed0 .. seed0 + seeds - 1), then a short low-temperature refine pass of
    `refine * iters` moves on the best one (taken out of the seeds' budget). `deadline` (time.time()): no new seed
    starts after it (the first always runs) and unstick stops. Returns (parts, cost, violations)."""
    import time
    b = design.board
    best = None
    extra = int(iters * refine)  # the refine pass is paid for by shortening the seeds, so total moves stay the same
    iters -= extra // seeds
    from . import routability
    rank = routability.rank_on(design) and seeds > 1
    for seed in range(seed0, seed0 + seeds):
        if best is not None and deadline is not None and time.time() >= deadline:
            print(f"[place] layout budget used up: seeds {seed}..{seed0 + seeds - 1} skipped")
            break
        parts = build_parts(design, libs, areas=areas)
        pl = Placer(design, parts, b["width_mm"], b["height_mm"], seed=seed, weights=weights)
        cost = pl.run(iters)
        v = pl.violations()
        # a legal seed first; among them, with the routability ranking, the fewest predicted hard nets
        score = routability.rank_score(design, libs, parts) if rank and not v else 0.0
        key = (len(v), score, cost)
        if rank:
            print(f"[place] seed {seed}: {len(v)} violations, predicted routability score {score:.3f}, cost {cost:.1f}")
        if best is None or key < best[0]:
            best = (key, parts, cost, v, pl)
    key, parts, cost, v, pl = best
    key = (key[0], key[2])
    for c in pl.corridors():
        print(f"[place] corridor {c['msg']}")
    for f in pl.fixed_violations():
        if f not in _REPORTED:
            _REPORTED.add(f)
            print(f"[place] fixed parts: {f}")
    if v and any(not p.fixed for p in parts.values()):  # a violation left by the annealing: search it away
        before = pl._state()
        cost2 = pl.unstick(deadline=deadline)
        v2 = pl.violations()
        if (len(v2), cost2) <= key:
            cost, v, key = cost2, v2, (len(v2), cost2)
        else:
            pl._restore(before)
            pl.cost()
    if v and any(not p.fixed for p in parts.values()) and get_settings().layout.settle:
        before = pl._state()
        v2 = pl.settle()
        if len(v2) < len(v):
            cost, v, key = pl.cost(), v2, (len(v2), pl.cost())
        else:
            pl._restore(before)
            pl.cost()
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
