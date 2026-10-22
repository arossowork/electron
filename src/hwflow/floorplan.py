"""Early layout feasibility: the `floorplan:` field of design.yaml and `hwflow floorplan`.

The architect writes the floorplan in stage 2: the construction (parts on one face, on two faces, or two stacked
boards), the depth stack, named regions (mains, SELV, antenna, ...) and the items that go into them. Items are
placeholders with datasheet package sizes until stage 4 names the real parts (`refs`); a pre-laid-out block is an
item with a size and a courtyard too. `check` answers in seconds, without simulation or routing: area per region and
per board side, depth against the limit, region geometry and isolation, and a quick placement with a congestion
estimate. In stage 7 the same regions constrain the placer (`placer_regions`).

Coordinates are board millimetres as in `placement: {x, y}` (origin top left, y down); on a stacked construction
each board has its own origin and sits at `at` in the layout panel.
"""

from __future__ import annotations

import copy
import fnmatch
import math
import time

from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

KINDS = ("mains", "selv", "antenna", "battery", "keepout", "barrier", "other")
EXCLUSIVE = {"mains", "antenna", "keepout", "barrier"}  # a part that is not a member stays out
SIDES = {"front": ("front",), "back": ("back",), "both": ("front", "back")}
CONSTRUCTIONS = ("single", "double", "stacked")
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
        iso = r.get("isolation_mm")
        if iso is not None and (not isinstance(iso, (int, float)) or iso < 0):
            errs.append(f"{where}: isolation_mm must be a number >= 0")
            iso = None
        regions.append({"name": nm, "kind": kind, "sides": SIDES[side], "side": side, "board": bd, "geom": g,
                        "iso": float(iso) if iso is not None else None, "from": _list(r.get("from")),
                        "nets": _list(r.get("nets")), "items": _list(r.get("items")), "allow": _list(r.get("allow")),
                        "conductive": bool(r.get("conductive", False)), "source": r.get("source")})
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
        if size is None and not refs and not groups:
            errs.append(f"{where}: needs size_mm (a placeholder) or refs / groups (real parts)")
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
                      "count": int(it.get("count", 1)), "at": at, "edge": it.get("edge"), "source": it.get("source")})
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
    fill = raw.get("fill_max", FILL_MAX)
    if not isinstance(fill, (int, float)) or not 0 < fill <= 1:
        errs.append("floorplan.fill_max must be in (0, 1]")
        fill = FILL_MAX
    if errs:
        raise FloorplanError("; ".join(errs))
    fp = {"construction": cons, "boards": boards, "regions": regions, "items": items, "nets": nets,
          "depth": {"limit_mm": float(lim) if lim is not None else None, "extra": extra}, "fill_max": float(fill),
          "margin": float(b.get("margin_mm", 1.0))}
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
                if num not in p.pads or not any(fnmatch.fnmatchcase(net, g) for g in r["nets"]):
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
        out[it["name"]] = real or (placeholder_names(it) if it["size"] else [])
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
        if r["kind"] in ("mains", "antenna", "keepout"):
            allow |= kind_mem["barrier"]
        if r["kind"] == "barrier":
            allow |= kind_mem["mains"]
        pads = {}
        for n, conns in nets.items():
            if any(fnmatch.fnmatchcase(n, p) for p in r["nets"]):
                for ref, pin in conns:
                    if ref in parts and pin in parts[ref].pads:
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


def _fits(w, h, poly) -> bool:
    """Does a w x h rectangle fit in the polygon in some position, in either rotation (coarse 0.5 mm search)."""
    if poly.is_empty:
        return False
    x0, y0, x1, y1 = poly.bounds
    for a, b in ((w, h), (h, w)):
        if a > x1 - x0 + 1e-9 or b > y1 - y0 + 1e-9:
            continue
        nx, ny = int((x1 - x0 - a) / 0.5) + 1, int((y1 - y0 - b) / 0.5) + 1
        for i in range(nx):
            for j in range(ny):
                if poly.contains(box(x0 + i * 0.5, y0 + j * 0.5, x0 + i * 0.5 + a, y0 + j * 0.5 + b)):
                    return True
        if poly.contains(box(x1 - a, y1 - b, x1, y1)):
            return True
    return False


