"""The `floorplan:` field of design.yaml as data: parsing and validation, regions, cut outlines, conductive
keep-outs, depth, and the regions the placer reads. Pure geometry on the design (shapely); the early feasibility
check that places parts (`check`, `report`, `run`) is `hwflow.layout.place.floorplan`.

Coordinates are board millimetres as in `placement: {x, y}` (origin top left, y down); on a stacked construction
each board has its own origin and sits at `at` in the layout panel."""

from __future__ import annotations

import fnmatch

from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from .blocks import YARD


KINDS = ("mains", "selv", "antenna", "battery", "keepout", "barrier", "other")
EXCLUSIVE = {"mains", "antenna", "keepout", "barrier"}  # a part that is not a member stays out
SIDES = {"front": ("front",), "back": ("back",), "both": ("front", "back")}
CONSTRUCTIONS = ("single", "double", "stacked")
EDGES = ("left", "right", "top", "bottom")
PANEL_GAP_MM = 6.0  # default gap between stacked boards laid side by side in the layout panel

# Fill: courtyard area over the usable area of a board face, and over the pool left outside the exclusive regions and
# the isolation band. Calibrated on the thermostats: the baseline faces that routed clean fill 20-42 %; the
# home-thermostat-3 SELV front face at 88 x 46 fills 74 % (4 unrouted on 4 layers, placement was tight) and at
# 74 x 46 it fills 95 % (unroutable). Above 70 % is a risk, above 85 % the parts do not place.
FILL_MAX = 0.85
FILL_RISK = 0.70
# Routability: RUDY wire of the quick placement over the track capacity of the cells each net may use (the placer's
# per-cell capacity, already derated by placer.CONG_UTIL; mains nets in the mains regions, the others outside them,
# their band, the antenna and keep-outs). Calibrated with the default quick placement (20000 moves, 2 seeds): baseline
# 130 x 40 / 4 layers 5 % (routed clean), home-thermostat-3 88 x 46 / 4 layers 11-13 % (4 unrouted), 74 x 46 /
# 2 layers 24-28 % (95 unrouted).
UTIL_RISK, UTIL_FAIL = 0.10, 0.20
PEAK_RISK = 2.0


class FloorplanError(ValueError):
    pass


def _num_list(v, n=None) -> bool:
    return isinstance(v, (list, tuple)) and (n is None or len(v) == n) and all(isinstance(x, (int, float)) for x in v)


def _geom(r: dict, where: str, errs: list):
    if "rect" in r:
        v = r["rect"]
        if not _num_list(v, 4) or v[2] <= v[0] or v[3] <= v[1]:
            errs.append(f"{where}: rect must be [x0, y0, x1, y1] with x1 > x0 and y1 > y0")
            return None
        return box(*map(float, v))
    if "poly" in r:
        v = r["poly"]
        if not isinstance(v, list) or len(v) < 3 or not all(_num_list(p, 2) for p in v):
            errs.append(f"{where}: poly must be a list of at least 3 [x, y] points")
            return None
        return Polygon([tuple(map(float, p)) for p in v]).buffer(0)
    if "circle" in r:
        v = r["circle"]
        if not _num_list(v, 3) or v[2] <= 0:
            errs.append(f"{where}: circle must be [cx, cy, r] with r > 0")
            return None
        return Point(float(v[0]), float(v[1])).buffer(float(v[2]), 32)
    errs.append(f"{where}: needs rect, poly or circle")
    return None


def _list(v) -> list:
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]


