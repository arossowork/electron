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

from shapely.geometry import Point, box
from shapely.ops import unary_union

from ...core.floorplan import EXCLUSIVE, UTIL_FAIL, UTIL_RISK, FILL_RISK, FloorplanError, PEAK_RISK, depth, fixed_pad_isolation, members, parse, region_distances, resolve
from ...core.floorplan import *  # noqa: F401,F403  (the data model moved to core.floorplan; its public names stay here)
from ...core.settings import get_settings


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


FIT_RES = 0.25  # mm grid of the fit search with fixed parts and conductive keep-outs (fit_anywhere)


def fit_anywhere(part, side: str, free, pad_rules: list, margin: float) -> bool:
    """Is there a position and rotation (0, 90, 180, 270) where the courtyard of `part` on `side` (without the
    placer margin) lies in `free` and every pad of `pad_rules` [(pad number, polygon)] lies in its polygon? A raster
    search on a FIT_RES grid, lenient by up to one cell (a fit within FIT_RES counts), so a miss is a proof."""
    import numpy as np
    import shapely
    from shapely.prepared import prep

    from . import placer
    if free.is_empty:
        return False
    x0, y0, x1, y1 = free.bounds
    res = FIT_RES
    nx, ny = int((x1 - x0) / res) + 1, int((y1 - y0) / res) + 1
    xs, ys = np.meshgrid(x0 + (np.arange(nx) + 0.5) * res, y0 + (np.arange(ny) + 0.5) * res, indexing="ij")
    mask = shapely.contains_xy(free, xs, ys).astype(np.int32)
    sat = np.zeros((nx + 1, ny + 1), dtype=np.int64)
    sat[1:, 1:] = mask.cumsum(0).cumsum(1)
    rules = [(n, prep(poly.buffer(res))) for n, poly in pad_rules]
    for a in (0.0, 90.0, 180.0, 270.0):
        q = copy.copy(part)
        q.x, q.y, q.r = 0.0, 0.0, a
        b = q.side_box(side)
        if b is None:
            return True
        bx0, by0, bx1, by1 = b[0] + margin, b[1] + margin, b[2] - margin, b[3] - margin
        # a box that fits holds at least floor(size / res) grid centres in a row, all in `free`: a necessary test
        kx, ky = max(1, int((bx1 - bx0) / res + 1e-9)), max(1, int((by1 - by0) / res + 1e-9))
        if kx > nx or ky > ny:
            continue
        full = sat[kx:, ky:] - sat[:-kx, ky:] - sat[kx:, :-ky] + sat[:-kx, :-ky]
        cand = np.argwhere(full == kx * ky)
        if not len(cand):
            continue
        if not rules:
            return True
        pads = [(placer.part_pad_box(q, n), pr) for n, pr in rules]
        # a spread sample finds a fit quickly; a miss is reported only after every candidate was tried
        step = max(1, len(cand) // 4000)
        order = np.concatenate([cand[::step], np.delete(cand, np.s_[::step], axis=0)]) if step > 1 else cand
        for i, j in order:
            dx, dy = x0 + i * res - bx0, y0 + j * res - by0
            if all(pr.contains(box(pb[0] + dx, pb[1] + dy, pb[2] + dx, pb[3] + dy)) for pb, pr in pads):
                return True
    return False


# ---------------------------------------------------------------- check


def _parts(design, libs, fp: dict):
    """(parts, synthetic design): the real parts of design.yaml plus a placeholder part for each item without real
    refs. A placeholder has one pad per estimated net (floorplan.nets) at its centre."""
    from . import placer
    from ...core.design import Component, Design
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

    def add(name, ok, msg, back_to=None):
        checks.append({"name": name, "ok": ok, "msg": msg, **({"back_to": back_to} if back_to else {})})

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
                        f"({_dims(r['poly'])})", "architecture")
                else:
                    msg = _fit_with_fixed(design, parts, fp, r, x, s, m)
                    if msg:
                        add("fit", False, msg, "architecture")
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
        for msg in place_out["fixed_violations"]:
            add("fixed-placement", False, msg)
        v = place_out["violations"]
        if v:  # an illegal placement is no placement: the floorplan fails (the stage-7 placer would stop there too)
            from .legality import placement_verdict
            pv = placement_verdict(d2, parts, place_out["W"], place_out["H"], v)
            place_out["verdict"] = pv
            add("placement", False, f"quick placement is illegal, {pv['msg'][len('placement illegal: '):]}"
                + "".join(f"; region {g['name']} {g['fill']:.0%} full ({g['load_mm2']:.0f} of {g['area_mm2']:.0f} mm2)"
                          for g in pv["regions"]), pv["back_to"] if pv["back_to"] != "layout" else None)
        if not v:  # the routability prediction of the quick placement (routability.py; a legal placement only)
            from . import routability
            try:
                rp = routability.predict(d2, libs, parts=parts)
                place_out["routability"] = routability.public(rp)
                # a report, not a gate: a prediction short of `routable` is a risk, never a FAIL
                add("routability-prediction", True if rp["verdict"] == "routable" else None, rp["message"])
            except Exception as e:  # noqa: BLE001 - an estimate; the other checks stand without it
                add("routability-prediction", None, f"not run: {type(e).__name__}: {e}")
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