# ---------------------------------------------------------------- check


def _parts(design, libs, fp: dict):
    """(parts, synthetic design): the real parts of design.yaml plus a placeholder part for each item without real
    refs. A placeholder has one pad per estimated net (floorplan.nets) at its centre."""
    from . import placer
    from .design import Component, Design
    parts = placer.build_parts(design, libs) if design.components else placer.Parts()
    if not design.components:
        parts.areas = []
    for r in [r for r, p in parts.items() if not p.sides]:  # keep-out markers: no copper and no body
        del parts[r]
    res = resolve(fp, design, list(parts))
    raw = copy.deepcopy(design.raw)
    d2 = Design(path=design.path, raw=raw)
    d2.components = {r: c for r, c in design.components.items()}
    pins = {}
    for k, (a, b, n) in enumerate(fp["nets"]):
        for i in range(n):
            for x, other in ((a, b), (b, a)):
                names = res.get(x) or []
                if names and names[0] not in design.components:  # placeholder: one pad per estimated net
                    tgt = names[i % len(names)]
                    pins.setdefault(tgt, {})[f"n{k}_{i}"] = f"~{a}~{b}~{i}"
    m = placer.COURTYARD_MARGIN
    for it in fp["items"]:
        names = res[it["name"]]
        if not names or names[0] in design.components:
            continue
        w, h = it["size"]
        for nm in names:
            pn = pins.get(nm) or {"c": f"~{nm}~nc"}
            p = placer.Part(nm, w + 2 * m, h + 2 * m, 0.0, 0.0, {k: (0.0, 0.0) for k in pn},
                            sides={it["side"]: (w + 2 * m, h + 2 * m, 0.0, 0.0)}, edge=it["edge"],
                            pad_size={k: (0.0, 0.0) for k in pn})
            if it["at"]:
                b = next(b for b in fp["boards"] if b["name"] == it["board"])
                p.x, p.y, p.fixed = b["x"] + it["at"][0], b["y"] + it["at"][1], True
            parts[nm] = p
            d2.components[nm] = Component(nm, {"pins": pn, "role": "none"})
    return parts, d2


def _areas(p, margin) -> dict:
    """side -> courtyard area of a part (without the placer's margin)."""
    return {s: max(0.0, v[0] - 2 * margin) * max(0.0, v[1] - 2 * margin) for s, v in (p.sides or {}).items()}


def _side_box(p, s, margin):
    b = p.side_box(s)
    return None if b is None else (b[0] + margin, b[1] + margin, b[2] - margin, b[3] - margin)


