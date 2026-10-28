"""KiCad PCB generation, Specctra DSN export, Freerouting run and SES import."""

from __future__ import annotations

import copy
import math
import re
import uuid
from pathlib import Path

from shapely.geometry import box

from ...core import sexpr
from ...core.design import Design
from ...core.current import current_width_mm, ipc2221_width_mm  # noqa: F401 - re-exported
from ...core.geometry import Part, cu_layers, rot
from .routing import pad_layers
from ...core.sexpr import Q

PCB_VERSION = "20241229"


def uid() -> Q:
    return Q(str(uuid.uuid4()))


# ------------------------------------------------------------------ track sizing

def net_classes(design: Design) -> dict[str, dict]:
    """net -> {width, clearance, class}, and min_width for a net with `min_width_mm` (net_min_widths)"""
    b = design.board
    out = {}
    for net in design.nets():
        info = design.net_info(net)
        w = float(info.get("width_mm", b["track_mm"]))
        if info.get("current_a"):
            w = max(w, round(ipc2221_width_mm(float(info["current_a"]), b["copper_oz"]) + 0.049, 1))
        if info.get("class") == "power":
            w = max(w, float(b.get("power_track_mm", 0.4)))
        w = max(w, (getattr(design, "budget_widths", None) or {}).get(net, 0.0))  # budgets.widths, set by the layout stage
        out[net] = {"width": w, "clearance": float(info.get("clearance_mm", b["clearance_mm"])),
                    "class": info.get("class", "signal")}
        if info.get("min_width_mm") is not None:
            out[net]["min_width"] = float(info["min_width_mm"])
    return out


def net_min_widths(design: Design) -> dict[str, float]:
    """`nets.<name>.min_width_mm`: the narrowest copper the net may have (no neck under it; a narrower track fails the
    layout), for the nets that set it."""
    return {n: c["min_width"] for n, c in net_classes(design).items() if "min_width" in c}


# ------------------------------------------------------------------ board writer

def _layers(n: int) -> list:
    cu = [[0, Q("F.Cu"), "signal"]]
    if n == 4:
        cu += [[4, Q("In1.Cu"), "signal"], [6, Q("In2.Cu"), "signal"]]
    cu += [[2, Q("B.Cu"), "signal"]]
    tech = [[9, Q("F.Adhes"), "user", Q("F.Adhesive")], [11, Q("B.Adhes"), "user", Q("B.Adhesive")],
            [13, Q("F.Paste"), "user"], [15, Q("B.Paste"), "user"],
            [5, Q("F.SilkS"), "user", Q("F.Silkscreen")], [7, Q("B.SilkS"), "user", Q("B.Silkscreen")],
            [1, Q("F.Mask"), "user"], [3, Q("B.Mask"), "user"],
            [17, Q("Dwgs.User"), "user", Q("User.Drawings")], [19, Q("Cmts.User"), "user", Q("User.Comments")],
            [25, Q("Edge.Cuts"), "user"], [27, Q("Margin"), "user"],
            [31, Q("F.CrtYd"), "user", Q("F.Courtyard")], [29, Q("B.CrtYd"), "user", Q("B.Courtyard")],
            [35, Q("F.Fab"), "user"], [33, Q("B.Fab"), "user"]]
    return ["layers"] + cu + tech


def net_layers(design: Design) -> dict[str, list[str]]:
    """`nets.<name>.layers`: the copper layers the router may use for that net (KiCad names, globs like In*.Cu), for
    the nets that set it, in board order. A name no copper layer of the board matches is dropped (design check
    reports it)."""
    import fnmatch
    copper = cu_layers(int(design.board["layers"]))
    out = {}
    for n, i in (design.raw.get("nets") or {}).items():
        pats = (i or {}).get("layers")
        if pats is None:
            continue
        pats = [pats] if isinstance(pats, str) else list(pats) if isinstance(pats, (list, tuple)) else []
        out[n] = [L for L in copper if any(fnmatch.fnmatchcase(L, str(p)) for p in pats)]
    return out


def _quote_tokens(node: list) -> None:
    """Quote pad numbers and layer names in place. Older KiCad libraries write `(pad 1 smd ..)` and
    `(layers F.Cu F.Mask)` unquoted; KiCad reads both, but the router's board parser only finds quoted pad numbers
    and silently drops those pads (IC pads vanish from the router's view and nets come out unconnected)."""
    head = node[0] if node else None
    if head == "pad" and len(node) > 1 and not isinstance(node[1], list):
        node[1] = Q(str(node[1]))
    if head in ("layer", "layers"):
        for i in range(1, len(node)):
            if not isinstance(node[i], list) and not (head == "layer" and str(node[i]) == "knockout"):
                node[i] = Q(str(node[i]))  # `(layer "F.SilkS" knockout)`: the flag stays a bare word
    for x in node:
        if isinstance(x, list):
            _quote_tokens(x)