def _fit_with_fixed(design, parts, fp, r, x, side, m) -> str | None:
    """Why member `x` fits nowhere in region `r` on `side` once the fixed parts already there, the keep-outs and,
    for its pads on the nets of a mains region with isolation_mm, that region (which excludes the isolation band
    around conductive keep-outs) are counted; None when it fits, or when none of these applies. A fixed or edge part
    may overhang the board edge (a connector's plug and wire bend)."""
    p = parts[x]
    bd = next(b for b in fp["boards"] if b["name"] == r["board"])
    fixed = {q: parts[q] for q in parts if q != x and parts[q].fixed and parts[q].side_box(side)}
    yards = {q: box(*_side_box(o, side, m)) for q, o in fixed.items()}
    yards = {q: g for q, g in yards.items() if g.intersects(r["poly"])}
    c = design.components.get(x)
    pad_rules, mains = [], []
    for g in fp["regions"]:
        if g["kind"] != "mains" or not g["iso"] or not g["nets"] or g["board"] != r["board"] or g["poly"].is_empty:
            continue
        for num, net in (c.pins.items() if c else []):
            if num in p.pads and num not in p.nocu and any(fnmatch.fnmatchcase(net, pat) for pat in g["nets"]):
                pad_rules.append((num, g["poly"]))
                mains.append((net, g))
    if not yards and not pad_rules:
        return None
    allowed = r["poly"]
    if p.fixed or p.edge:  # off the board: a body may overhang the edge
        allowed = allowed.union(bd["rect"].buffer(60, join_style="mitre").difference(bd["rect"]))
    kos = [k for k in fp["regions"] if k["kind"] == "keepout" and k["board"] == r["board"] and side in k["sides"]]
    areas = [a for a in getattr(parts, "areas", []) or [] if "footprints" in a["forbids"] and a["owner"] != x
             and ({"front": "F.Cu", "back": "B.Cu"}[side] in a["layers"] or "*.Cu" in a["layers"])]
    free = allowed.difference(unary_union([k["poly"] for k in kos] + [a["poly"] for a in areas] + list(yards.values())))
    if fit_anywhere(p, side, free, pad_rules, m):
        return None
    w, h = p.sides[side][0] - 2 * m, p.sides[side][1] - 2 * m
    why = []
    if yards:
        why.append("the fixed parts " + ", ".join(f"{q} ({_span(g)})" for q, g in sorted(yards.items())))
    near = [k["name"] for k in kos if k["poly"].distance(r["poly"]) < 1e-6]
    if near:
        why.append("keep-outs " + ", ".join(near))
    msg = f"{x} ({w:.1f} x {h:.1f} mm, {side}) fits nowhere in region {r['name']}: clear of " + " and ".join(why)
    if pad_rules:
        nets = sorted({n for n, _ in mains})
        cond = sorted({k["name"] for _, g in mains for k in fp["regions"] if k["kind"] == "keepout" and k["conductive"]
                       and k["board"] == g["board"] and k["poly"].distance(g["poly"]) <= g["iso"] + 1e-3})
        g = mains[0][1]
        msg += (f", with its {', '.join(nets)} pads inside region {g['name']}"
                + (f" ({g['iso']:g} mm from conductive {', '.join(cond)})" if cond else ""))
    return msg + " (back to architecture: board size, part or region change)"


def _span(g) -> str:
    x0, y0, x1, y1 = g.bounds
    return f"x {x0:.2f}-{x1:.2f}, y {y0:.2f}-{y1:.2f}"


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
        if v and get_settings().layout.settle:  # what is left: the placer's settle legalizer
            v2 = pl.settle()
            if len(v2) < len(v):
                cost, v = pl.cost(), v2
        key = (_region_viol(v), len(v), cost)
        if best is None or key < best[0]:
            best = (key, pl._state(), v, seed)
    (_, _, cost), state, viol, seed = best
    pl._restore(state)
    pl.cost()
    cong = _congestion(d2, pl, fp)
    # A fixed source position cannot be repaired by annealing. Exempt only
    # courtyard overhang beyond its own board, as the static fit check does.
    boards = {b["name"]: b["rect"].bounds for b in fp["boards"]}
    region_boards = {r["name"]: boards[r["board"]] for r in fp["regions"]}
    if fp["construction"] == "stacked":
        region_boards.update({f"board {name}": rect for name, rect in boards.items()})
    fixed_viol = pl.fixed_violations(region_boards)
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
    return {"seed": seed, "iters": iters, "cost": round(cost, 1), "violations": viol, "fixed_violations": fixed_viol, "congestion": cong,
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

    from ..board import pcb
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
              f"Seed {p['seed']}, {p['iters']} moves, {len(p['violations'])} placement violations, "
              f"{len(p.get('fixed_violations', []))} fixed-rule violations. Congestion (RUDY, "
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