def check(design, libs, iters: int = 20000, seeds: int = 2, place: bool = True) -> dict:
    """Area, depth, region geometry and a quick placement with a congestion estimate. Returns the result dict with
    `ok`, `checks` (each {name, ok (True, False, or None for a risk), msg}) and the numbers behind them."""
    from . import placer
    t0 = time.time()
    try:
        fp = parse(design)
    except FloorplanError as e:
        return {"ok": False, "checks": [{"name": "field", "ok": False, "msg": m} for m in str(e).split("; ")],
                "seconds": round(time.time() - t0, 1)}
    if fp is None:
        return {"ok": False, "checks": [{"name": "field", "ok": False, "msg": "design.yaml has no floorplan field"}],
                "seconds": 0.0}
    m = placer.COURTYARD_MARGIN
    parts, d2 = _parts(design, libs, fp)
    res = resolve(fp, design, list(parts))
    mem = members(fp, design, list(parts))
    checks: list[dict] = []

    def add(name, ok, msg):
        checks.append({"name": name, "ok": ok, "msg": msg})

    real = [r for r in parts if r in design.components]
    for it in fp["items"]:
        if (it["refs"] or it["groups"]) and not (res[it["name"]] and res[it["name"]][0] in design.components):
            if design.components:
                add("items", None if it["size"] else False, f"item {it['name']}: refs {it['refs'] + it['groups']} "
                    f"match no part" + (" (placeholder size used)" if it["size"] else ""))
    # board of each part (stacked: from its item or region; else the only board)
    bname = {}
    for it in fp["items"]:
        for x in res[it["name"]]:
            bname[x] = it["board"]
    for r in fp["regions"]:
        for x in mem[r["name"]]:
            bname.setdefault(x, r["board"])
    if fp["construction"] == "stacked":
        loose = sorted(x for x in parts if x not in bname)
        if loose:
            add("boards", False, f"{len(loose)} parts belong to no board ({', '.join(loose[:8])}"
                + (", ..." if len(loose) > 8 else "") + "): give them an item with board:")
    for x in parts:
        bname.setdefault(x, fp["boards"][0]["name"])
    if fp["construction"] == "single":
        back = sorted(x for x in parts if "back" in (parts[x].sides or {}))
        if back:
            add("construction", False, f"single-face construction but {', '.join(back[:8])} on the back face")

    # ---- area per region and per board side
    fill = fp["fill_max"]
    area = {x: _areas(parts[x], m) for x in parts}
    for x, p in parts.items():  # a fixed body that overhangs the board edge counts with its part on the board
        if p.fixed:
            rect = next(b["rect"] for b in fp["boards"] if b["name"] == bname[x])
            for s_ in area[x]:
                b = _side_box(p, s_, m)
                area[x][s_] = box(*b).intersection(rect).area if b[2] > b[0] and b[3] > b[1] else 0.0
    for it in fp["items"]:  # area_mm2: what the item really takes (a holder on wire leads leaves the board under it free)
        names = [x for x in res[it["name"]] if x in area]
        tot = sum(sum(area[x].values()) for x in names)
        if it["area_mm2"] is not None and tot > 0:
            for x in names:
                area[x] = {s_: v * float(it["area_mm2"]) / tot for s_, v in area[x].items()}
    regions_out = []
    for r in fp["regions"]:
        row = {"name": r["name"], "kind": r["kind"], "board": r["board"], "area_mm2": round(r["area"], 1),
               "members": sorted(mem[r["name"]]), "fill": {}}
        for s in r["sides"]:
            load = sum(area[x].get(s, 0.0) for x in mem[r["name"]] if x in area)
            if r["kind"] == "keepout" or (not load and not mem[r["name"]]):
                continue
            f = load / r["area"] if r["area"] > 0 else math.inf
            row["fill"][s] = round(f, 3)
            if f > 1.0:  # a region is a constraint sized to its parts, not a room: the fill limit is for the faces
                add("area", False, f"region {r['name']} {s}: {load:.0f} of {r['area']:.0f} mm2 = {f:.0%}")
        for x in sorted(mem[r["name"]]):  # a member that cannot fit in any position
            if x not in parts:
                continue
            for s, v in (parts[x].sides or {}).items():
                w, h = v[0] - 2 * m, v[1] - 2 * m
                if not _fits(w, h, r["poly"]):
                    add("fit", False, f"{x} ({w:.1f} x {h:.1f} mm) does not fit in region {r['name']} "
                        f"({_dims(r['poly'])})")
        regions_out.append(row)
    sides_out = []
    for b in fp["boards"]:
        inset = box(b["x"] + fp["margin"], b["y"] + fp["margin"], b["x"] + b["w"] - fp["margin"], b["y"] + b["h"] - fp["margin"])
        for s in ("front", "back"):
            ko = [r["poly"] for r in fp["regions"] if r["board"] == b["name"] and r["kind"] == "keepout" and s in r["sides"]]
            usable = inset.difference(unary_union(ko)) if ko else inset
            on = [x for x in parts if bname[x] == b["name"] and s in area[x]]
            load = sum(area[x][s] for x in on)
            if fp["construction"] == "single" and s == "back" and not load:
                continue
            f = load / usable.area if usable.area > 0 else math.inf
            # the pool left for the parts that are not members of an exclusive region
            excl = [r for r in fp["regions"] if r["board"] == b["name"] and r["kind"] in EXCLUSIVE and r["kind"] != "keepout"
                    and s in r["sides"]]
            band = [r["poly"].buffer(r["iso"]) if r["kind"] == "mains" and r["iso"] else r["poly"] for r in excl]
            pool = usable.difference(unary_union(band)) if band else usable
            in_excl = set().union(*[mem[r["name"]] for r in excl]) if excl else set()
            free = sum(area[x][s] for x in on if x not in in_excl)
            fpool = free / pool.area if pool.area > 0 else (0.0 if not free else math.inf)
            sides_out.append({"board": b["name"], "side": s, "usable_mm2": round(usable.area, 1), "load_mm2": round(load, 1),
                              "fill": round(f, 3), "pool_mm2": round(pool.area, 1), "pool_load_mm2": round(free, 1),
                              "pool_fill": round(fpool, 3)})
            add("area", _band(f, fill), f"{b['name']} {s} face: {load:.0f} of {usable.area:.0f} mm2 = {f:.0%} "
                f"(risk > {FILL_RISK:.0%}, limit {fill:.0%})")
            if excl:
                add("area", _band(fpool, fill), f"{b['name']} {s} face outside {', '.join(r['name'] for r in excl)} and "
                    f"the isolation band: {free:.0f} of {pool.area:.0f} mm2 = {fpool:.0%} (risk > {FILL_RISK:.0%}, "
                    f"limit {fill:.0%})")

    # ---- depth
    heights = []
    for it in fp["items"]:
        names = res[it["name"]]
        hs = it["height"]
        if hs is None:  # real parts may carry height_mm in design.yaml
            hs = max((float(design.components[x].get("height_mm")) for x in names
                      if x in design.components and design.components[x].get("height_mm") is not None), default=None)
        if hs is None:
            if it["size"]:
                add("depth", None, f"item {it['name']}: no height_mm (not in the depth stack)")
            continue
        sides = {s for x in names if x in parts for s in (parts[x].sides or {})} or {it["side"]}
        flip = next(b["flipped"] for b in fp["boards"] if b["name"] == it["board"])
        for s in sides:
            s = ("back" if s == "front" else "front") if flip else s  # a flipped board: its back face looks up
            heights.append({"name": it["name"], "board": it["board"], "side": s, "mm": float(hs), "source": it["source"]})
            if it["protrusion"]:
                o = "back" if s == "front" else "front"
                heights.append({"name": f"{it['name']} pins", "board": it["board"], "side": o, "mm": it["protrusion"],
                                "source": it["source"]})
    dep = depth(fp, heights)
    for e in dep["stack_errors"]:
        add("depth", False, e)
    if dep["limit_mm"] is not None:
        add("depth", dep["total_mm"] <= dep["limit_mm"] + 1e-9,
            f"depth {' + '.join(format(x['mm'], 'g') for x in dep['lines'] if x['mm'])} = {dep['total_mm']:.1f} mm "
            f"(limit {dep['limit_mm']:g} mm)")

    # ---- region geometry
    dists = region_distances(fp)
    for x in dists:
        add("isolation", x["ok"], f"region {x['a']} to {x['b']}: {x['mm']:.2f} mm (need {x['need']:g} mm)")
    for msg in fixed_pad_isolation(design, parts, fp):
        add("isolation", False, msg)
    for r in fp["regions"]:
        if r["kind"] != "keepout" and r["poly"].is_empty:
            add("geometry", False, f"region {r['name']} has no area left after the keep-outs and the board outline")

    # ---- quick placement and congestion
    place_out = None
    if place and parts:
        place_out = _quick_place(design, d2, parts, fp, iters, seeds)
        v = place_out["violations"]
        if v:  # an estimate: a short anneal leaves overlaps that the stage-7 placer (more seeds and moves) removes
            add("placement", None, f"quick placement left {len(v)} violations: {'; '.join(v[:5])}"
                + ("; ..." if len(v) > 5 else ""))
        c = place_out["congestion"]
        ok = True if c["util"] <= UTIL_RISK and c["peak"] <= PEAK_RISK else (False if c["util"] > UTIL_FAIL else None)
        add("routability", ok, f"{design.board.get('layers', 2)} layers: estimated wire {c['demand_mm']:.0f} mm on "
            f"{c['capacity_mm']:.0f} mm of track capacity where each net may run = {c['util']:.0%} (risk > "
            f"{UTIL_RISK:.0%}, fail > {UTIL_FAIL:.0%}); peak cell {c['peak']:.1f} x capacity (risk > {PEAK_RISK:g}); "
            f"{c['overflow']:.1%} of the wire over capacity, {c['detour']:.1%} over forbidden cells")
    ok = all(c["ok"] is not False for c in checks)
    return {"ok": ok, "verdict": "PASS" if ok and all(c["ok"] for c in checks) else ("PASS with risks" if ok else "FAIL"),
            "construction": fp["construction"], "layers": int(design.board.get("layers", 2)),
            "boards": [{k: b[k] for k in ("name", "w", "h", "x", "y", "t")} for b in fp["boards"]],
            "fill_max": fill, "regions": regions_out, "sides": sides_out, "depth": dep, "distances": dists,
            "placement": place_out, "checks": checks, "parts": len(parts), "real_parts": len(real),
            "seconds": round(time.time() - t0, 1), "_fp": fp, "_parts": parts}