def _place_footprint(fp_tree: list, ref: str, value: str, part: Part, netidx: dict, pin_nets: dict,
                     fields: dict | None = None) -> list:
    t = copy.deepcopy(fp_tree)
    t[0] = "footprint"
    fp_name = str(t[1])
    out = [t[0], Q(fp_name if ":" in fp_name else fp_name)]
    out.append(["layer", Q("F.Cu")])
    out.append(["uuid", uid()])
    out.append(["at", round(part.x, 4), round(part.y, 4)] + ([part.r] if part.r else []))
    for k, v in (fields or {}).items():
        out.append(["property", Q(k), Q(str(v)), ["at", 0, 0, 0], ["layer", Q("F.Fab")], ["hide", "yes"],
                    ["uuid", uid()], ["effects", ["font", ["size", 1, 1], ["thickness", 0.15]]]])
    for item in t[2:]:
        if not isinstance(item, list):
            continue
        head = item[0]
        if head in ("layer", "uuid", "at", "version", "generator", "generator_version", "tedit"):
            continue
        if head == "property":
            item = copy.deepcopy(item)
            if str(item[1]) == "Reference":
                item[2] = Q(ref)
            elif str(item[1]) == "Value":
                item[2] = Q(value)
            _rotate_text(item, part.r)
        elif head == "fp_text":
            item = copy.deepcopy(item)
            if str(item[1]) == "reference":
                item[2] = Q(ref)
            elif str(item[1]) == "value":
                item[2] = Q(value)
            _rotate_text(item, part.r)
        elif head == "pad":
            item = copy.deepcopy(item)
            _quote_tokens(item)
            at = sexpr.find(item, "at")
            sexpr.set_at_angle(at, (sexpr.at_angle(at) + part.r) % 360)
            net = pin_nets.get(str(item[1]))
            item = [x for x in item if not (isinstance(x, list) and x and x[0] in ("net", "uuid"))]
            if net is not None and str(item[1]) and net.upper() not in ("NC", "~"):  # design.nets() skips them too
                item.append(["net", netidx[net], Q(net)])
            item.append(["uuid", uid()])
        elif head == "model":
            item = copy.deepcopy(item)
            for k in ("path",):
                pass
            mp = str(item[1]).replace("${KICAD9_3DMODEL_DIR}", "${KICAD10_3DMODEL_DIR}")
            item[1] = Q(mp)
        elif head == "zone":
            # .kicad_mod zones are in footprint coordinates, .kicad_pcb zones in board coordinates
            item = copy.deepcopy(item)
            for poly in (x for x in item if isinstance(x, list) and x and x[0] == "polygon"):
                for xy in (p for p in sexpr.find(poly, "pts")[1:] if isinstance(p, list) and p[0] == "xy"):
                    dx, dy = rot(float(xy[1]), float(xy[2]), part.r)
                    xy[1], xy[2] = round(part.x + dx, 4), round(part.y + dy, 4)
        elif head not in ("pad", "property", "fp_text"):
            item = copy.deepcopy(item)
        _quote_tokens(item)
        out.append(item)
    return out


def _rotate_text(item: list, r: float):
    at = sexpr.find(item, "at")
    if at is None:
        return
    sexpr.set_at_angle(at, (sexpr.at_angle(at) + r) % 360)


