"""Import a placed KiCad board (.kicad_pcb, KiCad 6 to 10) as an hwflow design: the layout test cases of open
hardware boards (tests/fixtures/layout_corpus/oshw_*).

`import_board(pcb, out)` writes a standalone project to `out`:

- `design.yaml`: one component per footprint that has pads, `fixed: true` at the board's own placement (moved so that
  the outline's bounding box starts at 0, 0), its pad nets as `pins`, value and part numbers from the footprint
  properties; the board (bounding box of Edge.Cuts, copper layer count, thickness), the rules of the `.kicad_pro`
  (default net class: clearance, track, via; other net classes as per-net `width_mm` / `clearance_mm`), the ground
  nets (`class: ground`) and the outline: the parts of the bounding box outside the board and the inner cut-outs
  (slots) as floorplan `cut` regions.
- `lib/footprints/<lib>.pretty/`: every footprint as placed on the board, in footprint coordinates (pad and text
  angles less the footprint rotation; a back-side footprint stays mirrored with its B.* layers, which the board
  writer places as a mirrored `_back` footprint); identical geometry shares one file.
- `lib/symbols/<lib>.kicad_sym`: one generated symbol per footprint, a box with one passive pin per numbered pad,
  so that pin numbers equal pad numbers by construction (the source schematic symbols are not needed for layout).
- `cutout.yaml`: the source, the offset, the net renames, the parts the placer may move (`free`), dropped items.

`human_copper(pcb, offset, net_map)` returns the board's own tracks and vias in the imported coordinates, and
`copper_metrics` summarizes them (the human reference of a routing benchmark).

Nothing here is specific to a board: names, rules and geometry come from the files. Limits: copper zones other than
the ground pour, keep-out rule areas, custom DRC rules and blind/buried vias are recorded, not imported; only 2 and 4
copper layers (the board writer's stack-ups)."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path

import yaml

from ...core import sexpr
from ...core.geometry import rot
from ...core.sexpr import Q

GROUND_RE = re.compile(r"^(?:[ADP]?GND[A-Z0-9_]*|GND|VSS|0V|EARTH|PE)$", re.I)
# reference prefixes the placer does not move in a placement benchmark: connectors, mechanical parts, switches, test
# points, fiducials (their position is the product's, not the layout's)
FIXED_PREFIXES = ("J", "P", "CN", "X", "H", "MH", "MK", "M", "SW", "S", "BT", "TP", "FID", "REF", "LOGO", "G", "K",
                  "U_ANT", "AE", "ANT")
DROP_ITEMS = ("uuid", "tstamp", "path", "sheetname", "sheetfile", "net", "locked", "at", "layer", "model",
              "embedded_fonts", "component_classes", "units", "duplicate_pad_numbers_are_jumpers", "jumper_pad_groups")
KEEP_PROPS = ("Reference", "Value")
PART_PROPS = {"mpn": ("MPN", "Manufacturer Part Number", "Manufacturer_Part_Number", "MFR_PN", "MFN", "Part Number",
                      "PartNumber", "mpn", "Mfr. Part #", "Manufacturer PN", "Mfr Part Number", "MPN#"),
              "manufacturer": ("Manufacturer", "MANUFACTURER", "Mfr", "MFR", "Manufacturer_Name"),
              "lcsc": ("LCSC", "LCSC Part", "LCSC Part #", "LCSC_PN", "JLCPCB Part", "JLC", "LCSC#", "lcsc")}
MIN_CUT_MM2 = 1.0  # outline pieces smaller than this (rounded corners) are not cut regions
SYM_PITCH = 2.54


class UnsupportedBoard(ValueError):
    """The board cannot be imported (unsupported stack-up, no outline)."""


# ------------------------------------------------------------------ reading


def _f(x) -> float:
    return float(x)


def _prop(fp: list, name: str) -> str | None:
    for p in sexpr.find_all(fp, "property"):
        if len(p) > 2 and str(p[1]) == name:
            return str(p[2])
    for t in sexpr.find_all(fp, "fp_text"):  # KiCad 6
        if len(t) > 2 and str(t[1]) == name.lower():
            return str(t[2])
    return None


def _props(fp: list) -> dict[str, str]:
    out = {}
    for p in sexpr.find_all(fp, "property"):
        if len(p) > 2:
            out[str(p[1])] = str(p[2])
    for p in sexpr.find_all(fp, "fp_text"):
        if len(p) > 2 and str(p[1]) in ("reference", "value"):
            out.setdefault(str(p[1]).capitalize(), str(p[2]))
    return out


def _net_table(t: list) -> dict[int, str]:
    return {int(n[1]): str(n[2]) for n in sexpr.find_all(t, "net") if len(n) > 2 and str(n[1]).lstrip("-").isdigit()}


def _netname(node, table: dict[int, str]) -> str | None:
    """`(net 3 "X")` (KiCad <= 9) or `(net "X")` (KiCad 10)."""
    if node is None or len(node) < 2:
        return None
    if len(node) > 2:
        return str(node[2]) or None
    v = node[1]
    if not isinstance(v, Q) and str(v).lstrip("-").isdigit():
        return table.get(int(v)) or None
    return str(v) or None


def copper_layers(t: list) -> list[str]:
    lay = sexpr.find(t, "layers") or []
    out = []
    for item in lay[1:]:
        if isinstance(item, list) and len(item) > 2 and str(item[1]).endswith(".Cu") \
                and str(item[2]) in ("signal", "power", "mixed", "jumper"):
            out.append(str(item[1]))
    return out


def _arc_points(st, mid, en, n=16) -> list[tuple[float, float]]:
    """Points along a three-point arc (start, mid, end)."""
    (x1, y1), (x2, y2), (x3, y3) = st, mid, en
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return [st, en]
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1) + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3) + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1, a2, a3 = (math.atan2(y - uy, x - ux) for x, y in (st, mid, en))

    def ccw(a, b):  # angle from a to b, counter-clockwise in [0, 2pi)
        return (b - a) % (2 * math.pi)
    span = ccw(a1, a3)
    if ccw(a1, a2) > span:  # mid is not on the ccw path: go clockwise
        span -= 2 * math.pi
    pts = [(ux + r * math.cos(a1 + span * k / n), uy + r * math.sin(a1 + span * k / n)) for k in range(n + 1)]
    pts[0], pts[-1] = st, en  # exact ends: the arc meets its neighbours
    return pts


def _legacy_arc(item) -> tuple:
    """KiCad 6 footprint `fp_arc (start c) (end p) (angle a)`: centre, start point, sweep -> (start, mid, end)."""
    c, p = sexpr.find(item, "start"), sexpr.find(item, "end")
    cx, cy, px, py = _f(c[1]), _f(c[2]), _f(p[1]), _f(p[2])
    a = math.radians(_f(sexpr.value(item, "angle", 0)))
    r, a0 = math.hypot(px - cx, py - cy), math.atan2(py - cy, px - cx)
    pt = [(cx + r * math.cos(a0 + a * k / 2), cy + r * math.sin(a0 + a * k / 2)) for k in range(3)]
    return pt[0], pt[1], pt[2]


def _xy(node) -> tuple[float, float]:
    return _f(node[1]), _f(node[2])


def _edge_paths(t: list) -> list[list[tuple[float, float]]]:
    """Edge.Cuts items of the board and of its footprints as polylines (board mm)."""
    out = []

    def shapes(items, tf):
        for it in items:
            if not isinstance(it, list) or not it:
                continue
            head = str(it[0])
            if not head.startswith(("gr_", "fp_")) or str(sexpr.value(it, "layer", "")) != "Edge.Cuts":
                continue
            kind = head[3:]
            if kind == "line":
                out.append([tf(_xy(sexpr.find(it, "start"))), tf(_xy(sexpr.find(it, "end")))])
            elif kind == "rect":
                (x0, y0), (x1, y1) = _xy(sexpr.find(it, "start")), _xy(sexpr.find(it, "end"))
                out.append([tf(p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))])
            elif kind == "arc":
                if sexpr.find(it, "mid"):
                    st, mid, en = (_xy(sexpr.find(it, k)) for k in ("start", "mid", "end"))
                else:
                    st, mid, en = _legacy_arc(it)
                out.append([tf(p) for p in _arc_points(st, mid, en)])
            elif kind == "circle":
                (cx, cy), (ex, ey) = _xy(sexpr.find(it, "center")), _xy(sexpr.find(it, "end"))
                r = math.hypot(ex - cx, ey - cy)
                out.append([tf((cx + r * math.cos(2 * math.pi * k / 48), cy + r * math.sin(2 * math.pi * k / 48)))
                            for k in range(49)])
            elif kind in ("poly", "curve"):
                pts = []
                for q in (sexpr.find(it, "pts") or [])[1:]:
                    if isinstance(q, list) and q[0] == "xy":
                        pts.append(_xy(q))
                    elif isinstance(q, list) and q[0] == "arc":  # KiCad 8+: arcs inside a polygon
                        pts += _arc_points(*(_xy(sexpr.find(q, k)) for k in ("start", "mid", "end")))
                if kind == "poly" and pts:
                    pts.append(pts[0])
                if len(pts) > 1:
                    out.append([tf(p) for p in pts])

    shapes(t, lambda p: p)
    for fp in sexpr.find_all(t, "footprint"):
        at = sexpr.find(fp, "at")
        fx, fy, fr = _f(at[1]), _f(at[2]), sexpr.at_angle(at)

        def tf(p, fx=fx, fy=fy, fr=fr):
            dx, dy = rot(p[0], p[1], fr)
            return fx + dx, fy + dy
        shapes(fp, tf)
    return out


def outline(t: list):
    """(board polygon, bounding box) of Edge.Cuts: the largest closed region, its holes the inner cut-outs."""
    from shapely.geometry import LineString, MultiPoint, Polygon
    from shapely.ops import polygonize, unary_union
    paths = _edge_paths(t)
    if not paths:
        raise UnsupportedBoard("no Edge.Cuts outline")
    # ends a drawing tool left a micrometre apart are one point
    paths = [[(round(x, 3), round(y, 3)) for x, y in p] for p in paths]
    lines = unary_union([LineString(p) for p in paths if len(p) > 1])
    polys = list(polygonize(lines))
    hull = MultiPoint([q for p in paths for q in p]).convex_hull
    if polys and unary_union(polys).area < 0.5 * hull.area:  # the outline does not close: only small faces found
        polys = []
    if not polys:  # an outline with gaps: close it over its points
        return hull, hull.bounds
    # a face is board when an odd number of outline rings enclose it (a slot inside the board is even)
    rings = [Polygon(p.exterior) for p in polys]
    board = [p for p in polys if sum(r.contains(p.representative_point()) for r in rings) % 2 == 1]
    shape = unary_union(board or polys)
    return shape, shape.bounds


# ------------------------------------------------------------------ names


def safe_name(s: str, extra: str = "") -> str:
    s = re.sub(r"[^A-Za-z0-9_+\-." + re.escape(extra) + "]", "_", s.strip())
    return re.sub(r"_+", "_", s).strip("_") or "X"


def net_rename(nets: list[str]) -> dict[str, str]:
    """KiCad net name -> design net name (sheet paths and punctuation become `_`; collisions get a suffix)."""
    out, used = {}, set()
    for n in sorted(nets, key=lambda x: (x.count("/"), x)):
        base = safe_name(n.lstrip("/"))
        if base.upper() in ("NC", "~"):
            base = f"{base}_net"
        m, k = base, 2
        while m in used:
            m, k = f"{base}_{k}", k + 1
        used.add(m)
        out[n] = m
    return out


# ------------------------------------------------------------------ footprints and symbols


def localize(fp: list) -> list:
    """A footprint of a board in footprint coordinates: pad and text angles less the footprint rotation, board-level
    zones moved into the footprint frame, nets, ids, sheet paths and 3D models dropped."""
    at = sexpr.find(fp, "at")
    fx, fy, fr = _f(at[1]), _f(at[2]), sexpr.at_angle(at)
    out = ["footprint", Q(str(fp[1]).split(":")[-1]), ["version", "20241229"], ["generator", Q("hwflow-import")],
           ["layer", Q(str(sexpr.value(fp, "layer", "F.Cu")))]]
    for item in fp[2:]:
        if not isinstance(item, list) or not item:
            continue
        head = item[0]
        if head in DROP_ITEMS or head == "version" or head == "generator" or head == "generator_version":
            continue
        item = copy.deepcopy(item)
        if head == "property" and str(item[1]) not in KEEP_PROPS:
            continue
        if head in ("property", "fp_text", "pad"):
            pat = sexpr.find(item, "at")
            if pat is not None:
                sexpr.set_at_angle(pat, round((sexpr.at_angle(pat) - fr) % 360, 6))
        if head == "pad":
            item = [x for x in item if not (isinstance(x, list) and x and x[0] in ("net", "uuid", "tstamp",
                                                                                      "pinfunction", "pintype"))]
        if head == "zone":
            item = [x for x in item if not (isinstance(x, list) and x and x[0] in ("net", "net_name", "uuid",
                                                                                      "tstamp", "filled_polygon"))]
            for poly in (x for x in item if isinstance(x, list) and x and x[0] == "polygon"):
                for xy in (p for p in (sexpr.find(poly, "pts") or [])[1:] if isinstance(p, list) and p[0] == "xy"):
                    lx, ly = rot(_f(xy[1]) - fx, _f(xy[2]) - fy, -fr)
                    xy[1], xy[2] = round(lx, 4), round(ly, 4)
        out.append(_strip_ids(item))
    return out


def _strip_ids(node):
    if not isinstance(node, list):
        return node
    return [_strip_ids(x) for x in node if not (isinstance(x, list) and x and x[0] in ("uuid", "tstamp"))]


def geometry_key(local: list) -> str:
    """Hash of a localized footprint's copper and graphics, without its texts (reference, value and user texts move
    per instance), item order and float noise; a pad of a shape symmetric under 180 degrees counts its angle modulo
    180 (identical land patterns share a file)."""
    def canon(x):
        if isinstance(x, list):
            if x and x[0] == "pad" and len(x) > 3 and str(x[3]) in ("rect", "roundrect", "oval", "circle"):
                x = copy.deepcopy(x)
                at = sexpr.find(x, "at")
                if at is not None:
                    sexpr.set_at_angle(at, round(sexpr.at_angle(at) % 180, 3))
            return [canon(y) for y in x]
        if isinstance(x, Q):
            return Q(x)
        try:
            return f"{float(x):.3f}"
        except ValueError:
            return x
    body = sorted(sexpr.dumps(canon(x)) for x in local[2:]
                  if isinstance(x, list) and x and x[0] not in ("property", "fp_text", "version", "generator"))
    return hashlib.sha1("\n".join(body).encode()).hexdigest()[:12]


def pad_numbers(local: list) -> list[str]:
    """Numbers of the pads with copper and a number, in natural order (one symbol pin each)."""
    from ...core.design import natkey
    nums = []
    for p in sexpr.find_all(local, "pad"):
        n = str(p[1])
        lay = [str(x) for x in (sexpr.find(p, "layers") or [])[1:]]
        if n and n not in nums and any(L.endswith(".Cu") or L == "*.Cu" for L in lay):
            nums.append(n)
    return sorted(nums, key=natkey)


def symbol(name: str, pins: list[str]) -> list:
    """A box symbol with one passive pin per pad number, half on each side."""
    half = (len(pins) + 1) // 2
    hgt = max(half, 1) * SYM_PITCH
    top = round(hgt / 2, 2)
    font = ["effects", ["font", ["size", 1.27, 1.27]]]
    hidden = ["effects", ["font", ["size", 1.27, 1.27]], ["hide", "yes"]]
    body = [["rectangle", ["start", -5.08, top + 1.27], ["end", 5.08, round(top - hgt - 1.27, 2)],
             ["stroke", ["width", 0.254], ["type", "default"]], ["fill", ["type", "background"]]]]
    for i, n in enumerate(pins):
        left = i < half
        y = round(top - SYM_PITCH * (i if left else i - half) - SYM_PITCH / 2, 2)
        body.append(["pin", "passive", "line", ["at", -7.62 if left else 7.62, y, 0 if left else 180], ["length", 2.54],
                     ["name", Q(n), font], ["number", Q(n), font]])
    return ["symbol", Q(name), ["exclude_from_sim", "yes"], ["in_bom", "yes"], ["on_board", "yes"],
            ["property", Q("Reference"), Q("U"), ["at", 0, top + 3.0, 0], font],
            ["property", Q("Value"), Q(name), ["at", 0, round(top - hgt - 3.0, 2), 0], font],
            ["property", Q("Footprint"), Q(""), ["at", 0, 0, 0], hidden],
            ["property", Q("Datasheet"), Q(""), ["at", 0, 0, 0], hidden],
            ["symbol", Q(f"{name}_1_1"), *body]]


def numeric_value(ref: str, value: str) -> str:
    """The value of an R, C or L (hwflow checks they parse as numbers): the first token that parses ('0.1uF 100V' ->
    '0.1uF'); '1' when none does (a ferrite bead '600R@100MHz'). Other parts keep their value."""
    from ...core.units import parse_value
    m = re.match(r"[A-Za-z]+", ref)
    if not m or m.group(0).upper() not in ("R", "C", "L"):
        return value
    value = value.split("@")[0]  # a ferrite bead: impedance @ frequency
    for tok in [value] + re.split(r"[\s/,;_]+", value):
        try:
            parse_value(tok)
            return tok
        except ValueError:
            continue
    return "1"


# ------------------------------------------------------------------ project rules


def project_rules(pro: Path | None) -> dict:
    """Rules of a .kicad_pro: {default: {clearance, track_width, via_diameter, via_drill}, classes: {name: {...}},
    patterns: [(glob or regex, class)], assignments: {net: class}, min: {...}}."""
    if pro is None or not Path(pro).exists():
        return {}
    try:
        j = json.loads(Path(pro).read_text())
    except (ValueError, OSError):
        return {}
    ns = j.get("net_settings") or {}
    classes = {}
    assign = dict(ns.get("netclass_assignments") or {})
    for c in ns.get("classes") or []:
        classes[c.get("name", "Default")] = {k: c.get(k) for k in ("clearance", "track_width", "via_diameter",
                                                                   "via_drill") if c.get(k) is not None}
        for n in c.get("nets") or []:  # KiCad 6
            assign[n] = c.get("name")
    pats = [(p.get("pattern"), p.get("netclass")) for p in ns.get("netclass_patterns") or [] if p.get("pattern")]
    rules = ((j.get("board") or {}).get("design_settings") or {}).get("rules") or {}
    return {"default": classes.get("Default", {}), "classes": classes, "patterns": pats,
            "assignments": {k: (v[0] if isinstance(v, list) and v else v) for k, v in assign.items()},
            "min": {k: rules.get(k) for k in ("min_clearance", "min_track_width", "min_via_diameter",
                                              "min_through_hole_diameter", "min_copper_edge_clearance")
                    if rules.get(k) is not None}}


def _net_class(net: str, rules: dict) -> str | None:
    import fnmatch
    if net in (rules.get("assignments") or {}):
        return rules["assignments"][net]
    for pat, cls in rules.get("patterns") or []:
        try:
            if fnmatch.fnmatchcase(net, pat) or re.fullmatch(pat, net):
                return cls
        except re.error:
            if fnmatch.fnmatchcase(net, pat):
                return cls
    return None


def _pcb_setup_rules(t: list) -> dict:
    """Older boards keep net classes in the board file (`(net_class ...)`, KiCad 5 style), rarely still present."""
    out = {}
    for nc in sexpr.find_all(t, "net_class"):
        name = str(nc[1])
        d = {k: _f(sexpr.value(nc, src)) for k, src in (("clearance", "clearance"), ("track_width", "trace_width"),
                                                       ("via_diameter", "via_dia"), ("via_drill", "via_drill"))
             if sexpr.value(nc, src) is not None}
        out[name] = {"rule": d, "nets": [str(a[1]) for a in sexpr.find_all(nc, "add_net")]}
    return out


# ------------------------------------------------------------------ import


def _side(fp: list) -> str:
    return "back" if str(sexpr.value(fp, "layer", "F.Cu")) == "B.Cu" else "front"


def _attrs(fp: list) -> set[str]:
    a = sexpr.find(fp, "attr")
    return {str(x) for x in a[1:]} if a else set()


def import_board(pcb: Path, out: Path, *, name: str | None = None, pro: Path | None = None,
                 source: dict | None = None, lib: str | None = None, fixed_prefixes=FIXED_PREFIXES) -> dict:
    """Write the design of a placed KiCad board to `out` (see the module docstring); returns the cutout.yaml record."""
    pcb, out = Path(pcb), Path(out)
    t = sexpr.loads(pcb.read_text(encoding="utf-8", errors="replace"))
    if not t or t[0] != "kicad_pcb":
        raise UnsupportedBoard(f"{pcb}: not a KiCad board")
    version = int(str(sexpr.value(t, "version", "0")) or 0)
    if version < 20211014:
        raise UnsupportedBoard(f"{pcb}: board format {version} is older than KiCad 6 (20211014)")
    cu = copper_layers(t)
    if len(cu) not in (2, 4):
        raise UnsupportedBoard(f"{pcb}: {len(cu)} copper layers (the board writer has 2- and 4-layer stack-ups)")
    name = safe_name(name or pcb.stem)
    lib = lib or safe_name(f"oshw_{name}")
    pro = Path(pro) if pro else pcb.with_suffix(".kicad_pro")
    table = _net_table(t)
    shape, (x0, y0, x1, y1) = outline(t)
    W, H = round(x1 - x0, 3), round(y1 - y0, 3)
    warnings: list[str] = []

    # ---- footprints
    fps, dropped = [], []
    for fp in sexpr.find_all(t, "footprint"):
        pads = sexpr.find_all(fp, "pad")
        ref = _prop(fp, "Reference") or ""
        if not pads:
            dropped.append(ref or str(fp[1]))
            continue
        fps.append(fp)
    nets_used = set()
    for fp in fps:
        for p in sexpr.find_all(fp, "pad"):
            n = _netname(sexpr.find(p, "net"), table)
            if n:
                nets_used.add(n)
    rename = net_rename(sorted(nets_used))
    # pins of a net (part, pad number): a net on one pin is not a connection (KiCad `unconnected-(...)` nets, or the
    # pads of one number, a connector shell, on a net of their own)
    pins_of: dict[str, set] = {}
    for i, fp in enumerate(fps):
        for p in sexpr.find_all(fp, "pad"):
            n = _netname(sexpr.find(p, "net"), table)
            if n and str(p[1]):
                pins_of.setdefault(n, set()).add((i, str(p[1])))
    count = {n: len(v) for n, v in pins_of.items()}

    refs_used: set[str] = set()
    variants: dict[tuple, str] = {}  # (geometry key) -> footprint file name
    fp_files: dict[str, list] = {}
    sym_pins: dict[str, list[str]] = {}
    comps, free = {}, []
    for fp in fps:
        props = _props(fp)
        ref0 = props.get("Reference") or "X"
        ref = safe_name(ref0)
        if not re.match(r"^[A-Za-z]", ref) or "*" in ref0:
            ref = "X" + re.sub(r"[^A-Za-z0-9]", "", ref) if ref else "X"
        r, k = ref, 2
        while r in refs_used:
            r, k = f"{ref}_{k}", k + 1
        if r != ref0:
            warnings.append(f"reference {ref0!r} -> {r}")
        ref = r
        refs_used.add(ref)
        at = sexpr.find(fp, "at")
        fx, fy, fr = _f(at[1]), _f(at[2]), sexpr.at_angle(at)
        local = localize(fp)
        key = geometry_key(local)
        base = safe_name(str(fp[1]).split(":")[-1]) + ("_back" if _side(fp) == "back" else "")
        if key not in variants:
            nm, i = base, 2
            while nm in fp_files:
                nm, i = f"{base}_v{i}", i + 1
            variants[key] = nm
            local[1] = Q(nm)
            fp_files[nm] = local
            sym_pins[nm] = pad_numbers(local)
            if not sym_pins[nm]:  # no numbered copper pad (an NPTH mounting hole): a dummy pad without copper
                local.append(["pad", Q("1"), "smd", "rect", ["at", 0, 0], ["size", 0.1, 0.1], ["layers", Q("Dwgs.User")]])
                sym_pins[nm] = ["1"]
        fpname = variants[key]
        pins, nc, seen = {}, [], {}
        for p in sexpr.find_all(fp, "pad"):
            num = str(p[1])
            if not num or num not in sym_pins[fpname]:
                continue
            n = _netname(sexpr.find(p, "net"), table)
            if num in seen:
                if n and seen[num] and n != seen[num]:
                    warnings.append(f"{ref}: pads {num} on two nets ({seen[num]}, {n}); kept {seen[num]}")
                if n and not seen[num]:
                    seen[num] = n
                continue
            seen[num] = n
        for num in sym_pins[fpname]:
            n = seen.get(num)
            if n and count.get(n, 0) > 1:
                pins[num] = rename[n]
            else:
                nc.append(num)
        if sym_pins[fpname] == ["1"] and not pins and "1" not in seen:  # the dummy pad of a mounting hole
            nc, pins = [], {"1": "NC"}
        elif not pins:  # every pad unconnected (a spare connector): hwflow wants a pin map
            pins = {nc.pop(0): "NC"}
        attrs = _attrs(fp)
        value = props.get("Value") or fpname
        c = {"value": value}
        num = numeric_value(ref, value)
        if num != value:
            c["value"], c["source_value"] = num, value
        for k2, names in PART_PROPS.items():
            v = next((props[x] for x in names if props.get(x) and props[x] not in ("~", "-", "NA", "N/A")), None)
            if v:
                c[k2] = v
        c["spice"] = False  # a layout case: no simulation model
        c["symbol"] = f"{lib}:{fpname}"
        c["footprint"] = f"{lib}:{fpname}"
        if "exclude_from_bom" in attrs or "board_only" in attrs:
            c["bom"] = False
        if "dnp" in attrs or (sexpr.find(fp, "dnp") is not None):
            c["source_dnp"] = True
        c["pins"] = pins
        if nc:
            c["nc"] = nc
        c["placement"] = {"x": round(fx - x0, 4), "y": round(fy - y0, 4), "rot": round(fr % 360, 4), "fixed": True}
        comps[ref] = c
    # a prefix list match must be the whole letter prefix (R1 is not "REF", P is not "PE")
    free = [r for r in comps if comps[r]["pins"] and re.match(r"^[A-Za-z_]+", r).group(0).upper()
            not in {p.upper() for p in fixed_prefixes}]

    # ---- rules
    rules = project_rules(pro)
    dflt = rules.get("default") or {}
    old = _pcb_setup_rules(t)
    if not dflt and "Default" in old:
        dflt = old["Default"]["rule"]
    board = {"width_mm": W, "height_mm": H, "layers": len(cu)}
    th = sexpr.value(sexpr.find(t, "general") or [], "thickness")
    if th:
        board["thickness_mm"] = round(_f(th), 3)
    for k_src, k_dst in (("clearance", "clearance_mm"), ("track_width", "track_mm"),
                         ("via_diameter", "via_dia_mm"), ("via_drill", "via_drill_mm")):
        if dflt.get(k_src):
            board[k_dst] = round(float(dflt[k_src]), 4)
    nets_out = {}
    for n_kicad, n in rename.items():
        if count.get(n_kicad, 0) < 2:
            continue
        info = {}
        cls = _net_class(n_kicad, rules) if rules else None
        if cls is None:
            cls = next((c for c, v in old.items() if n_kicad in v["nets"]), None)
        cr = (rules.get("classes") or {}).get(cls) if cls else None
        cr = cr if cr is not None else (old.get(cls, {}).get("rule") if cls else None)
        if cr and cls != "Default":
            if cr.get("track_width") and abs(float(cr["track_width"]) - board.get("track_mm", 0.25)) > 1e-6:
                info["width_mm"] = round(float(cr["track_width"]), 4)
            if cr.get("clearance") and abs(float(cr["clearance"]) - board.get("clearance_mm", 0.2)) > 1e-6:
                info["clearance_mm"] = round(float(cr["clearance"]), 4)
        if GROUND_RE.match(n):
            info["class"] = "ground"
        if cls and cls != "Default":
            info["source_class"] = cls
        if info:
            nets_out[n] = info

    # ---- outline as floorplan cut regions
    from shapely.geometry import box
    regions = []
    bb = box(x0, y0, x1, y1)
    rest = bb.difference(shape)
    pieces = [g for g in getattr(rest, "geoms", [rest]) if not g.is_empty and g.area >= MIN_CUT_MM2]
    for i, g in enumerate(sorted(pieces, key=lambda g: (-g.area, g.bounds))):
        g = g.simplify(0.02, preserve_topology=True)
        for j, part in enumerate(getattr(g, "geoms", [g])):
            pts = [[round(px - x0, 3), round(py - y0, 3)] for px, py in list(part.exterior.coords)[:-1]]
            if len(pts) >= 3:
                regions.append({"name": f"outline_cut{len(regions) + 1}", "cut": True, "poly": pts})
    outline_ratio = round(shape.area / max(bb.area, 1e-9), 4)

    raw = {"project": f"{lib}", "revision": "A",
           "description": f"imported KiCad board {pcb.name}" + (f" ({source.get('url')})" if source else ""),
           "board": board, "components": comps}
    if nets_out:
        raw["nets"] = nets_out
    if regions:
        raw["floorplan"] = {"regions": regions}

    # ---- what is not imported
    zones = {}
    for z in sexpr.find_all(t, "zone"):
        n = _netname(sexpr.find(z, "net"), table) or (str(sexpr.value(z, "net_name", "")) or None)
        if sexpr.find(z, "keepout") is not None:
            zones.setdefault("__keepout__", 0)
            zones["__keepout__"] += 1
        elif n:
            zones[rename.get(n, n)] = zones.get(rename.get(n, n), 0) + 1
    dru = pro.with_suffix(".kicad_dru") if pro else None
    blind = sum(1 for v in sexpr.find_all(t, "via") if len(v) > 1 and str(v[1]) in ("blind", "micro", "buried"))

    # ---- files
    out.mkdir(parents=True, exist_ok=True)
    fpdir = out / "lib" / "footprints" / f"{lib}.pretty"
    if fpdir.exists():
        for f in fpdir.glob("*.kicad_mod"):
            f.unlink()
    fpdir.mkdir(parents=True, exist_ok=True)
    for nm, local in fp_files.items():
        (fpdir / f"{nm}.kicad_mod").write_text(sexpr.dumps(local) + "\n")
    symdir = out / "lib" / "symbols"
    symdir.mkdir(parents=True, exist_ok=True)
    symlib = ["kicad_symbol_lib", ["version", "20231120"], ["generator", Q("hwflow-import")]]
    symlib += [symbol(nm, pins) for nm, pins in sym_pins.items()]
    (symdir / f"{lib}.kicad_sym").write_text(sexpr.dumps(symlib) + "\n")
    (out / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False, width=120, allow_unicode=True))
    rec = {"source": source or {"file": str(pcb)}, "source_project": pcb.stem, "importer": "hwflow.layout.board.kicad_import",
           "source_format": version, "copper_layers": cu, "offset": [round(-x0, 4), round(-y0, 4)],
           "board_mm": [W, H], "outline_fill": outline_ratio, "free": sorted(free),
           "dropped_footprints": sorted(set(dropped)),
           "net_renames": {k: v for k, v in sorted(rename.items()) if k != v},
           "not_imported": {"zones": zones, "custom_rules": bool(dru and dru.exists()), "blind_vias": blind,
                            "rules_source": str(pro.name) if pro and pro.exists() else None},
           "terminals": [], "warnings": warnings}
    (out / "cutout.yaml").write_text(yaml.safe_dump(rec, sort_keys=False, width=120, allow_unicode=True))
    return rec


# ------------------------------------------------------------------ the human copper


def human_copper(pcb: Path, offset=(0.0, 0.0), rename: dict | None = None) -> dict:
    """Tracks (arcs as chords through their midpoint) and vias of a board, moved by `offset`, nets renamed."""
    t = sexpr.loads(Path(pcb).read_text(encoding="utf-8", errors="replace"))
    table = _net_table(t)
    dx, dy = offset
    ren = rename or {}

    def nn(node):
        n = _netname(node, table)
        return ren.get(n, n) if n else None
    tracks, vias = [], []
    for s in sexpr.find_all(t, "segment"):
        (a, b), (c, d) = _xy(sexpr.find(s, "start")), _xy(sexpr.find(s, "end"))
        tracks.append({"x1": a + dx, "y1": b + dy, "x2": c + dx, "y2": d + dy, "w": _f(sexpr.value(s, "width")),
                       "layer": str(sexpr.value(s, "layer")), "net": nn(sexpr.find(s, "net"))})
    for s in sexpr.find_all(t, "arc"):
        pts = _arc_points(*(_xy(sexpr.find(s, k)) for k in ("start", "mid", "end")), n=8)
        for (a, b), (c, d) in zip(pts, pts[1:]):
            tracks.append({"x1": a + dx, "y1": b + dy, "x2": c + dx, "y2": d + dy, "w": _f(sexpr.value(s, "width")),
                           "layer": str(sexpr.value(s, "layer")), "net": nn(sexpr.find(s, "net")), "arc": True})
    for v in sexpr.find_all(t, "via"):
        x, y = _xy(sexpr.find(v, "at"))
        vias.append({"x": x + dx, "y": y + dy, "net": nn(sexpr.find(v, "net")),
                     "size": _f(sexpr.value(v, "size", 0) or 0), "drill": _f(sexpr.value(v, "drill", 0) or 0)})
    return {"tracks": tracks, "vias": vias}


def human_zones(pcb: Path, offset=(0.0, 0.0), rename: dict | None = None) -> list[list]:
    """The board's copper zones and rule areas, moved by `offset`, nets renamed, fills dropped (KiCad refills them):
    the planes of the human layout (a power net on a pour is connected by it, not by tracks)."""
    t = sexpr.loads(Path(pcb).read_text(encoding="utf-8", errors="replace"))
    table = _net_table(t)
    dx, dy = offset
    out = []
    for z in sexpr.find_all(t, "zone"):
        n = _netname(sexpr.find(z, "net"), table) or (str(sexpr.value(z, "net_name", "")) or None)
        n = (rename or {}).get(n, n) if n else None
        z = [x for x in z if not (isinstance(x, list) and x and x[0] in ("net", "net_name", "filled_polygon",
                                                                        "fill_segments", "uuid", "tstamp"))]
        for poly in (x for x in z if isinstance(x, list) and x and x[0] == "polygon"):
            for q in (sexpr.find(poly, "pts") or [])[1:]:
                pts = [q] if isinstance(q, list) and q[0] == "xy" else [
                    sexpr.find(q, k) for k in ("start", "mid", "end")] if isinstance(q, list) and q[0] == "arc" else []
                for xy in (p for p in pts if p):
                    xy[1], xy[2] = round(_f(xy[1]) + dx, 4), round(_f(xy[2]) + dy, 4)
        out.append({"net": n, "zone": z})
    return out


def copper_metrics(cop: dict) -> dict:
    """Track length (total and per layer), segments, vias, nets with copper, of a copper set."""
    per = {}
    for s in cop["tracks"]:
        per[s["layer"]] = per.get(s["layer"], 0.0) + math.hypot(s["x2"] - s["x1"], s["y2"] - s["y1"])
    return {"track_mm": round(sum(per.values()), 1), "per_layer_mm": {k: round(v, 1) for k, v in sorted(per.items())},
            "segments": len(cop["tracks"]), "vias": len(cop["vias"]),
            "nets_with_copper": len({s["net"] for s in cop["tracks"] if s["net"]} |
                                    {v["net"] for v in cop["vias"] if v["net"]})}