def _band(f, fill):
    return True if f <= FILL_RISK else (None if f <= fill else False)


def _dims(poly) -> str:
    x0, y0, x1, y1 = poly.bounds
    return f"{x1 - x0:.1f} x {y1 - y0:.1f} mm"


def _quick_place(design, d2, parts, fp, iters, seeds) -> dict:
    """A short anneal (the stage-7 placer with the region rules), then the congestion of the result."""
    from . import placer
    W, H = float(design.board["width_mm"]), float(design.board["height_mm"])
    if fp["construction"] == "stacked":
        W = max(W, max(b["x"] + b["w"] for b in fp["boards"]))
        H = max(H, max(b["y"] + b["h"] for b in fp["boards"]))
    weights = (design.raw.get("placement") or {}).get("weights")
    best = None
    for seed in range(1, seeds + 1):
        for p in parts.values():
            if not p.fixed:
                p.x = p.y = 0.0
        pl = placer.Placer(d2, parts, W, H, seed=seed, weights=weights)
        cost = pl.run(iters)
        v = pl.violations()
        if _region_viol(v):  # a big part stuck across its region edge: search it away
            before, vb = pl._state(), v
            cost = _settle(pl, v)
            v = pl.violations()
            if _region_viol(v) >= _region_viol(vb):
                pl._restore(before)
                cost, v = pl.cost(), vb
        key = (_region_viol(v), len(v), cost)
        if best is None or key < best[0]:
            best = (key, pl._state(), v, seed)
    (_, _, cost), state, viol, seed = best
    pl._restore(state)
    pl.cost()
    cong = _congestion(d2, pl, fp)
    # nets with pads in more than one region (after the quick placement, by position)
    regs = [(r["name"], r["poly"], r["sides"]) for r in fp["regions"] if r["kind"] != "keepout" and not r["poly"].is_empty]
    cross: dict = {}
    for w, conns in pl.nets:
        where = set()
        for ref, pin in conns:
            x, y = parts[ref].pad(pin)
            names = [n for n, poly, _ in regs if poly.covers(Point(x, y))]
            where.add(names[0] if names else "(free)")
        if len(where) > 1:
            k = " / ".join(sorted(where))
            cross[k] = cross.get(k, 0) + 1
    return {"seed": seed, "iters": iters, "cost": round(cost, 1), "violations": viol, "congestion": cong,
            "crossing": dict(sorted(cross.items(), key=lambda kv: -kv[1])), "crossing_nets": sum(cross.values()),
            "positions": {r: [round(p.x, 2), round(p.y, 2), p.r] for r, p in parts.items()}, "W": W, "H": H,
            "cell": pl.cell}