def thermal_vias(design: Design, libs, parts: dict[str, Part]) -> list[dict]:
    """Via grids inside thermal pads, from `thermal_vias: {pad, count, pitch_mm}` on a component."""
    b = design.board
    out = []
    for ref, part in parts.items():
        c = design.components[ref]
        tv = c.get("thermal_vias")
        if not tv:
            continue
        pad_no = str(tv.get("pad", ""))
        net = c.pins.get(pad_no)
        pad = next((p for p in libs.footprint(c.get("footprint"))["pads"] if p["number"] == pad_no), None)
        if not net or pad is None:
            continue
        w, h = pad["w"], pad["h"]
        if pad.get("hull"):
            xs, ys = [q[0] for q in pad["hull"]], [q[1] for q in pad["hull"]]
            cx0, cy0 = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
            w, h = max(xs) - min(xs), max(ys) - min(ys)
        else:
            cx0, cy0 = 0.0, 0.0
        if pad["rot"] % 180:
            w, h = h, w
        pitch = float(tv.get("pitch_mm", max(b["via_dia_mm"] + 0.4, 1.0)))
        r = b["via_dia_mm"] / 2
        nx = max(1, int((w - 2 * r) // pitch) + 1)
        ny = max(1, int((h - 2 * r) // pitch) + 1)
        pts = [((i - (nx - 1) / 2) * pitch + cx0, (j - (ny - 1) / 2) * pitch + cy0) for i in range(nx) for j in range(ny)]
        pts = pts[: int(tv.get("count", len(pts)))]
        for lx, ly in pts:
            dx, dy = rot(pad["x"] + lx, pad["y"] + ly, part.r)
            out.append({"net": net, "x": part.x + dx, "y": part.y + dy, "thermal": f"{ref}.{pad_no}"})
    return out


def build_board(design: Design, libs, parts: dict[str, Part], tracks: list | None = None,
                vias: list | None = None, island_removal: int = 0) -> list:
    b = design.board
    nets = list(design.nets())
    netidx = {n: i + 1 for i, n in enumerate(nets)}
    classes = net_classes(design)
    nl = b["layers"]
    tree = ["kicad_pcb", ["version", PCB_VERSION], ["generator", Q("hwflow")], ["generator_version", Q("10.0")],
            ["general", ["thickness", b["thickness_mm"]], ["legacy_teardrops", "no"]],
            ["paper", Q("A4")], _layers(nl),
            ["setup", ["pad_to_mask_clearance", 0], ["allow_soldermask_bridges_in_footprints", "no"],
             ["pcbplotparams", ["layerselection", Q("0x00010fc_ffffffff")], ["outputformat", 1],
              ["outputdirectory", Q("gerbers/")]]],
            ["net", 0, Q("")]]
    for n in nets:
        tree.append(["net", netidx[n], Q(n)])
    W, H = b["width_mm"], b["height_mm"]
    tree += edge_cuts(design)
    tree.append(["gr_text", Q(f"{design.name} rev {design.raw.get('revision', 'A')}"), ["at", 1.5, H - 1.2, 0],
                 ["layer", Q("F.SilkS")], ["uuid", uid()],
                 ["effects", ["font", ["size", 0.8, 0.8], ["thickness", 0.12]], ["justify", "left"]]])
    for ref, part in parts.items():
        c = design.components[ref]
        fp = libs.footprint_tree(c.get("footprint"))
        fp = copy.deepcopy(fp)
        fp[1] = Q(c.get("footprint"))
        fields = {k.upper() if k != "manufacturer" else "Manufacturer": c.get(k)
                  for k in ("mpn", "lcsc", "manufacturer") if c.get(k)}
        tree.append(_place_footprint(fp, ref, str(c.get("value")), part, netidx, c.pins, fields))
    routed = tracks is not None
    if design.raw.get("blocks"):  # layout blocks: their copper, once, on the unrouted and on the routed board
        from ...core import blocks
        tracks, vias = blocks.merge(design, parts, tracks, vias)
        tree += blocks.keepout_zones(design, parts)
    for t in tracks or []:
        tree.append(["segment", ["start", round(t["x1"], 4), round(t["y1"], 4)], ["end", round(t["x2"], 4), round(t["y2"], 4)],
                     ["width", t["w"]], ["layer", Q(t["layer"])], ["net", netidx[t["net"]]], ["uuid", uid()]])
    if routed:  # routed board: add declared thermal via grids inside their pads
        vias = list(vias or []) + thermal_vias(design, libs, parts)
    for v in vias or []:
        tree.append(["via", ["at", round(v["x"], 4), round(v["y"], 4)], ["size", b["via_dia_mm"]],
                     ["drill", b["via_drill_mm"]], ["layers", Q("F.Cu"), Q("B.Cu")], ["net", netidx[v["net"]]],
                     ["uuid", uid()]])
    if b["gnd_pour"]:
        for g in sorted(design.ground_nets()):
            if g not in netidx:
                continue
            for layer in cu_layers(nl):
                if layer in b.get("pour_skip_layers", []):
                    continue
                tree.append(["zone", ["net", netidx[g]], ["net_name", Q(g)], ["layer", Q(layer)], ["uuid", uid()],
                             ["hatch", "edge", 0.5],
                             ["connect_pads", "yes", ["clearance", b["clearance_mm"]]],
                             ["min_thickness", 0.25], ["filled_areas_thickness", "no"],
                             ["fill", "yes", ["thermal_gap", 0.3], ["thermal_bridge_width", 0.4], ["island_removal_mode", island_removal]],
                             ["polygon", ["pts", ["xy", 0.3, 0.3], ["xy", W - 0.3, 0.3], ["xy", W - 0.3, H - 0.3],
                                          ["xy", 0.3, H - 0.3]]]])
    return tree


EDGE_CLEARANCE_MM = 0.3  # min_copper_edge_clearance written by write_project
ARC_QUAD_SEGS = 256  # circle cuts on Edge.Cuts: a chord sags r x 5e-6 under the arc, so the edge points are on it


def edge_cuts(design: Design) -> list:
    """Edge.Cuts: the board rectangle, or with floorplan cut regions the rectangle minus their union as gr_line, gr_arc
    where the edge follows a circle cut, and gr_circle for a hole that is a whole circle."""
    from shapely.ops import unary_union

    from ...core import floorplan
    b = design.board
    W, H = b["width_mm"], b["height_mm"]
    stroke = ["stroke", ["width", 0.1], ["type", "default"]]
    cs = floorplan.cuts(design, ARC_QUAD_SEGS)
    if not cs:
        return [["gr_rect", ["start", 0, 0], ["end", W, H], stroke, ["fill", "no"], ["layer", Q("Edge.Cuts")], ["uuid", uid()]]]
    shape = box(0, 0, W, H).difference(unary_union([c["poly"] for c in cs]))
    circles = [c["circle"] for c in cs if c["circle"]]
    out = []
    for poly in getattr(shape, "geoms", [shape]):
        for ring in (poly.exterior, *poly.interiors):
            out += _ring_items(list(ring.coords)[:-1], circles, stroke)
    return out


def _ring_items(pts: list, circles: list, stroke: list) -> list:
    """One closed outline ring as gr_line, gr_arc (runs of chords of a circle cut) or one gr_circle."""
    def on(k, q):
        cx, cy, r = circles[k]
        return abs(math.hypot(q[0] - cx, q[1] - cy) - r) < 1e-4 + r * 1e-5

    def circle_of(a, q):  # the circle cut an edge is a chord of, or None
        return next((k for k in range(len(circles)) if on(k, a) and on(k, q)
                     and math.dist(a, q) < circles[k][2] * 0.02 + 1e-6), None)

    def xy(q):
        return [round(q[0], 4), round(q[1], 4)]

    n = len(pts)
    lab = [circle_of(pts[i], pts[(i + 1) % n]) for i in range(n)]
    layer = ["layer", Q("Edge.Cuts")]
    if lab[0] is not None and all(x == lab[0] for x in lab):
        cx, cy, r = circles[lab[0]]
        return [["gr_circle", ["center", *xy((cx, cy))], ["end", *xy((cx + r, cy))], stroke, ["fill", "no"], layer,
                 ["uuid", uid()]]]
    i0 = next((i for i in range(n) if lab[i] != lab[i - 1]), 0)  # start where a run starts
    out, i = [], 0
    while i < n:
        k, run = lab[(i0 + i) % n], [pts[(i0 + i) % n]]
        while i < n and lab[(i0 + i) % n] == k:
            i += 1
            run.append(pts[(i0 + i) % n])
            if k is None:
                break  # a straight edge is one line
        if k is None or math.dist(run[0], run[-1]) < 0.01:
            out.append(["gr_line", ["start", *xy(run[0])], ["end", *xy(run[-1])], stroke, layer, ["uuid", uid()]])
            continue
        if len(run) > 2:
            mid = run[len(run) // 2]
        else:  # one chord: its midpoint pushed out onto the circle
            cx, cy, r = circles[k]
            mx, my = (run[0][0] + run[1][0]) / 2, (run[0][1] + run[1][1]) / 2
            d = math.hypot(mx - cx, my - cy) or 1.0
            mid = (cx + (mx - cx) * r / d, cy + (my - cy) * r / d)
        out.append(["gr_arc", ["start", *xy(run[0])], ["mid", *xy(mid)], ["end", *xy(run[-1])], stroke, layer,
                    ["uuid", uid()]])
    return out


def board_text(tree: list) -> str:
    """The board as text, its UUIDs renamed in order of appearance: the same input gives the same bytes."""
    ids: dict[str, str] = {}
    text = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                  lambda m: ids.setdefault(m.group(0), str(uuid.uuid5(uuid.NAMESPACE_URL, f"hwflow/{len(ids)}"))),
                  sexpr.dumps(tree))
    return text + "\n"


def write_board(path: Path, tree: list):
    path.write_text(board_text(tree))


def pour_keepoff_mm(g: dict) -> float | None:
    """Distance a copper pour (zone) of another net keeps from the members of a `board.isolation` entry:
    max(clearance_mm, creepage_mm), or None when the entry sets neither."""
    vals = [float(g[k]) for k in ("clearance_mm", "creepage_mm") if g.get(k) is not None]
    return max(vals) if vals else None


def isolation_groups(design: Design) -> list[dict]:
    """board.isolation (backlog 38): each entry names a net group (`nets:` glob or list of globs, and/or `class:`)
    that keeps `clearance_mm` and `creepage_mm` to every other net. Returns the member nets and the KiCad custom
    rules (clearance, creepage) written to the .kicad_dru, so DRC enforces them in the layout stage."""
    import fnmatch
    out = []
    nets = sorted(set(design.nets()) | set((design.raw.get("nets") or {})))
    for i, g in enumerate(design.board.get("isolation") or []):
        pats = g.get("nets") or []
        pats = [pats] if isinstance(pats, str) else list(pats)
        cls = g.get("class")
        members = [n for n in nets if any(fnmatch.fnmatchcase(n, p) for p in pats)
                   or (cls and design.net_info(n).get("class") == cls)]
        name = str(g.get("name") or g.get("req") or f"isolation{i + 1}")
        between = g.get("between_mm")
        row = {"name": name, "members": members, "req": g.get("req"), "kicad": [],
               "clearance_mm": g.get("clearance_mm"), "creepage_mm": g.get("creepage_mm"),
               "between_mm": float(between) if between is not None else None}
        out.append(row)
        if not members:
            continue
        a = " || ".join(f"A.NetName == '{n}'" for n in members)
        cond = (f"({a})" if len(members) > 1 else a) + " && " + " && ".join(f"B.NetName != '{n}'" for n in members)
        for kind in ("clearance", "creepage"):
            v = g.get(f"{kind}_mm")
            if v is not None:
                row["kicad"].append(f'(rule "{name} {kind}" (constraint {kind} (min {float(v):g}mm)) '
                                    f'(condition "{cond}"))')
        # a copper pour (zone) of another net keeps max(clearance, creepage): the zone filler honours clearance
        # constraints only, so a fill at the clearance fails the creepage rule on a flat board (no slot), where
        # creepage is the in-plane surface distance. Written after the group clearance rule: the later rule wins.
        keep = pour_keepoff_mm(g)
        if keep is not None and keep > float(g.get("clearance_mm") or 0.0):
            row["kicad"].append(f'(rule "{name} pour keep-off" (constraint clearance (min {keep:g}mm)) '
                                f'(condition "{cond} && B.Type == \'Zone\'"))')
        # between_mm: functional insulation between two different member nets (one rule per member, against the
        # other members: KiCad applies a rule to both orders of a pair, so each pair is covered twice, harmlessly)
        if between is not None and len(members) > 1:
            for n in members:
                others = [m for m in members if m != n]
                bc = " || ".join(f"B.NetName == '{m}'" for m in others)
                bcond = f"A.NetName == '{n}' && " + (f"({bc})" if len(others) > 1 else bc)
                for kind in ("clearance", "creepage"):
                    row["kicad"].append(f'(rule "{name} between {n} {kind}" (constraint {kind} '
                                        f'(min {float(between):g}mm)) (condition "{bcond}"))')
    return out


def isolation_needs(design: Design) -> list[dict]:
    """Distance a net group keeps to every other net, as [{members, mm}] for the placer: `board.isolation` entries
    (creepage, else clearance) and `board.drc_rules` clearance or creepage rules shaped
    `A.NetName == 'P' && B.NetName != 'P'` (P a glob; the largest value per P). `between` is the group's
    between_mm (the distance two different member nets keep), or None."""
    import fnmatch
    out = [{"members": g["members"], "mm": float(g["creepage_mm"] if g["creepage_mm"] is not None else g["clearance_mm"]),
            "name": g["name"], "between": g["between_mm"]} for g in isolation_groups(design)
           if g["members"] and (g["creepage_mm"] is not None or g["clearance_mm"] is not None)]
    nets = sorted(set(design.nets()) | set((design.raw.get("nets") or {})))
    pat = re.compile(r"A\.NetName\s*==\s*'([^']+)'\s*&&\s*B\.NetName\s*!=\s*'([^']+)'")
    raw: dict = {}
    for r in design.board.get("drc_rules") or []:
        mm = re.search(r"\(constraint\s+(?:clearance|creepage)\s*\(min\s+([0-9.]+)\s*mm\)", r)
        cond = re.search(r'\(condition\s+"([^"]*)"', r)
        m = pat.fullmatch(cond.group(1).strip()) if cond else None
        if mm and m and m.group(1) == m.group(2):
            raw[m.group(1)] = max(raw.get(m.group(1), 0.0), float(mm.group(1)))
    out += [{"members": [n for n in nets if fnmatch.fnmatchcase(n, p)], "mm": mm, "name": f"drc_rules {p}", "between": None}
            for p, mm in raw.items()]
    return [g for g in out if g["members"]]


def isolation_router_rules(design: Design) -> list[dict]:
    """Creepage as a clearance the router honours (L11). KiCadRoutingTools reads no net-name condition on a
    clearance rule; it reads a track-to-track rule scoped to a net class, `A.Type=='track' && B.Type=='track' &&
    A.NetClass=='C' && B.NetClass!='C'` (member against non-member only, so a group keeps its own spacing). Every
    creepage requirement between a net group and all other nets becomes such a rule at the creepage value, with a
    class `iso_<name>` that holds the group nets. Sources: `board.isolation` entries with `creepage_mm`, and
    `board.drc_rules` creepage rules shaped `A.NetName == 'P' && B.NetName != 'P'` (P a glob). A group with
    `between_mm` adds one class `isb_<name>_between_<net>` per member, holding only that net, with a rule at
    between_mm (`between: True`): the members keep that distance to each other's tracks. Returns
    [{class, members, mm, between, rule}]. Other condition shapes stay with KiCad DRC and the repair loop."""
    import fnmatch
    nets = sorted(set(design.nets()) | set((design.raw.get("nets") or {})))
    out, seen = [], set()

    def add(name, members, mm, prefix="iso_", between=False):
        cname = prefix + re.sub(r"\W+", "_", name).strip("_")
        if not members or cname in seen:
            return
        seen.add(cname)
        cond = f"A.Type == 'track' && B.Type == 'track' && A.NetClass == '{cname}' && B.NetClass != '{cname}'"
        out.append({"class": cname, "members": members, "mm": mm, "between": between,
                    "rule": f'(rule "{name} (router clearance)" (constraint clearance (min {mm:g}mm)) '
                            f'(condition "{cond}"))'})

    for g in isolation_groups(design):
        if g["creepage_mm"] is not None:
            add(g["name"], g["members"], float(g["creepage_mm"]))
        # between_mm: a class of its own per member (KiCadRoutingTools reads one class per rule, and a net may be in
        # several classes), so the member keeps between_mm to every other net; against the other side of the group
        # the larger group rule wins (the router takes the largest rule that binds a pair)
        if g["between_mm"] is not None and len(g["members"]) > 1:
            for n in g["members"]:
                add(f"{g['name']} between {n}", [n], float(g["between_mm"]), "isb_", True)
    pat = re.compile(r"A\.NetName\s*==\s*'([^']+)'\s*&&\s*B\.NetName\s*!=\s*'([^']+)'")
    for r in design.board.get("drc_rules") or []:
        nm = re.search(r'\(rule\s+"([^"]+)"', r)
        mm = re.search(r"\(constraint\s+creepage\s*\(min\s+([0-9.]+)\s*mm\)", r)
        cond = re.search(r'\(condition\s+"([^"]*)"', r)
        m = pat.fullmatch(cond.group(1).strip()) if cond else None
        if nm and mm and m and m.group(1) == m.group(2):
            add(nm.group(1), [n for n in nets if fnmatch.fnmatchcase(n, m.group(1))], float(mm.group(1)))
    return out


def area_layers(design: Design, libs=None) -> dict[str, list[str]]:
    """Named rule areas (zones) of the design's footprints -> the copper layers they cover, in board order."""
    if libs is None:
        from ...core.libs import Libs
        libs = Libs(design=design)
    copper = cu_layers(int(design.board["layers"]))
    out: dict[str, list[str]] = {}
    for c in design.components.values():
        if c.get("virtual") or not c.get("footprint"):
            continue
        try:
            t = libs.footprint_tree(c.get("footprint"))
        except Exception:  # noqa: BLE001 - a missing footprint is reported by the design check
            continue
        for parent in [t] + sexpr.find_all(t, "footprint"):
            for z in sexpr.find_all(parent, "zone"):
                nm = sexpr.value(z, "name")
                lay = sexpr.find(z, "layers") or sexpr.find(z, "layer") or []
                if nm is not None:
                    got = set(out.get(str(nm), [])) | set(pad_layers([str(x) for x in lay[1:]], copper))
                    out[str(nm)] = [L for L in copper if L in got]
    return out


def net_layer_exemptions(design: Design, libs=None) -> tuple[list[str], list[dict]]:
    """`board.drc_rules` with `nets.<name>.layers` taking precedence: a `disallow` rule on a named rule area
    (`A.intersectsArea('Z')`) no longer applies to a net whose own layers list shares a copper layer with Z (the net
    may use those layers there; its other layers are closed to it by the list anyway). The net is excluded with
    `&& A.NetName != '<net>'`, read the same way by KiCad DRC and the router. Only nets the rule names (NetName, or
    NetClass: the net's class or an `iso_` isolation class holding it), or every net when the rule names none.
    Returns (rules, [{rule, area, net, layers}] the exemptions made)."""
    import fnmatch
    rules = [str(r) for r in design.board.get("drc_rules") or []]
    lays = net_layers(design)
    if not lays or not any("disallow" in r and "intersectsArea" in r for r in rules):
        return rules, []
    areas = area_layers(design, libs)
    cls_of: dict[str, set] = {}
    for n in lays:
        cls_of[n] = {str(design.net_info(n).get("class", "signal"))}
    for x in isolation_router_rules(design):
        for n in x["members"]:
            if n in cls_of:
                cls_of[n].add(x["class"])
    out, notes = [], []
    for r in rules:
        cond = re.search(r'\(condition\s+"([^"]*)"', r)
        if "disallow" not in r or not cond or "intersectsArea" not in cond.group(1):
            out.append(r)
            continue
        c = cond.group(1)
        names = re.findall(r"A\.NetName\s*==\s*'([^']+)'", c)
        classes = re.findall(r"A\.NetClass\s*==\s*'([^']+)'", c)
        rname = (re.search(r'\(rule\s+"?([^"\s)]+)', r) or [None, "?"])[1]
        zl = sorted({L for a in re.findall(r"intersectsArea\('([^']+)'\)", c) for L in areas.get(a, [])})
        za = re.findall(r"intersectsArea\('([^']+)'\)", c)
        ex = []
        for n, allowed in lays.items():
            named = any(fnmatch.fnmatchcase(n, p) for p in names) or bool(cls_of[n] & set(classes))
            both = [L for L in allowed if L in zl]
            if both and (named or not (names or classes)):
                ex.append(n)
                notes.append({"rule": rname, "area": ", ".join(za), "net": n, "layers": both})
        if ex:
            new = f"({c}) && " + " && ".join(f"A.NetName != '{n}'" for n in ex)
            r = r[:cond.start(1)] + new + r[cond.end(1):]
        out.append(r)
    return out, notes


def write_project(dirpath: Path, name: str, design: Design):
    """Minimal .kicad_pro with net classes so DRC uses our widths and clearances."""
    import json
    classes = net_classes(design)
    b = design.board
    nc = [{"name": "Default", "clearance": b["clearance_mm"], "track_width": b["track_mm"],
           "via_diameter": b["via_dia_mm"], "via_drill": b["via_drill_mm"], "microvia_diameter": 0.3,
           "microvia_drill": 0.1, "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "pcb_color": "rgba(0,0,0,0.000)",
           "schematic_color": "rgba(0,0,0,0.000)", "wire_width": 6, "bus_width": 12, "line_style": 0}]
    assignments = []
    widths = sorted({(v["width"], v["clearance"]) for v in classes.values()})
    for w, cl in widths:
        if w == b["track_mm"] and cl == b["clearance_mm"]:
            continue
        cname = f"W{w:g}_C{cl:g}"
        nc.append(dict(nc[0], name=cname, track_width=w, clearance=cl))
        for n, v in classes.items():
            if (v["width"], v["clearance"]) == (w, cl):
                assignments.append({"netclass": cname, "pattern": n})
    iso = isolation_router_rules(design)
    for x in iso:  # membership only (no size fields): the aggregate class keeps the width and clearance of the net's own class
        nc.append({"name": x["class"], "priority": 100})
        assignments += [{"netclass": x["class"], "pattern": n} for n in x["members"]]
    pro = {
        # the design's own rules are never below the DRC minimums (a 0.127 mm track board, a 0.4 / 0.2 mm via)
        "board": {"design_settings": {"rules": {"min_clearance": min([b["clearance_mm"], 0.15]
                                                                     + [v["clearance"] for v in classes.values()]),
                                                "min_track_width": min([0.15, float(b["track_mm"])]
                                                                       + [v["width"] for v in classes.values()]),
                                                "min_via_diameter": min(0.45, float(b["via_dia_mm"])),
                                                "min_through_hole_diameter": min(0.2, float(b["via_drill_mm"])),
                                                "min_copper_edge_clearance": EDGE_CLEARANCE_MM,
                                                "min_hole_clearance": min(0.25, float(b["clearance_mm"])),
                                                "min_hole_to_hole": 0.25,
                                                "min_via_annular_width": min(0.1, round((float(b["via_dia_mm"]) - float(
                                                    b["via_drill_mm"])) / 2, 4))}}},
        "meta": {"filename": f"{name}.kicad_pro", "version": 3},
        "net_settings": {"classes": nc, "meta": {"version": 4}, "net_colors": None,
                         "netclass_assignments": None, "netclass_patterns": assignments},
        "sheets": [], "text_variables": {},
    }
    (dirpath / f"{name}.kicad_pro").write_text(json.dumps(pro, indent=2))
    # board.drc_rules: KiCad custom rules (class-pair clearance, creepage), applied by DRC from <name>.kicad_dru;
    # board.isolation: the same rules generated from data (backlog 38)
    dru = dirpath / f"{name}.kicad_dru"
    # board.drc_rules, with nets.<name>.layers winning over a disallow rule area (net_layer_exemptions)
    rules = net_layer_exemptions(design)[0] + [r for g in isolation_groups(design) for r in g["kicad"]]
    rules += [x["rule"] for x in iso]
    if design.raw.get("blocks"):  # the land-pattern rules of the layout blocks
        from ...core import blocks
        rules += blocks.drc_rules(design)
    if rules:
        dru.write_text("(version 1)\n" + "\n".join(rules) + "\n")
    elif dru.exists():
        dru.unlink()


# ------------------------------------------------------------------ Specctra DSN

def _um(v: float) -> float:
    return round(v * 1000, 1)


def _padstack(p: dict, layers_all: list[str]) -> tuple[str, list]:
    if p["kind"] in ("thru_hole", "np_thru_hole"):
        lays = layers_all
    else:
        lays = [l for l in p["layers"] if l.endswith(".Cu")]
        lays = ["F.Cu"] if "F.Cu" in lays else lays
    w, h = _um(p["w"]), _um(p["h"])
    shape = p["shape"]
    shapes = []
    hull = p.get("hull")
    for L in lays:
        if hull:  # custom pad: full outline (anchor + primitives), y flipped for Specctra
            coords = []
            for x, y in hull:
                coords += [_um(x), -_um(y)]
            shapes.append(["shape", ["polygon", L, 0] + coords])
        elif shape == "circle":
            shapes.append(["shape", ["circle", L, w]])
        elif shape == "oval":
            if w > h:
                shapes.append(["shape", ["path", L, h, -(w - h) / 2, 0, (w - h) / 2, 0]])
            else:
                shapes.append(["shape", ["path", L, w, 0, -(h - w) / 2, 0, (h - w) / 2]])
        else:
            shapes.append(["shape", ["rect", L, -w / 2, -h / 2, w / 2, h / 2]])
    name = f"{shape}_{p['kind']}_{w:g}x{h:g}_{'-'.join(lays)}".replace(".", "")
    if hull:
        name += "_h" + str(abs(hash(tuple(hull))) % 100000)
    return name, ["padstack", Q(name)] + shapes + [["attach", "off"]]


def export_dsn(design: Design, libs, parts: dict[str, Part], path: Path):
    b = design.board
    layers = cu_layers(b["layers"])
    classes = net_classes(design)
    W, H = b["width_mm"], b["height_mm"]
    structure = ["structure"]
    for i, L in enumerate(layers):
        structure.append(["layer", L, ["type", "power" if False else "signal"], ["property", ["index", i]]])
    from ...core import floorplan
    shape = floorplan.outline(design, 16)
    if shape.geom_type == "Polygon" and floorplan.cuts(design):  # cut regions: the outline ring, holes as keep-outs
        ring = [c for x, y in shape.exterior.coords for c in (_um(x), -_um(y))]
        structure.append(["boundary", ["path", "pcb", 0] + ring])
        for h in shape.interiors:
            structure.append(["keepout", Q(""), ["polygon", "signal", 0] + [c for x, y in h.coords for c in (_um(x), -_um(y))]])
    else:
        structure.append(["boundary", ["path", "pcb", 0, 0, 0, _um(W), 0, _um(W), -_um(H), 0, -_um(H), 0, 0]])
    via_name = f"Via[0-{len(layers) - 1}]_{_um(b['via_dia_mm']):g}:{_um(b['via_drill_mm']):g}_um"
    structure.append(["via", Q(via_name)])
    structure.append(["rule", ["width", _um(b["track_mm"])], ["clearance", _um(b["clearance_mm"])]])
    placement = ["placement"]
    library = ["library"]
    padstacks = {}
    for ref, part in parts.items():
        c = design.components[ref]
        fp = libs.footprint(c.get("footprint"))
        img = f"{c.get('footprint')}__{ref}"
        placement.append(["component", Q(img), ["place", ref, _um(part.x), -_um(part.y), "front", part.r]])
        image = ["image", Q(img)]
        for p in fp["pads"]:
            if not p["number"] or p["kind"] == "np_thru_hole":
                continue
            name, ps = _padstack(p, layers)
            padstacks[name] = ps
            pin = ["pin", Q(name)]
            if p["rot"]:
                pin.append(["rotate", p["rot"]])
            pin += [Q(p["number"]), _um(p["x"]), -_um(p["y"])]
            image.append(pin)
        # keepout for non-plated holes
        for p in fp["pads"]:
            if p["kind"] == "np_thru_hole":
                image.append(["keepout", Q(""), ["circle", "signal", _um(p["w"]), _um(p["x"]), -_um(p["y"])]])
        library.append(image)
    library += list(padstacks.values())
    vd = _um(b["via_dia_mm"])
    library.append(["padstack", Q(via_name)] + [["shape", ["circle", L, vd]] for L in layers] + [["attach", "off"]])
    wiring = ["wiring"]
    if design.raw.get("blocks"):  # layout blocks: their copper as protected wiring
        from ...core import blocks
        bt, bv = blocks.copper(design, parts)
        wiring += [["wire", ["path", t["layer"], _um(t["w"]), _um(t["x1"]), -_um(t["y1"]), _um(t["x2"]), -_um(t["y2"])],
                    ["net", Q(t["net"])], ["type", "protect"]] for t in bt]
        wiring += [["via", Q(via_name), _um(v["x"]), -_um(v["y"]), ["net", Q(v["net"])], ["type", "protect"]] for v in bv]
    network = ["network"]
    nets = design.nets()
    for net, conns in nets.items():
        pins = [f"{r}-{p}" for r, p in conns if r in parts]
        network.append(["net", Q(net), ["pins"] + pins])
    only = {n: tuple(L) for n, L in net_layers(design).items() if L and set(L) != set(layers)}
    groups: dict[tuple, list] = {}
    for net, cl in classes.items():
        groups.setdefault((cl["width"], cl["clearance"], only.get(net, ())), []).append(net)
    for i, ((w, cl, lays), ns) in enumerate(sorted(groups.items())):
        circuit = ["circuit", ["use_via", Q(via_name)]] + ([["use_layer", *lays]] if lays else [])  # nets.<name>.layers
        network.append(["class", Q(f"c{i}")] + [Q(n) for n in ns] +
                       [circuit, ["rule", ["width", _um(w)], ["clearance", _um(cl)]]])
    tree = ["pcb", Q(str(path)), ["parser", ["string_quote", Q('"')], ["space_in_quoted_tokens", "on"],
                                  ["host_cad", Q("hwflow")], ["host_version", Q("0.1")]],
            ["resolution", "um", 10], ["unit", "um"], structure, placement, library, network, wiring]
    text = sexpr.dumps(tree).replace('(string_quote "\\"")', "(string_quote \")")
    path.write_text(text + "\n")


def read_board(path: Path) -> dict:
    """Parse our own .kicad_pcb: footprints with absolute pads, segments, vias."""
    t = sexpr.loads(path.read_text())
    nets = {int(n[1]): str(n[2]) for n in sexpr.find_all(t, "net") if len(n) > 2}

    def netname(node):
        """KiCad <=9 writes (net 3 "X") or (net 3); KiCad 10 writes (net "X")."""
        if node is None or len(node) < 2:
            return None
        if len(node) > 2:
            return str(node[2])
        v = node[1]
        return nets.get(int(v)) if str(v).isdigit() and not isinstance(v, Q) else str(v)

    fps = {}
    for fp in sexpr.find_all(t, "footprint"):
        at = sexpr.find(fp, "at")
        fx, fy, fr = float(at[1]), float(at[2]), sexpr.at_angle(at)
        ref = next((str(p[2]) for p in sexpr.find_all(fp, "property") if str(p[1]) == "Reference"), None)
        ref = ref or next((str(p[2]) for p in sexpr.find_all(fp, "fp_text") if str(p[1]) == "reference"), None)
        ref = ref if ref and ref not in fps else f"?{len(fps)}"  # older footprints keep the reference in fp_text
        pads = []
        for p in sexpr.find_all(fp, "pad"):
            pat = sexpr.find(p, "at")
            dx, dy = rot(float(pat[1]), float(pat[2]), fr)
            size = sexpr.find(p, "size")
            n = sexpr.find(p, "net")
            ang = sexpr.at_angle(pat)
            w, h = float(size[1]), float(size[2])
            hull = sexpr.custom_pad_hull(p)
            off = (0.0, 0.0)
            if hull:  # custom pad: bounding box of the full outline, centred where the copper really is
                hx, hy = [q[0] for q in hull], [q[1] for q in hull]
                w, h = max(hx) - min(hx), max(hy) - min(hy)
                ox, oy = (max(hx) + min(hx)) / 2, (max(hy) + min(hy)) / 2
                off = rot(ox, oy, ang)
            if ang % 180 == 90:
                w, h = h, w
            pads.append({"number": str(p[1]), "x": fx + dx + off[0], "y": fy + dy + off[1], "w": w, "h": h,
                         "net": netname(n),
                         "kind": str(p[2]), "shape": "custom" if hull else str(p[3]) if len(p) > 3 else "rect",
                         "rratio": float(sexpr.value(p, "roundrect_rratio") or 0.25),  # KiCad's default 0.25
                         "clearance": float(sexpr.value(p, "clearance") or 0.0),  # the pad's own override (0: none)
                         "layers": [str(x) for x in (sexpr.find(p, "layers") or [])[1:]]})
        fps[ref] = {"x": fx, "y": fy, "r": fr, "pads": pads, "footprint": str(fp[1])}
    segs = []
    for s in sexpr.find_all(t, "segment"):
        st, en = sexpr.find(s, "start"), sexpr.find(s, "end")
        segs.append({"x1": float(st[1]), "y1": float(st[2]), "x2": float(en[1]), "y2": float(en[2]),
                     "w": float(sexpr.value(s, "width")), "layer": str(sexpr.value(s, "layer")),
                     "net": netname(sexpr.find(s, "net"))})
    vias = []
    for v in sexpr.find_all(t, "via"):
        at = sexpr.find(v, "at")
        vias.append({"x": float(at[1]), "y": float(at[2]), "net": netname(sexpr.find(v, "net"))})
    return {"footprints": fps, "segments": segs, "vias": vias, "nets": nets}