def parse(design) -> dict | None:
    """The normalized floorplan, or None when design.yaml has none. Raises FloorplanError with every problem found."""
    raw = design.raw.get("floorplan")
    if raw is None:
        return None
    errs: list[str] = []
    if not isinstance(raw, dict):
        raise FloorplanError("floorplan must be a mapping")
    cons = raw.get("construction", "single")
    if cons not in CONSTRUCTIONS:
        errs.append(f"floorplan.construction {cons!r} is not single, double or stacked")
    b = design.board
    t = float(b.get("thickness_mm", 1.6))
    boards = []
    if cons == "stacked":
        x = 0.0
        for i, bd in enumerate(raw.get("boards") or []):
            nm = str(bd.get("name", f"board{i + 1}"))
            if not _num_list(bd.get("size_mm"), 2):
                errs.append(f"floorplan.boards.{nm}: size_mm must be [w, h]")
                continue
            w, h = map(float, bd["size_mm"])
            at = bd.get("at", [x, 0.0])
            if not _num_list(at, 2):
                errs.append(f"floorplan.boards.{nm}: at must be [x, y]")
                at = [x, 0.0]
            boards.append({"name": nm, "w": w, "h": h, "x": float(at[0]), "y": float(at[1]),
                           "t": float(bd.get("thickness_mm", t)), "stack_mm": bd.get("stack_mm"),
                           "connector": bd.get("connector"), "flipped": bool(bd.get("flipped", False))})
            x = float(at[0]) + w + PANEL_GAP_MM
        if len(boards) < 2:
            errs.append("floorplan.construction stacked needs at least two boards (floorplan.boards)")
    else:
        if raw.get("boards"):
            errs.append("floorplan.boards is only for construction: stacked")
        boards = [{"name": "board", "w": float(b["width_mm"]), "h": float(b["height_mm"]), "x": 0.0, "y": 0.0, "t": t,
                   "stack_mm": None, "connector": None, "flipped": False}]
    bnames = {bd["name"]: bd for bd in boards}
    if len(bnames) != len(boards):
        errs.append("floorplan.boards: names must be unique")

    def board_of(v, where):
        if cons != "stacked":
            if v is not None:
                errs.append(f"{where}: board is only for construction: stacked")
            return boards[0]["name"]
        if v is None or str(v) not in bnames:
            errs.append(f"{where}: board must be one of {sorted(bnames)}")
            return boards[0]["name"]
        return str(v)

    regions = []
    for i, r in enumerate(raw.get("regions") or []):
        nm = str(r.get("name", f"region{i + 1}"))
        where = f"floorplan.regions.{nm}"
        kind = r.get("kind", "other")
        if kind not in KINDS:
            errs.append(f"{where}: kind {kind!r} is not one of {', '.join(KINDS)}")
        side = r.get("side", "both")
        if side not in SIDES:
            errs.append(f"{where}: side {side!r} is not front, back or both")
            side = "both"
        g = _geom(r, where, errs)
        bd = board_of(r.get("board"), where)
        cut = bool(r.get("cut", False))
        if cut:  # a hole or notch in the board: a keep-out on both faces that no part may enter
            if r.get("kind", "keepout") != "keepout":
                errs.append(f"{where}: a cut region is a keep-out (kind keepout or no kind)")
            if r.get("side", "both") != "both":
                errs.append(f"{where}: a cut goes through the board (side both or no side)")
            if r.get("items"):
                errs.append(f"{where}: a cut region holds no items")
            kind, side = "keepout", "both"
            b0 = next((x for x in boards if x["name"] == bd), None)
            if g is not None and b0 is not None and not g.intersects(box(0, 0, b0["w"], b0["h"])):
                errs.append(f"{where}: the cut lies outside the board")
        iso = r.get("isolation_mm")
        if iso is not None and (not isinstance(iso, (int, float)) or iso < 0):
            errs.append(f"{where}: isolation_mm must be a number >= 0")
            iso = None
        regions.append({"name": nm, "kind": kind, "sides": SIDES[side], "side": side, "board": bd, "geom": g,
                        "iso": float(iso) if iso is not None else None, "from": _list(r.get("from")),
                        "nets": _list(r.get("nets")), "items": _list(r.get("items")), "allow": _list(r.get("allow")),
                        "conductive": bool(r.get("conductive", False)), "source": r.get("source"), "cut": cut,
                        "circle": tuple(map(float, r["circle"])) if cut and g is not None and "circle" in r else None})
    for b0 in boards:  # what the cuts leave of each board must be one piece
        cs = [r["geom"] for r in regions if r["cut"] and r["board"] == b0["name"] and r["geom"] is not None]
        left = box(0, 0, b0["w"], b0["h"]).difference(unary_union(cs)) if cs else None
        if left is not None and left.area < 1e-6:
            errs.append(f"floorplan.regions: the cut regions remove the whole board {b0['name']}")
        elif left is not None and len(getattr(left, "geoms", [left])) > 1:
            errs.append(f"floorplan.regions: the cut regions split board {b0['name']} into "
                        f"{len(left.geoms)} pieces")
    rnames = {r["name"] for r in regions}
    if len(rnames) != len(regions):
        errs.append("floorplan.regions: names must be unique")
    for r in regions:
        for f in r["from"]:
            if f not in rnames and f not in KINDS:
                errs.append(f"floorplan.regions.{r['name']}: from {f!r} is neither a region nor a kind")

    items = []
    for i, it in enumerate(raw.get("items") or []):
        nm = str(it.get("name") or it.get("ref") or f"item{i + 1}")
        where = f"floorplan.items.{nm}"
        size = it.get("size_mm")
        if size is not None and (not _num_list(size, 2) or min(size) <= 0):
            errs.append(f"{where}: size_mm must be [w, h] > 0")
            size = None
        refs = _list(it.get("refs")) + _list(it.get("ref"))
        groups = _list(it.get("groups"))
        fpid = it.get("footprint")
        if fpid is not None and (not isinstance(fpid, str) or ":" not in fpid):
            errs.append(f"{where}: footprint must be a library id Lib:Name")
            fpid = None
        if size is None and not refs and not groups and not fpid:
            errs.append(f"{where}: needs size_mm or footprint (a placeholder) or refs / groups (real parts)")
        env = it.get("envelope_mm")
        if env is not None and (not isinstance(env, (int, float)) or env < 0):
            errs.append(f"{where}: envelope_mm must be a number >= 0")
            env = None
        side = it.get("side", "front")
        if side not in ("front", "back"):
            errs.append(f"{where}: side {side!r} is not front or back")
            side = "front"
        if cons == "single" and side == "back":
            errs.append(f"{where}: side back on a single-face construction")
        reg = it.get("region")
        if reg is not None and str(reg) not in rnames:
            errs.append(f"{where}: region {reg!r} is not defined in floorplan.regions")
        for k in ("height_mm", "protrusion_mm"):
            if it.get(k) is not None and not isinstance(it.get(k), (int, float)):
                errs.append(f"{where}: {k} must be a number")
        at = it.get("at")
        if at is not None and not _num_list(at, 2):
            errs.append(f"{where}: at must be [x, y]")
            at = None
        if it.get("edge") not in (None, "left", "right", "top", "bottom"):
            errs.append(f"{where}: edge must be left, right, top or bottom")
        if it.get("area_mm2") is not None and not isinstance(it.get("area_mm2"), (int, float)):
            errs.append(f"{where}: area_mm2 must be a number")
        items.append({"name": nm, "area_mm2": it.get("area_mm2"), "refs": refs, "groups": groups, "size": tuple(map(float, size)) if size else None,
                      "height": it.get("height_mm"), "protrusion": float(it.get("protrusion_mm") or 0.0),
                      "side": side, "region": str(reg) if reg is not None else None,
                      "board": board_of(it.get("board"), where) if cons == "stacked" or it.get("board") else boards[0]["name"],
                      "count": int(it.get("count", 1)), "at": at, "edge": it.get("edge"), "source": it.get("source"),
                      "footprint": fpid, "envelope": float(env) if env is not None else None})
    inames = [it["name"] for it in items]
    if len(set(inames)) != len(inames):
        errs.append("floorplan.items: names must be unique")
    nets = []
    for i, n in enumerate(raw.get("nets") or []):
        btw = n.get("between") if isinstance(n, dict) else None
        if not (isinstance(btw, list) and len(btw) == 2):
            errs.append(f"floorplan.nets[{i}]: needs between: [item, item]")
            continue
        for x in btw:
            if str(x) not in inames:
                errs.append(f"floorplan.nets[{i}]: {x!r} is not an item")
        nets.append((str(btw[0]), str(btw[1]), int(n.get("count", 1))))
    dp = raw.get("depth") or {}
    lim = dp.get("limit_mm")
    if lim is not None and not isinstance(lim, (int, float)):
        errs.append("floorplan.depth.limit_mm must be a number")
        lim = None
    extra = []
    for e in dp.get("extra") or []:
        if not isinstance(e, dict) or not isinstance(e.get("mm"), (int, float)):
            errs.append("floorplan.depth.extra: each entry needs {name, mm, source}")
            continue
        extra.append({"name": str(e.get("name", "extra")), "mm": float(e["mm"]), "source": e.get("source")})
    wall = raw.get("wall_mm") or {}
    if not isinstance(wall, dict) or any(k not in EDGES or not isinstance(v, (int, float)) or v < 0
                                         for k, v in wall.items()):
        errs.append("floorplan.wall_mm must map left, right, top or bottom to a distance >= 0 (board edge to the "
                    "enclosure wall)")
        wall = {}
    fill = raw.get("fill_max", FILL_MAX)
    if not isinstance(fill, (int, float)) or not 0 < fill <= 1:
        errs.append("floorplan.fill_max must be in (0, 1]")
        fill = FILL_MAX
    if errs:
        raise FloorplanError("; ".join(errs))
    fp = {"construction": cons, "boards": boards, "regions": regions, "items": items, "nets": nets,
          "depth": {"limit_mm": float(lim) if lim is not None else None, "extra": extra}, "fill_max": float(fill),
          "margin": float(b.get("margin_mm", 1.0)), "wall": {k: float(v) for k, v in wall.items()}}
    _shape_regions(fp)
    return fp