def _settle(pl, viol, step: float = 2.0) -> float:
    """Each movable part that breaks a floorplan region: the cheapest position on a coarse grid over the bounding box
    of its region, in the four rotations (the placer's `unstick`, restricted to the region, so it takes seconds)."""
    import re
    pl.cost()
    for ref in dict.fromkeys(m.group(1) for x in viol if (m := re.match(r"(\S+) .*floorplan region", x))):
        p = pl.parts[ref]
        bounds = pl.region_start.get(ref)
        if p.fixed or not bounds:
            continue
        x0, y0, x1, y1 = bounds
        best = (pl._total(), p.x, p.y, p.r)
        for a in (0.0, 90.0, 180.0, 270.0):
            for i in range(int((x1 - x0) / step) + 1):
                for j in range(int((y1 - y0) / step) + 1):
                    p.x, p.y, p.r = x0 + i * step, y0 + j * step, a
                    c = pl._update([ref])
                    if c < best[0] - 1e-9:
                        best = (c, p.x, p.y, p.r)
                    pl._undo()
        p.x, p.y, p.r = best[1:]
        pl._update([ref])
    pl.legalize()
    return pl.cost()


def _region_viol(v) -> int:
    return sum("floorplan region" in x for x in v)


def _congestion(d2, pl, fp) -> dict:
    """RUDY demand of the quick placement against the capacity of the area each net may use: a mains net (a member of
    an isolation group or of a mains region's `nets`) routes inside the mains regions, every other net outside them,
    their isolation band and the antenna and keep-out regions, and on its own board. `overflow` is the wire above the
    capacity of allowed cells, `detour` the wire whose bounding box lies over forbidden cells. Nets between stacked
    boards go through the connector and are left out. Capacity per cell is the placer's (layers x cell / track pitch x placer.CONG_UTIL)."""
    import numpy as np
    import shapely

    from . import pcb
    mains_nets = {n for g in pcb.isolation_needs(d2) for n in g["members"]}
    for r in fp["regions"]:
        if r["kind"] == "mains":
            mains_nets |= {n for n in d2.nets() if any(fnmatch.fnmatchcase(n, g) for g in r["nets"])}
    mains = [r["poly"] for r in fp["regions"] if r["kind"] == "mains"]
    block = [r["poly"].buffer(r["iso"]) if r["kind"] == "mains" and r["iso"] else r["poly"]
             for r in fp["regions"] if r["kind"] in ("mains", "antenna", "keepout")]
    c = pl.cell
    xs, ys = np.meshgrid((np.arange(pl.nx) + 0.5) * c, (np.arange(pl.ny) + 0.5) * c, indexing="ij")
    boards = [shapely.contains_xy(b["rect"], xs, ys) for b in fp["boards"]]
    on_board = np.logical_or.reduce(boards)
    in_mains = shapely.contains_xy(unary_union(mains), xs, ys) if mains else np.zeros_like(on_board)
    blocked = shapely.contains_xy(unary_union(block), xs, ys) if block else np.zeros_like(on_board)
    gnd = d2.ground_nets() if d2.board["gnd_pour"] else set()
    dem = {"mains": np.zeros((pl.nx, pl.ny)), "other": np.zeros((pl.nx, pl.ny))}
    P = pl.parts
    for net, conns in d2.nets().items():
        conns = [(r, p) for r, p in conns if r in P and p in P[r].pads]
        if len(conns) < 2 or net in gnd:
            continue
        pts = [P[r].pad(p) for r, p in conns]
        if len({next((k for k, b in enumerate(fp["boards"]) if b["rect"].covers(Point(x, y))), -1) for x, y in pts}) > 1:
            continue  # between stacked boards: through the board-to-board connector
        i0, j0, d = pl._rudy([x for x, _ in pts], [y for _, y in pts])
        dem["mains" if net in mains_nets else "other"][i0:i0 + d.shape[0], j0:j0 + d.shape[1]] += d
    cap_m = np.where(on_board & in_mains, pl.cap, 0.0)
    cap_o = np.where(on_board & ~blocked, pl.cap, 0.0)
    demand = float(dem["mains"].sum() + dem["other"].sum())
    capacity = float(cap_m.sum() + cap_o.sum())
    # over capacity where the net may run; a bounding box that also covers forbidden cells is a detour, reported apart
    over = np.where(cap_m > 0, np.clip(dem["mains"] - cap_m, 0, None), 0) + np.where(cap_o > 0, np.clip(dem["other"] - cap_o, 0, None), 0)
    off = float(np.where(cap_m > 0, 0, dem["mains"]).sum() + np.where(cap_o > 0, 0, dem["other"]).sum())
    cap, tot = cap_m + cap_o, dem["mains"] + dem["other"]
    use = cap > 1e-9
    return {"demand_mm": demand, "capacity_mm": capacity, "util": demand / max(capacity, 1e-9),
            "overflow": float(over.sum()) / max(demand, 1e-9), "detour": off / max(demand, 1e-9),
            "peak": float((tot[use] / cap[use]).max()) if use.any() else 0.0,
            "hot": [(int(i), int(j)) for i, j in zip(*np.nonzero(over > 1e-6))]}


# ---------------------------------------------------------------- outputs


def report(design, res: dict) -> str:
    """build/FLOORPLAN.md text."""
    L = [f"# {design.name}: floorplan check", "", f"Verdict: {res.get('verdict', 'FAIL')} ({res['seconds']} s)", ""]
    if "depth" not in res:
        L += [f"- {c['msg']}" for c in res["checks"]]
        return "\n".join(L) + "\n"
    bs = ", ".join(f"{b['name']} {b['w']:g} x {b['h']:g} mm" for b in res["boards"])
    L += [f"Construction: {res['construction']}; {bs}; {res['layers']} layers; fill limit {res['fill_max']:.0%}; "
          f"{res['parts']} parts ({res['real_parts']} real, {res['parts'] - res['real_parts']} placeholders).", ""]
    bad = [c for c in res["checks"] if c["ok"] is False]
    risk = [c for c in res["checks"] if c["ok"] is None]
    if bad:
        L += ["## Failures", ""] + [f"- {c['name']}: {c['msg']}" for c in bad] + [""]
    if risk:
        L += ["## Risks", ""] + [f"- {c['name']}: {c['msg']}" for c in risk] + [""]
    L += ["## Area", "", "| where | load mm2 | area mm2 | fill |", "|---|---|---|---|"]
    for s in res["sides"]:
        L.append(f"| {s['board']} {s['side']} face | {s['load_mm2']:.0f} | {s['usable_mm2']:.0f} | {s['fill']:.0%} |")
        if s["pool_mm2"] != s["usable_mm2"]:
            L.append(f"| {s['board']} {s['side']}, outside exclusive regions and bands | {s['pool_load_mm2']:.0f} | "
                     f"{s['pool_mm2']:.0f} | {s['pool_fill']:.0%} |")
    for r in res["regions"]:
        for s, f in r["fill"].items():
            L.append(f"| region {r['name']} ({r['kind']}) {s} | | {r['area_mm2']:.0f} | {f:.0%} |")
    L += ["", "## Depth", "", "| item | mm | source |", "|---|---|---|"]
    for x in res["depth"]["lines"]:
        L.append(f"| {x['what']} | {x['mm']:g} | {x['source'] or ''} |")
    lim = res["depth"]["limit_mm"]
    L.append(f"| **total** | **{res['depth']['total_mm']:g}** | limit {lim if lim is not None else 'none'} |")
    if res["distances"]:
        L += ["", "## Region isolation", ""] + [f"- {x['a']} to {x['b']}: {x['mm']:.2f} mm (need {x['need']:g}) "
                                                 f"{'ok' if x['ok'] else 'FAIL'}" for x in res["distances"]]
    p = res.get("placement")
    if p:
        c = p["congestion"]
        L += ["", "## Quick placement", "",
              f"Seed {p['seed']}, {p['iters']} moves, {len(p['violations'])} violations. Congestion (RUDY, "
              f"{p['cell']:g} mm cells): wire {c['demand_mm']:.0f} mm on {c['capacity_mm']:.0f} mm capacity "
              f"({c['util']:.0%}), overflow {c['overflow']:.1%}, peak {c['peak']:.1f} x, {len(c['hot'])} cells over.",
              f"Nets with pads in more than one region: {p['crossing_nets']}"
              + (" (" + "; ".join(f"{k}: {v}" for k, v in list(p["crossing"].items())[:6]) + ")" if p["crossing"] else "")]
        L += ["", "Picture: build/floorplan.svg (front and back face, red cells over capacity)."]
    L += ["", "Thresholds: fill <= limit per region and face; depth <= limit; region distance >= isolation_mm; "
          f"routability fails above {UTIL_FAIL:.0%} of the track capacity, is at risk above {UTIL_RISK:.0%} or a peak "
          f"above {PEAK_RISK:g} x."]
    return "\n".join(L) + "\n"