def validate(design) -> list[str]:
    """Error messages of the floorplan field (empty when it is valid or absent)."""
    try:
        parse(design)
    except FloorplanError as e:
        return str(e).split("; ")
    return []


def _shape_regions(fp: dict):
    """Board coordinates (panel offset of the region's board) and the effective polygon: the declared shape clipped
    to its board, minus the keep-outs on its sides, minus the isolation distance around a conductive keep-out (a screw
    or a metal frame counts as accessible)."""
    bd = {b["name"]: b for b in fp["boards"]}
    for b in fp["boards"]:
        b["rect"] = box(b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"])
    for r in fp["regions"]:
        b = bd[r["board"]]
        g = r["geom"]
        from shapely import affinity
        r["poly"] = affinity.translate(g, b["x"], b["y"])  # may pass the board edge (a body that overhangs)
        if r["circle"]:
            r["circle"] = (r["circle"][0] + b["x"], r["circle"][1] + b["y"], r["circle"][2])
    for r in fp["regions"]:
        if r["kind"] == "keepout":
            continue
        cut = []
        for k in fp["regions"]:
            if k["kind"] != "keepout" or k["board"] != r["board"] or not set(k["sides"]) & set(r["sides"]):
                continue
            cut.append(k["poly"])
            if k["conductive"] and r["iso"]:
                cut.append(k["poly"].buffer(r["iso"], 32))
        if cut:
            r["poly"] = r["poly"].difference(unary_union(cut))
    for r in fp["regions"]:
        r["area"] = r["poly"].intersection(bd[r["board"]]["rect"]).area


def cuts(design, quad_segs: int | None = None) -> list[dict]:
    """The `cut: true` regions in board coordinates: [{name, poly, circle (cx, cy, r) or None, conductive}]. With
    `quad_segs` a circle is drawn again with that many segments per quarter (the Edge.Cuts writer wants a fine one).
    An invalid floorplan has no cuts (design check reports it)."""
    if not any(isinstance(r, dict) and r.get("cut") for r in ((design.raw.get("floorplan") or {}).get("regions") or [])):
        return []
    try:
        fp = parse(design)
    except FloorplanError:
        return []
    out = []
    for r in fp["regions"] if fp else []:
        if r["cut"]:
            c = r["circle"]
            poly = Point(c[0], c[1]).buffer(c[2], quad_segs) if c and quad_segs else r["poly"]
            out.append({"name": r["name"], "poly": poly, "circle": c, "conductive": r["conductive"]})
    return out


def outline(design, quad_segs: int | None = None):
    """The board shape: the board rectangle minus the cut regions."""
    b = design.board
    rect = box(0, 0, float(b["width_mm"]), float(b["height_mm"]))
    cs = cuts(design, quad_segs)
    return rect.difference(unary_union([c["poly"] for c in cs])) if cs else rect


def conductive_needs(design) -> list[dict]:
    """The distance the nets of a region with `isolation_mm` and `nets` keep from every `conductive: true` keep-out
    or cut on its board and faces (accessible metal: a screw, a frame): [{region, keepout, nets, mm, poly}], poly
    in board coordinates (the keep-out shape, not grown). The router keeps those nets out of the shape grown by mm
    (routing.conductive_keepouts); postlayout.conductive_clearances measures it on the routed board."""
    if not any(isinstance(r, dict) and r.get("conductive")
               for r in ((design.raw.get("floorplan") or {}).get("regions") or [])):
        return []
    try:
        fp = parse(design)
    except FloorplanError:
        return []
    names = sorted(set(design.nets()) | set(design.raw.get("nets") or {}))
    out = []
    for r in fp["regions"] if fp else []:
        if not r["iso"] or not r["nets"] or r["kind"] == "keepout":
            continue
        nets = [n for n in names if any(fnmatch.fnmatchcase(n, g) for g in r["nets"])]
        for k in fp["regions"]:
            if k["kind"] == "keepout" and k["conductive"] and k["board"] == r["board"] and set(k["sides"]) & set(r["sides"]) \
                    and nets and k["poly"] is not None and not k["poly"].is_empty:
                out.append({"region": r["name"], "keepout": k["name"], "nets": nets, "mm": r["iso"], "poly": k["poly"]})
    return out


def net_regions(design) -> list[dict]:
    """The regions that declare `nets` (not keep-outs): [{region, nets, poly}], poly the declared shape in board
    coordinates (keep-outs and isolation bands not taken out: postlayout.conductive_clearances checks those). The
    copper of those nets belongs inside it; postlayout.region_excursions measures what lies outside."""
    if not any(isinstance(r, dict) and r.get("nets")
               for r in ((design.raw.get("floorplan") or {}).get("regions") or [])):
        return []
    try:
        fp = parse(design)
    except FloorplanError:
        return []
    from shapely import affinity
    bd = {b["name"]: b for b in fp["boards"]} if fp else {}
    names = sorted(set(design.nets()) | set(design.raw.get("nets") or {}))
    out = []
    for r in fp["regions"] if fp else []:
        if not r["nets"] or r["kind"] == "keepout":
            continue
        nets = [n for n in names if any(fnmatch.fnmatchcase(n, g) for g in r["nets"])]
        b = bd[r["board"]]
        if nets:
            out.append({"region": r["name"], "nets": nets, "poly": affinity.translate(r["geom"], b["x"], b["y"])})
    return out


def member_net_regions(design) -> list[dict]:
    """With `board.route_groups: members`: every region with local nets (local_nets) as a route-group seed
    [{region, nets, poly}], without the constraints of a declared `nets:` (no corridor proof, no region-nets
    checks). A large board then routes as cached groups of its own (regionroute)."""
    if design.board.get("route_groups") != "members":
        return []
    try:
        fp = parse(design)
    except FloorplanError:
        return []
    if fp is None:
        return []
    from shapely import affinity
    bd = {b["name"]: b for b in fp["boards"]}
    loc = local_nets(design)
    return [{"region": r["name"], "nets": loc[r["name"]],
             "poly": affinity.translate(r["geom"], bd[r["board"]]["x"], bd[r["board"]]["y"])}
            for r in fp["regions"] if r["kind"] != "keepout" and loc.get(r["name"])]

def cut_pad_findings(design, parts: dict, edge_mm: float) -> list[str]:
    """Pads of fixed parts closer than `edge_mm` (the copper-to-edge clearance) to a cut region, or inside one."""
    cs = cuts(design)
    if not cs:
        return []
    out = []
    for ref, p in parts.items():
        c = design.components.get(ref)
        if c is None or not p.fixed:
            continue
        for num in p.pads:
            if num in p.nocu:  # no copper: nothing to keep from the edge
                continue
            x, y = p.pad(num)
            w, h = (p.pad_size or {}).get(num, (0.0, 0.0))
            if p.r % 180:
                w, h = h, w
            g = box(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
            for k in cs:
                d = g.distance(k["poly"])
                if d < edge_mm - 1e-6:
                    out.append(f"{ref}.{num} (fixed) is {d:.2f} mm from cut {k['name']} (copper-to-edge clearance "
                               f"{edge_mm:g} mm)")
    return out


def fixed_pad_isolation(design, parts: dict, fp: dict) -> list[str]:
    """Pads of fixed parts on the nets of a mains region with isolation_mm: each must lie inside the region (which
    already excludes the isolation band around conductive keep-outs) and keep isolation_mm from every conductive
    keep-out. A fixed part is where the design puts it, so a pad in the band is a floorplan failure, not a placement
    task."""
    out = []
    bd = {b["name"]: b for b in fp["boards"]}
    for r in fp["regions"]:
        if r["kind"] != "mains" or not r["nets"] or not r["iso"]:
            continue
        ox, oy = bd[r["board"]]["x"], bd[r["board"]]["y"]
        cond = [k for k in fp["regions"] if k["kind"] == "keepout" and k["conductive"] and k["board"] == r["board"]]
        for ref, p in parts.items():
            c = design.components.get(ref)
            if c is None or not p.fixed:
                continue
            for num, net in c.pins.items():
                if num not in p.pads or num in getattr(p, "nocu", ()) or not any(fnmatch.fnmatchcase(net, g) for g in r["nets"]):
                    continue
                x, y = p.pad(num)
                w, h = (p.pad_size or {}).get(num, (0.0, 0.0))
                if p.r % 180:
                    w, h = h, w
                g = box(x + ox - w / 2, y + oy - h / 2, x + ox + w / 2, y + oy + h / 2)
                near = [(g.distance(k["poly"]), k["name"]) for k in cond]
                bad = [(d, n) for d, n in near if d < r["iso"] - 1e-6]
                if bad:
                    d, n = min(bad)
                    out.append(f"{ref}.{num} ({net}, fixed) is {d:.2f} mm from conductive keep-out {n} "
                               f"(region {r['name']} needs {r['iso']:g} mm)")
                elif not r["poly"].buffer(1e-3).contains(g):
                    out.append(f"{ref}.{num} ({net}, fixed) lies outside region {r['name']} or in its isolation band")
    return out


# ---------------------------------------------------------------- membership


def _refs_of(item: dict, design, refs) -> list[str]:
    out = []
    for r in refs:
        c = design.components.get(r)
        grp = str((c.get("group") if c else None) or "")
        if any(fnmatch.fnmatchcase(r, p) for p in item["refs"]) or (grp and any(fnmatch.fnmatchcase(grp, p) for p in item["groups"])):
            out.append(r)
    return out


def placeholder_names(item: dict) -> list[str]:
    return [item["name"]] if item["count"] <= 1 else [f"{item['name']}#{i + 1}" for i in range(item["count"])]


def resolve(fp: dict, design, refs) -> dict:
    """item name -> the part refs it stands for: real refs when they exist, else its placeholder names."""
    out = {}
    refs = [r for r in refs if r in design.components]
    named = {r for it in fp["items"] for r in refs if any(fnmatch.fnmatchcase(r, p) for p in it["refs"])}
    for it in fp["items"]:  # a ref named in `refs` belongs to that item, not to an item that takes its whole group
        real = _refs_of(it, design, refs) if it["refs"] or it["groups"] else []
        real = [r for r in real if r not in named or any(fnmatch.fnmatchcase(r, p) for p in it["refs"])]
        out[it["name"]] = real or (placeholder_names(it) if it["size"] or it.get("footprint") else [])
    return out


def members(fp: dict, design, refs) -> dict[str, set]:
    """region name -> refs that must lie inside it (items with `region`, and the region's own `items` list of item
    names or ref patterns)."""
    res = resolve(fp, design, refs)
    out = {r["name"]: set() for r in fp["regions"]}
    for it in fp["items"]:
        if it["region"]:
            out[it["region"]] |= set(res[it["name"]])
    for r in fp["regions"]:
        for e in r["items"]:
            out[r["name"]] |= set(res[e]) if e in res else {x for x in refs if fnmatch.fnmatchcase(x, e)}
    return out


def routed_nets(design) -> list[str]:
    """Nets with two or more pads that the router routes (poured ground left out)."""
    gnd = design.ground_nets() if design.board.get("gnd_pour") else set()
    return sorted(n for n, conns in design.nets().items() if n not in gnd and len(conns) > 1)


def local_nets(design) -> dict[str, list[str]]:
    """Region -> the routed nets whose every pad is on a member of that region only: the candidates for the region's
    `nets:`, which make route groups (regionroute). A net on members of two overlapping regions is in neither."""
    fp = parse(design)
    if fp is None:
        return {}
    mem = members(fp, design, list(design.components))
    owner: dict[str, set] = {}
    for r, refs in mem.items():
        for ref in refs:
            owner.setdefault(ref, set()).add(r)
    out = {r["name"]: [] for r in fp["regions"]}
    conns = design.nets()
    for net in routed_nets(design):
        common = set.intersection(*(owner.get(ref, set()) for ref, _ in conns[net]))
        if len(common) == 1:
            out[next(iter(common))].append(net)
    return {r: v for r, v in out.items() if v}

def placer_regions(design, parts) -> tuple[list, dict]:
    """Rules for the placer: ([{name, poly, rect, sides, members, excl, pads}], {ref: start box}). A member keeps its
    courtyard inside the region; an exclusive region (mains, antenna, keepout, barrier) keeps every other part out on
    its sides, except the parts it `allow`s, the members of a barrier region (a relay bridges the mains region and its
    isolation band) and, for a barrier region, the members of a mains region. `pads` are the pads of the region's
    `nets`, which stay inside it. Stacked boards are regions too: their items stay on them."""
    try:
        fp = parse(design)
    except FloorplanError:
        return [], {}
    if fp is None:
        return [], {}
    refs = list(parts)
    mem = members(fp, design, refs)
    res = resolve(fp, design, refs)
    kind_mem = {k: set().union(*[mem[r["name"]] for r in fp["regions"] if r["kind"] == k] or [set()]) for k in KINDS}
    nets = design.nets()
    out, start = [], {}
    for r in fp["regions"]:
        if r["poly"].is_empty:
            continue
        allow = {x for e in r["allow"] for x in (res.get(e) or [y for y in refs if fnmatch.fnmatchcase(y, e)])}
        # a layout block that holds an allowed part is allowed with it: its courtyard (ref block:<name>) carries the part
        allow |= {f"{YARD}{b}" for b, e in (design.raw.get("blocks") or {}).items()
                  if isinstance(e, dict) and set(map(str, (e.get("parts") or {}).values())) & allow}
        if r["cut"]:  # a hole in the board: every part keeps the board margin from its edge, as from the outline
            out.append(_rule(r["name"], r["poly"].buffer(float(design.board["margin_mm"]), join_style="mitre"),
                             r["sides"], set(), True, allow, {}))
            continue
        if r["kind"] in ("mains", "antenna", "keepout"):
            allow |= kind_mem["barrier"]
        if r["kind"] == "barrier":
            allow |= kind_mem["mains"]
        pads = {}
        for n, conns in nets.items():
            if any(fnmatch.fnmatchcase(n, p) for p in r["nets"]):
                for ref, pin in conns:
                    if ref in parts and pin in parts[ref].pads and pin not in parts[ref].nocu:
                        pads.setdefault(ref, []).append(pin)
        out.append(_rule(r["name"], r["poly"], r["sides"], mem[r["name"]] & set(refs), r["kind"] in EXCLUSIVE, allow, pads))
        for x in mem[r["name"]]:
            start[x] = r["poly"].bounds
    if fp["construction"] == "stacked":
        for b in fp["boards"]:
            own = {x for it in fp["items"] if it["board"] == b["name"] for x in res[it["name"]]}
            own |= {x for r in fp["regions"] if r["board"] == b["name"] for x in mem[r["name"]]}
            out.append(_rule(f"board {b['name']}", b["rect"], ("front", "back"), own & set(refs), True, set(), {}))
            for x in own:
                start.setdefault(x, b["rect"].bounds)
    return out, start


def _rule(name, poly, sides, mem, excl, allow, pads) -> dict:
    rect = poly.bounds if abs(poly.area - box(*poly.bounds).area) < 1e-6 else None  # a rectangle: no shapely per move
    return {"name": name, "poly": poly, "rect": rect, "sides": tuple(sides), "members": set(mem), "excl": excl,
            "allow": set(allow), "pads": pads}


# ---------------------------------------------------------------- arithmetic


def depth(fp: dict, heights: list[dict]) -> dict:
    """Depth stack from the tallest item on each face of each board. `heights`: [{name, board, side, mm, source}]
    (a through-hole part also lists its pin protrusion on the other face). Stacked boards are listed from the top
    (face A) down; every front face looks up, so between two boards the gap holds the back face of the upper board
    and the front face of the lower one: gap = max(stack_mm, back + front)."""
    tall = {}
    for h in heights:
        k = (h["board"], h["side"])
        if h["mm"] is not None and (k not in tall or h["mm"] > tall[k]["mm"]):
            tall[k] = h
    lines = []

    def face(b, s):
        h = tall.get((b, s))
        return (h["mm"], h["name"], h.get("source")) if h else (0.0, "nothing", None)

    bs = fp["boards"]
    cons = fp["construction"]
    stack_errs = []
    if cons in ("single", "double"):
        b = bs[0]["name"]
        lines.append(("front face: " + face(b, "front")[1], face(b, "front")[0], face(b, "front")[2]))
        lines.append(("board", bs[0]["t"], "board.thickness_mm"))
        if cons == "double" or face(b, "back")[0]:
            lines.append(("back face: " + face(b, "back")[1], face(b, "back")[0], face(b, "back")[2]))
    else:
        top = bs[0]["name"]
        lines.append((f"{top} front: " + face(top, "front")[1], face(top, "front")[0], face(top, "front")[2]))
        for i, b in enumerate(bs):
            lines.append((f"{b['name']} board", b["t"], "thickness"))
            if i + 1 < len(bs):
                lo = bs[i + 1]["name"]
                (hb, nb, _), (hf, nf, _) = face(b["name"], "back"), face(lo, "front")
                need = hb + hf
                st = b.get("stack_mm")
                if st is not None and float(st) < need - 1e-9:
                    stack_errs.append(f"{b['name']}-{lo} connector stack {float(st):.1f} mm < {need:.1f} mm "
                                      f"({nb} {hb:.1f} + {nf} {hf:.1f})")
                gap = max(float(st or 0.0), need)
                lines.append((f"gap {b['name']}-{lo}: {nb} {hb:.1f} + {nf} {hf:.1f}"
                              + (f", connector stack {float(st):.1f}" if st is not None else ""), gap, None))
        last = bs[-1]["name"]
        lines.append((f"{last} back: " + face(last, "back")[1], face(last, "back")[0], face(last, "back")[2]))
    for e in fp["depth"]["extra"]:
        lines.append((e["name"], e["mm"], e["source"]))
    total = round(sum(x[1] for x in lines), 3)
    lim = fp["depth"]["limit_mm"]
    return {"lines": [{"what": w, "mm": round(mm, 3), "source": s} for w, mm, s in lines], "total_mm": total,
            "limit_mm": lim, "ok": (lim is None or total <= lim + 1e-9) and not stack_errs, "stack_errors": stack_errs}


def region_distances(fp: dict) -> list[dict]:
    """Every region with isolation_mm against the other regions on the same board: kinds in `from` (names or
    kinds), else every region of another kind except barrier and keepout regions. Regions on opposite faces only
    (front against back) are separated by the board and skipped."""
    out = []
    for r in fp["regions"]:
        if not r["iso"]:
            continue
        for o in fp["regions"]:
            if o is r or o["board"] != r["board"] or not set(o["sides"]) & set(r["sides"]):
                continue
            if r["from"]:
                if o["name"] not in r["from"] and o["kind"] not in r["from"]:
                    continue
            elif o["kind"] in (r["kind"], "barrier", "keepout"):
                continue
            if r["poly"].is_empty or o["poly"].is_empty:
                continue
            dist = r["poly"].distance(o["poly"])
            out.append({"a": r["name"], "b": o["name"], "mm": round(dist, 2), "need": r["iso"], "ok": dist >= r["iso"] - 1e-6})
    return out