COLORS = {"mains": "#e4572e", "selv": "#4c9f70", "antenna": "#3b7dd8", "battery": "#d8a13b", "keepout": "#777777",
          "barrier": "#a05cc8", "other": "#999999"}


def svg(res: dict) -> str:
    """Front and back face side by side: regions, parts (members in the region colour), cells over capacity."""
    fp, parts, p = res["_fp"], res["_parts"], res.get("placement")
    W = p["W"] if p else max(b["x"] + b["w"] for b in fp["boards"])
    H = p["H"] if p else max(b["y"] + b["h"] for b in fp["boards"])
    k, pad = 6.0, 20.0
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{(2 * W + 3) * k + 2 * pad:.0f}" height="{H * k + 2 * pad + 20:.0f}" '
           f'font-family="sans-serif" font-size="9">', '<rect width="100%" height="100%" fill="white"/>']
    kind_of = {x: r["kind"] for r in reversed(res["regions"]) for x in r["members"]}
    for col, side in enumerate(("front", "back")):
        ox = pad + col * (W + 3) * k
        out.append(f'<text x="{ox:.1f}" y="{pad - 6:.1f}" font-size="12">{side} face</text>')
        for b in fp["boards"]:
            out.append(f'<rect x="{ox + b["x"] * k:.1f}" y="{pad + b["y"] * k:.1f}" width="{b["w"] * k:.1f}" '
                       f'height="{b["h"] * k:.1f}" fill="#f6f6f0" stroke="black"/>')
        for r in fp["regions"]:
            if side not in r["sides"] or r["poly"].is_empty:
                continue
            geoms = getattr(r["poly"], "geoms", [r["poly"]])
            for g in geoms:
                pts = " ".join(f"{ox + x * k:.1f},{pad + y * k:.1f}" for x, y in g.exterior.coords)
                out.append(f'<polygon points="{pts}" fill="{COLORS[r["kind"]]}" fill-opacity="0.10" '
                           f'stroke="{COLORS[r["kind"]]}" stroke-width="1.5" stroke-dasharray="5 3"/>')
            x0, y0, _, y1 = r["poly"].bounds
            out.append(f'<text x="{ox + max(x0, 0) * k + 3:.1f}" y="{pad + min(y1, H) * k - 3:.1f}" '
                       f'fill="{COLORS[r["kind"]]}" font-weight="bold">{r["name"]}</text>')
        if p:
            c = p["cell"]
            for i, j in p["congestion"]["hot"]:
                out.append(f'<rect x="{ox + i * c * k:.1f}" y="{pad + j * c * k:.1f}" width="{c * k:.1f}" '
                           f'height="{c * k:.1f}" fill="red" fill-opacity="0.35"/>')
        for r, part in parts.items():
            b = part.side_box(side)
            if b is None:
                continue
            m = 0.25
            col_ = COLORS.get(kind_of.get(r), "#555555")
            out.append(f'<rect x="{ox + (b[0] + m) * k:.1f}" y="{pad + (b[1] + m) * k:.1f}" '
                       f'width="{(b[2] - b[0] - 2 * m) * k:.1f}" height="{(b[3] - b[1] - 2 * m) * k:.1f}" '
                       f'fill="{col_}" fill-opacity="0.45" stroke="black" stroke-width="0.5"/>')
            if (b[2] - b[0]) * k > 18:
                out.append(f'<text x="{ox + (b[0] + b[2]) / 2 * k:.1f}" y="{pad + (b[1] + b[3]) / 2 * k + 3:.1f}" '
                           f'text-anchor="middle">{r}</text>')
    out.append(f'<text x="{pad:.1f}" y="{H * k + 2 * pad + 10:.1f}">Verdict: {res["verdict"]}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def run(design, libs, out, iters: int = 20000, seeds: int = 2) -> tuple[str, dict]:
    """`hwflow floorplan`: the check, build/FLOORPLAN.md and build/floorplan.svg. Returns (report text, status data)."""
    res = check(design, libs, iters=iters, seeds=seeds)
    text = report(design, res)
    (out / "FLOORPLAN.md").write_text(text)
    if "_fp" in res:
        (out / "floorplan.svg").write_text(svg(res))
    return text, {k: v for k, v in res.items() if not k.startswith("_")}
