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
import re
import time

from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from ...core.floorplan import EDGES, EXCLUSIVE, UTIL_FAIL, UTIL_RISK, FILL_RISK, FloorplanError, PEAK_RISK, cuts, depth, fixed_pad_isolation, members, outline, parse, placer_regions, region_distances, resolve
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
    rules = [(n, poly.buffer(res)) for n, poly in pad_rules]
    for _, g in rules:
        shapely.prepare(g)
    rules = [(n, g, prep(g)) for n, g in rules]
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
        pads = [(placer.part_pad_box(q, n), g, pr) for n, g, pr in rules]
        # necessary test, vectorised: the four corners of every pad box on or in its polygon (a box inside a
        # polygon has its corners there); only the candidates that pass get the exact polygon test
        keep = np.ones(len(cand), dtype=bool)
        dx, dy = x0 + cand[:, 0] * res - bx0, y0 + cand[:, 1] * res - by0
        for pb, g, _ in pads:
            for cx, cy in ((pb[0], pb[1]), (pb[2], pb[1]), (pb[0], pb[3]), (pb[2], pb[3])):
                keep &= shapely.intersects_xy(g, cx + dx, cy + dy)
        cand = cand[keep]
        # a spread sample finds a fit quickly; a miss is reported only after every candidate was tried
        step = max(1, len(cand) // 4000)
        order = np.concatenate([cand[::step], np.delete(cand, np.s_[::step], axis=0)]) if step > 1 else cand
        pads = [(pb, pr) for pb, _, pr in pads]
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
    from .fpsynth import is_synthetic
    pins, syn = {}, {}
    for k, (a, b, n) in enumerate(fp["nets"]):
        for i in range(n):
            for x, other in ((a, b), (b, a)):
                names = res.get(x) or []
                if names and names[0] not in design.components:  # placeholder: one pad per estimated net
                    tgt = names[i % len(names)]
                    pins.setdefault(tgt, {})[f"n{k}_{i}"] = f"~{a}~{b}~{i}"
                elif names and is_synthetic(design, names[0]):  # a drawn item: on a pad without a net of its own
                    syn.setdefault(names[i % len(names)], []).append(f"~{a}~{b}~{i}")
    for ref, nets in syn.items():
        if ref not in parts:
            continue
        c = d2.components[ref]
        free = [n for n in parts[ref].pads if n not in c.pins]
        if free:
            pn = dict(c.pins)
            for j, net in enumerate(nets):
                pn.setdefault(free[j % len(free)], net)
            d2.components[ref] = Component(ref, {**c.data, "pins": pn})
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
            d2.components[nm] = Component(nm, {"pins": pn, "role": "none", "side": it["side"]})  # board_fill reads it
    return parts, d2


def _areas(p, margin) -> dict:
    """side -> courtyard area of a part (without the placer's margin)."""
    return {s: max(0.0, v[0] - 2 * margin) * max(0.0, v[1] - 2 * margin) for s, v in (p.sides or {}).items()}


def _side_box(p, s, margin):
    b = p.side_box(s)
    return None if b is None else (b[0] + margin, b[1] + margin, b[2] - margin, b[3] - margin)


def check(design, libs, iters: int = 20000, seeds: int = 2, place: bool = True, extra=None, stage: str = "full") -> dict:
    """Area, depth, region geometry, connector edges and envelopes, isolation groups, and a quick placement with a
    congestion estimate. `extra(design, libs) -> [{name, ok, msg, back_to?}]` adds checks of a higher layer (the
    feasibility proofs, layout.feasibility.floorplan_checks). Returns the result dict with `ok`, `checks` (each
    {name, ok (True, False, or None for a risk), msg}) and the numbers behind them.

    Footprint and pin-row items become parts with real pads (fpsynth). `stage="arch"` is the stage-2 mechanical
    check: only the mechanical parts (footprint, pin-row and box items, and of a captured design the fixed,
    through-hole or large parts) are checked, as fixed copper with their nets; the rest of the circuit (`area_mm2`
    items, the other captured parts) is an area budget; no quick placement (_arch_scope, _arch_checks)."""
    from . import placer
    from . import fpsynth
    t0 = time.time()
    if stage == "arch":
        place = False
    try:
        fp = parse(design)
    except FloorplanError as e:
        return {"ok": False, "checks": [{"name": "field", "ok": False, "msg": m} for m in str(e).split("; ")],
                "seconds": round(time.time() - t0, 1)}
    if fp is None:
        return {"ok": False, "checks": [{"name": "field", "ok": False, "msg": "design.yaml has no floorplan field"}],
                "seconds": 0.0}
    m = placer.COURTYARD_MARGIN
    checks: list[dict] = []

    def add(name, ok, msg, back_to=None):
        checks.append({"name": name, "ok": ok, "msg": msg, **({"back_to": back_to} if back_to else {})})

    syn: list[dict] = []
    design, libs, made = fpsynth.synth(design, libs, fp, syn)
    for c in syn:
        add(c["name"], c["ok"], c["msg"])
    rest = {"face": {}, "pool": {}, "items": []}  # the area budget of the parts not drawn (stage arch)
    excl_names = {r["name"] for r in fp["regions"] if r["kind"] in EXCLUSIVE and r["kind"] != "keepout"}
    for it in fp["items"]:  # area_mm2 alone: parts not drawn yet, counted in the face fill only
        if it["area_mm2"] is not None and not it["size"] and not it["footprint"] and not it["pin_rows"] \
                and not it["refs"] and not it["groups"]:
            k = (it["board"], it["side"])
            rest["face"][k] = rest["face"].get(k, 0.0) + float(it["area_mm2"])
            inr = it["region"] in excl_names or any(it["name"] in r["items"] for r in fp["regions"] if r["name"] in excl_names)
            if not inr:
                rest["pool"][k] = rest["pool"].get(k, 0.0) + float(it["area_mm2"])
            rest["items"].append(it["name"])
    design_full = design
    if stage == "arch":
        design = _arch_scope(design, libs, fp, rest)
    for it in fp["items"]:  # a footprint chosen in the parts stage gives a placeholder its real courtyard
        if it["footprint"] and not it["size"] and it["name"] not in made:
            try:
                b = libs.footprint(it["footprint"])["bbox"]
                it["size"] = (b[2] - b[0], b[3] - b[1])
            except Exception as e:  # noqa: BLE001 - a missing footprint is a finding, not a crash
                add("field", False, f"item {it['name']}: footprint {it['footprint']} not found ({type(e).__name__}: {e})")
                it["size"] = (1.0, 1.0)
    parts, d2 = _parts(design, libs, fp)
    keep_out = {it["name"]: it["keepout"] for it in fp["items"] if it["keepout"]}
    for x, p in parts.items():  # an item's keepout_mm grows its courtyard (room to fit a cell, a lever, a tool)
        c = design.components.get(x)
        k = keep_out.get(c.get("floorplan_item")) if c is not None and c.get("floorplan_item") else None
        if k and p.sides:
            p.sides = {s: (v[0] + 2 * k, v[1] + 2 * k, v[2], v[3]) for s, v in p.sides.items()}
            p.w, p.h = p.w + 2 * k, p.h + 2 * k
    res = resolve(fp, design, list(parts))
    mem = members(fp, design, list(parts))

    real = [r for r in parts if r in design.components and not fpsynth.is_synthetic(design, r)]
    drawn = [r for r in parts if fpsynth.is_synthetic(design, r)]
    res_full = res if design_full is design else resolve(fp, design_full, list(design_full.components))
    for it in fp["items"]:
        if (it["refs"] or it["groups"]) and not (res_full[it["name"]] and res_full[it["name"]][0] in design_full.components):
            if design_full.components:
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
            load = sum(area[x][s] for x in on) + rest["face"].get((b["name"], s), 0.0)
            if fp["construction"] == "single" and s == "back" and not load:
                continue
            f = load / usable.area if usable.area > 0 else math.inf
            # the pool left for the parts that are not members of an exclusive region
            excl = [r for r in fp["regions"] if r["board"] == b["name"] and r["kind"] in EXCLUSIVE and r["kind"] != "keepout"
                    and s in r["sides"]]
            band = [r["poly"].buffer(r["iso"]) if r["kind"] == "mains" and r["iso"] else r["poly"] for r in excl]
            pool = usable.difference(unary_union(band)) if band else usable
            in_excl = set().union(*[mem[r["name"]] for r in excl]) if excl else set()
            free = sum(area[x][s] for x in on if x not in in_excl) + rest["pool"].get((b["name"], s), 0.0)
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

    # ---- connector edges and isolation groups (before any placement: they need only sizes, regions and nets)
    if fp["construction"] != "stacked":
        for name, ok, msg, back in _edge_checks(d2, parts, fp, res, m) + _isolation_checks(d2, parts, fp, area):
            add(name, ok, msg, back)

    # ---- quick placement and congestion
    place_out = None
    if parts and fp["construction"] != "stacked":  # the legality gate's own fill, before any placement
        from .legality import PLACE_FILL_MAX, board_fill
        W, H = float(design.board["width_mm"]), float(design.board["height_mm"])
        mg = fp["margin"]
        outl = outline(design)
        usable = outl.buffer(-mg, join_style="mitre").area if cuts(design) else (W - 2 * mg) * (H - 2 * mg)
        for s, v in board_fill(d2, parts, W, H).items():
            f = v * (outl.area if cuts(design) else W * H) / max(usable, 1e-9)
            if f > PLACE_FILL_MAX:
                k = math.sqrt(f / PLACE_FILL_MAX)
                add("area", False, f"{s} face: the courtyards with the placer margin fill {f:.0%} of the board inside "
                    f"its {mg:g} mm margin; above {PLACE_FILL_MAX:.0%} no legal placement exists (the stage-7 "
                    f"legality gate stops there): a board of at least {math.ceil((W - 2 * mg) * k + 2 * mg)} x "
                    f"{math.ceil((H - 2 * mg) * k + 2 * mg)} mm, or fewer or smaller parts, or parts on the other "
                    f"face", "architecture")
    hopeless = [c for c in checks if c["ok"] is False and c["name"] in ("area", "fit", "edge", "isolation", "depth")]
    if place and parts and hopeless:  # fail fast: an area, fit or edge proof already fails, a placement cannot help
        add("placement", None, f"quick placement skipped: {len(hopeless)} area, fit, edge, isolation or depth "
            f"failures above already send the floorplan back")
        place = False
    if place and parts:
        place_out = _quick_place(design, d2, parts, fp, iters, seeds)
        for msg in place_out["fixed_violations"]:
            ok, why = _fixed_severity(parts, fp, msg, m)
            add("fixed-placement", ok, msg + why, None if ok is None else "architecture")
        v = place_out["violations"]
        if v:  # an illegal placement is no placement: the floorplan fails (the stage-7 placer would stop there too)
            from .legality import placement_verdict
            pv = placement_verdict(d2, parts, place_out["W"], place_out["H"], v)
            place_out["verdict"] = pv
            place_out["illegal"] = illegal_parts(parts, v)
            add("placement", False, f"quick placement is illegal, {pv['msg'][len('placement illegal: '):]}"
                + "".join(f"; region {g['name']} {g['fill']:.0%} full ({g['load_mm2']:.0f} of {g['area_mm2']:.0f} mm2)"
                          for g in pv["regions"]), pv["back_to"] if pv["back_to"] != "layout" else None)
            add("placement", False, "illegal placement, by part: " + illegal_text(place_out["illegal"])
                + "; no routability or congestion estimate from an illegal placement",
                pv["back_to"] if pv["back_to"] != "layout" else None)
        if not v:  # the routability prediction of the quick placement (routability.py; a legal placement only)
            from . import routability
            try:
                rp = routability.predict(d2, libs, parts=parts)
                place_out["routability"] = routability.public(rp)
                # a report, not a gate: a prediction short of `routable` is a risk, never a FAIL
                add("routability-prediction", True if rp["verdict"] == "routable" else None, rp["message"])
            except Exception as e:  # noqa: BLE001 - an estimate; the other checks stand without it
                add("routability-prediction", None, f"not run: {type(e).__name__}: {e}")
    if place_out is not None and not place_out["violations"]:  # a congestion figure of an illegal placement misleads
        c = place_out["congestion"]
        ok = True if c["util"] <= UTIL_RISK and c["peak"] <= PEAK_RISK else (False if c["util"] > UTIL_FAIL else None)
        add("routability", ok, f"{design.board.get('layers', 2)} layers: estimated wire {c['demand_mm']:.0f} mm on "
            f"{c['capacity_mm']:.0f} mm of track capacity where each net may run = {c['util']:.0%} (risk > "
            f"{UTIL_RISK:.0%}, fail > {UTIL_FAIL:.0%}); peak cell {c['peak']:.1f} x capacity (risk > {PEAK_RISK:g}); "
            f"{c['overflow']:.1%} of the wire over capacity, {c['detour']:.1%} over forbidden cells")
    placed = bool(place and place_out is not None and not place_out["violations"])  # an illegal placement is no position
    if fp["construction"] != "stacked" and (placed or any(p.fixed for p in parts.values())):
        for name, ok, msg, back in _envelope_checks(parts, fp, res, m, placed):  # fixed or quick-placed (legal only)
            add(name, ok, msg, back)
    arch = None
    if stage == "arch" and parts:
        arch = _arch_checks(design, parts, fp, mem, rest, sides_out, m)
        for name, ok, msg, back in arch.pop("checks"):
            add(name, ok, msg, back)
    if extra is not None:
        try:
            for c in extra(design, libs):
                add(c["name"], c["ok"], c["msg"], c.get("back_to"))
        except Exception as e:  # noqa: BLE001 - a crashed proof is a risk to look at, not a lost floorplan check
            add("proofs", None, f"feasibility proofs not run: {type(e).__name__}: {e}")
    ok = all(c["ok"] is not False for c in checks)
    return {"ok": ok, "verdict": "PASS" if ok and all(c["ok"] for c in checks) else ("PASS with risks" if ok else "FAIL"),
            "construction": fp["construction"], "layers": int(design.board.get("layers", 2)),
            "boards": [{k: b[k] for k in ("name", "w", "h", "x", "y", "t")} for b in fp["boards"]],
            "fill_max": fill, "regions": regions_out, "sides": sides_out, "depth": dep, "distances": dists,
            "placement": place_out, "checks": checks, "parts": len(parts), "real_parts": len(real),
            "drawn_parts": len(drawn), "stage": stage, "arch": arch, "rest_items": rest["items"],
            "seconds": round(time.time() - t0, 1), "_fp": fp, "_parts": parts}


ARCH_BIG_MM2 = 100.0  # stage arch: a captured part with a courtyard this large is mechanical (a module, a holder)


def _arch_scope(design, libs, fp: dict, rest: dict):
    """Stage arch: the design with only its mechanical parts (the items drawn by fpsynth, and the captured parts that
    are fixed, through-hole or at least ARCH_BIG_MM2 of courtyard); the courtyard area of every other captured part
    goes into the area budget `rest` (per board face, and the pool outside the exclusive regions)."""
    from ...core.design import Design
    from .fpsynth import is_synthetic
    mem = members(fp, design, list(design.components))
    excl = set().union(*[mem[r["name"]] for r in fp["regions"] if r["kind"] in EXCLUSIVE and r["kind"] != "keepout"]
                       or [set()])
    res = resolve(fp, design, list(design.components))
    board_of = {x: it["board"] for it in fp["items"] for x in res[it["name"]]}
    keep, n = {}, 0
    for ref, c in design.components.items():
        if c.get("virtual"):
            continue
        if is_synthetic(design, ref):
            keep[ref] = c
            continue
        pl = c.get("placement") or {}
        try:
            f = libs.footprint(c.get("footprint"))
        except Exception:  # noqa: BLE001 - the parts check reports it; keep it so the floorplan check does too
            keep[ref] = c
            continue
        x0, y0, x1, y1 = f["bbox"]
        big = (x1 - x0) * (y1 - y0) >= ARCH_BIG_MM2
        tht = any(q["kind"] in ("thru_hole", "np_thru_hole") for q in f["pads"])
        if ("x" in pl and "y" in pl and pl.get("fixed", True)) or tht or big:
            keep[ref] = c
            continue
        side = "back" if set(f["sides"]) == {"back"} else str(c.get("side", "front"))
        b = f["sides"].get(side) or f["bbox"]
        k = (board_of.get(ref, fp["boards"][0]["name"]), side)
        a = (b[2] - b[0]) * (b[3] - b[1])
        rest["face"][k] = rest["face"].get(k, 0.0) + a
        if ref not in excl:
            rest["pool"][k] = rest["pool"].get(k, 0.0) + a
        n += 1
    if n:
        rest["items"].append(f"{n} captured parts")
    raw = dict(design.raw)
    raw["blocks"] = {}  # layout blocks move parts that are not drawn here
    d2 = Design(path=design.path, raw=raw)
    d2.components = keep
    d2.budget_widths = dict(design.budget_widths)
    return d2


def _arch_checks(design, parts, fp: dict, mem: dict, rest: dict, sides_out: list, m: float) -> dict:
    """Stage arch: the fixed mechanical parts against each other (courtyards on one face, through-hole pins and pegs
    under a courtyard on either face), their region, keep-out and isolation rules (the placer's fixed-part rules),
    the movable mechanical parts that no region holds against the room the board leaves them, and the room left for
    the rest of the circuit. Returns {checks: [(name, ok, msg, back_to)], room: [...]}."""
    from . import placer
    out = []
    fixed = sorted(x for x, p in parts.items() if p.fixed and p.sides)

    def yard(p, s):
        b = _side_box(p, s, m)
        return b if b and b[2] > b[0] and b[3] > b[1] else None
    for i, a in enumerate(fixed):
        for b in fixed[i + 1:]:
            pa, pb = parts[a], parts[b]
            for s in ("front", "back"):
                ya, yb = yard(pa, s), yard(pb, s)
                if ya and yb and placer._overlap(ya, yb) > 0.01:
                    g = box(*ya).intersection(box(*yb))
                    out.append(("overlap", False, f"{a} and {b} overlap on the {s} face ({g.area:.1f} mm2 at "
                                f"{_span(g)}): move or turn one of them, or put one on the other face", "architecture"))
            for p1, n1, p2, n2 in ((pa, a, pb, b), (pb, b, pa, a)):
                for s in ("front", "back"):
                    y2 = yard(p2, s)
                    if not y2 or s in (p1.sides or {}):  # same face: the courtyard overlap above says it
                        continue
                    hit = [n for n in sorted(p1.tht) if placer._overlap(placer.part_pad_box(p1, n), y2) > 0]
                    pegs = [hb for hb in p1.npth_boxes() if placer._overlap(hb, y2) > 0]
                    if not hit and not pegs:
                        continue
                    g = unary_union([box(*placer.part_pad_box(p1, n)) for n in hit] + [box(*hb) for hb in pegs])
                    what = ", ".join([f"{n1}.{n}" for n in hit] + ([f"{len(pegs)} pegs"] if pegs else []))
                    out.append(("tht-under", False, f"{what} go through the board under {n2} on the {s} face "
                                f"({_span(g)}; {n2} courtyard {_span(box(*y2))}): move or turn {n1} or {n2} so "
                                f"the pins clear the courtyard", "architecture"))
    if fixed:
        W, H = float(design.board["width_mm"]), float(design.board["height_mm"])
        boards = {b["name"]: b["rect"].bounds for b in fp["boards"]}
        rb = {r["name"]: boards[r["board"]] for r in fp["regions"]}
        try:
            pl = placer.Placer(design, parts, W, H, seed=1)
            for msg in pl.fixed_violations(rb):
                ok, why = _fixed_severity(parts, fp, msg, m)
                out.append(("fixed-placement", ok, msg + why, None if ok is None else "architecture"))
        except Exception as e:  # noqa: BLE001 - the geometric checks above stand without the placer's rules
            out.append(("fixed-placement", None, f"placer rules not run: {type(e).__name__}: {e}", None))
    # movable mechanical parts that no region holds: room on the board face, clear of fixed parts and exclusive regions
    inreg = set().union(*mem.values()) if mem else set()
    rules, _ = placer_regions(design, parts)
    for x in sorted(parts):
        p = parts[x]
        if p.fixed or x in inreg or not p.sides or x not in design.components:
            continue
        bname = fp["boards"][0]["name"]
        b = fp["boards"][0]
        for s in p.sides:
            inset = box(b["x"] + fp["margin"], b["y"] + fp["margin"], b["x"] + b["w"] - fp["margin"],
                        b["y"] + b["h"] - fp["margin"])
            bad = [g for g in rules if g["excl"] and s in g["sides"] and x not in g["members"] and x not in g["allow"]]
            poly = inset.difference(unary_union([g["poly"] for g in bad])) if bad else inset
            w, h = p.sides[s][0] - 2 * m, p.sides[s][1] - 2 * m
            r = {"name": f"{bname} {s} face", "poly": poly, "board": bname, "forbid_names": [g["name"] for g in bad]}
            if not _fits(w, h, poly):
                out.append(("fit", False, f"{x} ({w:.1f} x {h:.1f} mm, {s}) fits nowhere on the {s} face clear of the "
                            f"exclusive regions {', '.join(r['forbid_names']) or '(none)'}: a smaller part, another "
                            f"face or a larger board", "architecture"))
                continue
            msg = _fit_with_fixed(design, parts, fp, r, x, s, m)
            if msg:
                out.append(("fit", False, msg, "architecture"))
    room = []
    for row in sides_out:
        k = (row["board"], row["side"])
        need = rest["pool"].get(k, 0.0)
        mech = row["pool_load_mm2"] - need
        left = row["pool_mm2"] - mech
        room.append({"board": row["board"], "side": row["side"], "pool_mm2": row["pool_mm2"],
                     "mechanical_mm2": round(mech, 1), "left_mm2": round(left, 1), "rest_mm2": round(need, 1)})
    if not rest["items"]:
        out.append(("budget", None, "no area estimate for the rest of the circuit: add items with area_mm2 and side "
                    "(the blocks not drawn yet) so the fill check sees them; room left outside the exclusive regions: "
                    + "; ".join(f"{x['board']} {x['side']} {x['left_mm2']:.0f} mm2" for x in room), None))
    else:
        for x in room:
            if x["rest_mm2"] or x["side"] == "front":
                f = x["rest_mm2"] / x["left_mm2"] if x["left_mm2"] > 0 else math.inf
                out.append(("budget", True, f"{x['board']} {x['side']} face: the mechanical parts leave "
                            f"{x['left_mm2']:.0f} of {x['pool_mm2']:.0f} mm2 outside the exclusive regions; the rest "
                            f"of the circuit needs {x['rest_mm2']:.0f} mm2 ({f:.0%} of it; the area check above "
                            f"holds the limit)", None))
    return {"checks": out, "room": room, "fixed": fixed,
            "movable": sorted(x for x, p in parts.items() if not p.fixed and x in design.components)}


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
    # what goes through the board from a fixed part on either face (through-hole pins, pegs): no courtyard over it
    from .placer import part_pad_box
    holes, hole_names = {}, {}
    for q, o in parts.items():
        if q == x or not o.fixed or q in yards or not (o.tht or o.npth):
            continue
        hs = [(f"{q}.{n}", box(*part_pad_box(o, n))) for n in sorted(o.tht)] + [(f"{q} peg", box(*b)) for b in o.npth_boxes()]
        hs = [(n, g) for n, g in hs if g.intersects(r["poly"])]
        if hs:
            holes[q] = unary_union([g for _, g in hs])
            hole_names[q] = [n for n, _ in hs]
    c = design.components.get(x)
    pad_rules, mains = [], []
    for g in fp["regions"]:
        if g["kind"] != "mains" or not g["iso"] or not g["nets"] or g["board"] != r["board"] or g["poly"].is_empty:
            continue
        for num, net in (c.pins.items() if c else []):
            if num in p.pads and num not in p.nocu and any(fnmatch.fnmatchcase(net, pat) for pat in g["nets"]):
                pad_rules.append((num, g["poly"]))
                mains.append((net, g))
    # its own through-hole pins and pegs must clear the courtyards of the fixed parts on the other face
    other = "back" if side == "front" else "front"
    under = {q: box(*_side_box(o, other, m)) for q, o in parts.items() if q != x and o.fixed and o.side_box(other)
             and other not in (p.sides or {})}
    under = {q: g for q, g in under.items() if g.intersects(r["poly"])}
    if p.tht and under:
        ok_pins = bd["rect"].buffer(60, join_style="mitre").difference(unary_union(list(under.values())))
        pad_rules = pad_rules + [(n, ok_pins) for n in sorted(p.tht)]
    if not yards and not pad_rules and not holes:
        return None
    allowed = r["poly"]
    if p.fixed or p.edge:  # off the board: a body may overhang the edge
        allowed = allowed.union(bd["rect"].buffer(60, join_style="mitre").difference(bd["rect"]))
    kos = [k for k in fp["regions"] if k["kind"] == "keepout" and k["board"] == r["board"] and side in k["sides"]]
    areas = [a for a in getattr(parts, "areas", []) or [] if "footprints" in a["forbids"] and a["owner"] != x
             and ({"front": "F.Cu", "back": "B.Cu"}[side] in a["layers"] or "*.Cu" in a["layers"])]
    free = allowed.difference(unary_union([k["poly"] for k in kos] + [a["poly"] for a in areas] + list(yards.values())
                                          + list(holes.values()) + list(r.get("forbid") or [])))
    if fit_anywhere(p, side, free, pad_rules, m):
        return None
    w, h = p.sides[side][0] - 2 * m, p.sides[side][1] - 2 * m
    why = []
    if yards:
        why.append("the fixed parts " + ", ".join(f"{q} ({_span(g)})" for q, g in sorted(yards.items())))
    if holes:
        why.append("the through-hole pins of fixed parts on the other face, " + ", ".join(
            f"{' '.join(hole_names[q])} ({_span(g)})" for q, g in sorted(holes.items())))
    if r.get("forbid"):
        why.append("the exclusive regions " + ", ".join(r.get("forbid_names") or []))
    near = [k["name"] for k in kos if k["poly"].distance(r["poly"]) < 1e-6]
    if near:
        why.append("keep-outs " + ", ".join(near))
    if p.tht and under:
        why.append(f"its own through-hole pins off the {other}-face courtyards of " + ", ".join(
            f"{q} ({_span(g)})" for q, g in sorted(under.items())))
    msg = f"{x} ({w:.1f} x {h:.1f} mm, {side}) fits nowhere in region {r['name']}: clear of " + " and ".join(why)
    if mains:
        nets = sorted({n for n, _ in mains})
        cond = sorted({k["name"] for _, g in mains for k in fp["regions"] if k["kind"] == "keepout" and k["conductive"]
                       and k["board"] == g["board"] and k["poly"].distance(g["poly"]) <= g["iso"] + 1e-3})
        g = mains[0][1]
        msg += (f", with its {', '.join(nets)} pads inside region {g['name']}"
                + (f" ({g['iso']:g} mm from conductive {', '.join(cond)})" if cond else ""))
    return msg + " (back to architecture: board size, part or region change)"


# ---------------------------------------------------------------- connector edges, envelopes, isolation groups


def _edge_line(rect, e: str) -> LineString:
    x0, y0, x1, y1 = rect.bounds
    return LineString({"left": [(x0, y0), (x0, y1)], "right": [(x1, y0), (x1, y1)], "top": [(x0, y0), (x1, y0)],
                       "bottom": [(x0, y1), (x1, y1)]}[e])


def _length(g) -> list[float]:
    return [x.length for x in getattr(g, "geoms", [g]) if not x.is_empty and x.length > 1e-9]


def _edge_checks(d2, parts, fp, res, m) -> list[tuple]:
    """Connector edge feasibility of the movable parts with an `edge` (the part's placement.edge or its item's): the
    stretch of that board edge a part may use (inside its own region, outside the cuts with their board margin and the
    exclusive regions it may not enter) must hold the part's shorter courtyard side, and the edge, cuts taken out,
    must hold the shorter sides of every part sent to it. Shorter side: a proof, whatever the rotation."""
    from ...core.floorplan import placer_regions
    rules, _ = placer_regions(d2, parts)
    rect = fp["boards"][0]["rect"]
    cut_names = {r["name"] for r in fp["regions"] if r["cut"]}
    item_edge = {x: it["edge"] for it in fp["items"] if it["edge"] for x in res[it["name"]]}
    out, by_edge = [], {}
    for x, p in parts.items():
        e = getattr(p, "edge", None) or item_edge.get(x)
        if e not in EDGES or p.fixed or not p.sides:
            continue
        for s, v in p.sides.items():
            w, h = v[0] - 2 * m, v[1] - 2 * m
            need = min(w, h)
            line, common = _edge_line(rect, e), _edge_line(rect, e)
            for g in rules:
                if s not in g["sides"] or g["name"].startswith("board "):
                    continue
                if g["name"] in cut_names:
                    common = common.difference(g["poly"])
                if x in g["members"]:
                    line = line.intersection(g["poly"].buffer(0.01))
                elif g["excl"] and x not in g["allow"]:
                    line = line.difference(g["poly"])
            longest = max(_length(line), default=0.0)
            if longest < need - 1e-6:
                out.append(("edge", False, f"{x} ({w:.1f} x {h:.1f} mm, {s}) needs at least {need:.1f} mm of the {e} "
                            f"edge; the longest stretch inside its region and clear of the cuts and of the regions it "
                            f"may not enter is {longest:.1f} mm (another edge, a smaller connector, or a larger board)",
                            "architecture"))
            by_edge.setdefault((e, s), [sum(_length(common)), []])[1].append((x, need))
    for (e, s), (free, rows) in sorted(by_edge.items()):
        tot = sum(n for _, n in rows)
        if len(rows) > 1 and tot > free + 1e-6:
            out.append(("edge", False, f"{s} {e} edge: {', '.join(x for x, _ in rows)} need at least {tot:.1f} mm "
                        f"side by side, the edge has {free:.1f} mm clear of the cuts (move some to another edge, or a "
                        f"larger board)", "architecture"))
    return out


def _isolation_checks(d2, parts, fp, area) -> list[tuple]:
    """Each isolation group of the design (board.isolation, drc_rules isolation patterns): a group of two or more
    nets without between_mm (the functional spacing between its own nets, across a fuse or an open contact), member
    nets free to use the inner layers of a board with more than 2 layers (they can pass under the other zone), and a
    group that no floorplan region confines (with the parts that carry its nets, an area estimate of its isolation
    bubble: the members' courtyards at the fill limit with the isolation band on their open sides, in a corner or as a
    strip along one edge, plus the other parts at the fill limit, against the usable face)."""
    from ..board import pcb
    try:
        groups = pcb.isolation_needs(d2)
    except Exception:  # noqa: BLE001 - a malformed rule is the design check's finding
        return []
    nets_raw = d2.raw.get("nets") or {}
    layers = int(d2.board.get("layers", 2))
    b = fp["boards"][0]
    mg, fill = fp["margin"], fp["fill_max"]
    out = []
    for g in groups:
        mem = sorted(g["members"])
        names = ", ".join(mem[:6]) + (", ..." if len(mem) > 6 else "")
        if len(mem) > 1 and not g.get("between"):
            out.append(("isolation", None, f"isolation group {g['name']} ({names}): no between_mm, so its own nets keep "
                        f"only the class clearance from each other; state the functional spacing (across a fuse or an "
                        f"open contact) from the safety standard, or why none is needed", None))
        if layers > 2:
            free = [n for n in mem if not (nets_raw.get(n) or {}).get("layers")]
            if free:
                out.append(("isolation", None, f"isolation group {g['name']} on {layers} layers: {', '.join(free[:6])} "
                            f"may route on the inner layers, under the other zone; give each member net `layers:` "
                            f"(nets.<net>.layers) or a rule area that keeps them out", None))
        wide = [n for n in mem if (nets_raw.get(n) or {}).get("current_a") and not (nets_raw.get(n) or {}).get("min_width_mm")]
        if wide:
            out.append(("isolation", None, f"isolation group {g['name']}: {', '.join(wide[:6])} carry current but have no "
                        f"min_width_mm, so the router may neck them down at a fine pad; set the narrowest track the "
                        f"protection allows (a neck must not melt before the fuse opens: its I2t against the fuse's)",
                        None))
        regs = [r for r in fp["regions"] if r["nets"] and r["kind"] != "keepout"
                and all(any(fnmatch.fnmatchcase(n, p) for p in r["nets"]) for n in mem)]
        tht = sorted({x for x in parts if x in d2.components and getattr(parts[x], "tht", None)
                      and any(net in mem for pin, net in d2.components[x].pins.items() if pin in parts[x].tht)})
        if tht and (not regs or all(len(r["sides"]) == 1 for r in regs)):
            out.append(("isolation", None, f"isolation group {g['name']}: the through-hole pads of {', '.join(tht[:6])} "
                        f"on its nets come through to both faces, but no region of the group covers both: the "
                        f"isolation (and the accessible face) must hold on the other face too (region side: both, "
                        f"or an insulating barrier item)", None))
        if regs:
            continue
        out.append(("isolation", None, f"isolation group {g['name']} ({names}) has no floorplan region with its nets: "
                    f"the placer does not confine it; declare a region (kind mains for a hazardous voltage) with "
                    f"`nets:` and `isolation_mm`", None))
        refs = [x for x in parts if x in d2.components and set(d2.components[x].pins.values()) & set(mem)]
        Wu, Hu = b["w"] - 2 * mg, b["h"] - 2 * mg
        iso = float(g["mm"])
        for s in ("front", "back"):
            A = sum(area[x].get(s, 0.0) for x in refs if x in area) / fill
            if A <= 0:
                continue
            rest = sum(v.get(s, 0.0) for x, v in area.items() if x not in refs) / fill
            opts = [(math.sqrt(A) + iso) ** 2] if math.sqrt(A) + iso <= min(Wu, Hu) else []
            opts += [(A / L + iso) * L for L, D in ((Wu, Hu), (Hu, Wu)) if A / L + iso <= D]
            need = min(opts, default=math.inf)
            if need + rest > Wu * Hu + 1e-6:
                out.append(("isolation", False, f"isolation bubble of {g['name']} on the {s} face: its {len(refs)} parts "
                            f"take at least {need:.0f} mm2 with the {iso:g} mm band, the other parts {rest:.0f} mm2 (fill "
                            f"limit {fill:.0%}): {need + rest:.0f} of {Wu * Hu:.0f} mm2 (a larger board, parts on the "
                            f"other face, or another construction)", "architecture"))
    return out


def _envelope_checks(parts, fp, res, m, placed: bool) -> list[tuple]:
    """Items with `envelope_mm`: the mating part, cable and its bend need that much room in front of the part's edge
    side (its `edge`, else the nearest board edge), across the part's width. It must stay within floorplan.wall_mm of
    that edge (the enclosure wall), off every keep-out and cut, and off the courtyards of other parts on its faces.
    A fixed part against a cut or a fixed part fails; against a quick-placed part it is a risk."""
    out = []
    rect = fp["boards"][0]["rect"]
    X0, Y0, X1, Y1 = rect.bounds
    kos = [r for r in fp["regions"] if r["kind"] == "keepout" and r["poly"] is not None and not r["poly"].is_empty]
    for it in fp["items"]:
        env = it["envelope"]
        if env is None:
            continue
        names = [x for x in res[it["name"]] if x in parts]
        for x in names:
            p = parts[x]
            boxes = {s: _side_box(p, s, m) for s in (p.sides or {})}
            boxes = {s: bb for s, bb in boxes.items() if bb and bb[2] > bb[0] and bb[3] > bb[1]}
            if not boxes or not (p.fixed or placed):
                continue
            bx = (min(v[0] for v in boxes.values()), min(v[1] for v in boxes.values()),
                  max(v[2] for v in boxes.values()), max(v[3] for v in boxes.values()))
            gaps = {"left": bx[0] - X0, "right": X1 - bx[2], "top": bx[1] - Y0, "bottom": Y1 - bx[3]}
            e = it["edge"] or getattr(p, "edge", None) or min(gaps, key=gaps.get)
            strip = {"left": box(bx[0] - env, bx[1], bx[0], bx[3]), "right": box(bx[2], bx[1], bx[2] + env, bx[3]),
                     "top": box(bx[0], bx[1] - env, bx[2], bx[1]), "bottom": box(bx[0], bx[3], bx[2], bx[3] + env)}[e]
            reach = env - gaps[e]  # past the board edge
            wall = fp["wall"].get(e)
            where = f"{x} ({'fixed' if p.fixed else 'quick placement'}): its mating part and cable need {env:g} mm in front of its {e} side"
            if wall is not None and reach > wall + 1e-6:
                out.append(("envelope", False, f"{where}, {reach:.1f} mm past the {e} board edge; the enclosure wall is "
                            f"{wall:g} mm away (floorplan.wall_mm): a connector with another entry direction or a "
                            f"shorter mating part, or another position", "parts"))
            for r in kos:
                a = strip.intersection(r["poly"]).area
                if a > 0.01:
                    out.append(("envelope", False if p.fixed else None, f"{where}, which crosses {'cut' if r['cut'] else 'keep-out'} "
                                f"{r['name']} ({a:.1f} mm2): move the part along the edge, or another connector",
                                "architecture" if p.fixed else None))
            for q, o in parts.items():
                if q == x or q in names or not o.sides or not (o.fixed or placed):
                    continue
                a = max((strip.intersection(box(*ob)).area for s in boxes if s in o.sides
                         for ob in [_side_box(o, s, m)] if ob and ob[2] > ob[0] and ob[3] > ob[1]), default=0.0)
                if a > 0.01:
                    hard = p.fixed and o.fixed
                    out.append(("envelope", False if hard else None, f"{where}, which overlaps {q} ({a:.1f} mm2)"
                                + ("" if hard else ": the stage-7 placer does not know the envelope; fix the "
                                   "connector or keep that side clear"), "architecture" if hard else None))
    return out


_ISO_SHORT = re.compile(r"isolation \S+: ([\d.]+) mm short .*?\+ ([\d.]+) mm (?:placer )?margin")
_IN_REGION = re.compile(r"(\S+) inside floorplan region '([^']+)'")


def _fixed_severity(parts, fp: dict, msg: str, m: float) -> tuple[bool | None, str]:
    """A fixed-rule finding of the quick placement as (False, "") for a failure, or (None, why) for a risk: the
    placer adds a margin to its rules (an isolation shortfall no larger than the placer's own margin leaves the rule
    itself met on the pad bounding boxes) and keeps parts board.margin_mm from a cut as from the board edge (a fixed
    courtyard that enters only that band, not the cut, does not collide with what fills the cut)."""
    from .placer import FIXED_LEG_RISK
    if msg.startswith("budget ") and FIXED_LEG_RISK in msg:
        return None, ""
    k = _ISO_SHORT.search(msg)
    if k and float(k.group(1)) <= float(k.group(2)) + 1e-6:
        return None, (f" (the rule itself holds: the shortfall is within the placer's {float(k.group(2)):g} mm "
                      "margin; check the real pad gap in post-layout)")
    k = _IN_REGION.match(msg)
    if k and k.group(1) in parts:
        cut = next((r for r in fp["regions"] if r["name"] == k.group(2) and r["cut"]), None)
        if cut is not None:
            p = parts[k.group(1)]
            hit = 0.0
            for s in p.sides or {}:
                b = _side_box(p, s, m)
                if b and b[2] > b[0] and b[3] > b[1]:
                    hit = max(hit, box(*b).intersection(cut["poly"]).area)
            if hit < 0.01:
                return None, (f" (the courtyard stays off the cut itself, inside its {fp['margin']:g} mm board "
                              "margin: check the mating part and cable envelope against the cut)")
    return False, ""


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


_PAIR = re.compile(r"courtyard overlap (\S+)/(\S+)$")


def illegal_parts(parts, viol: list[str]) -> list[dict]:
    """The violations of an illegal placement by part: [{ref, fixed, overlaps: [refs], other: [violation texts]}],
    the parts with the most violations first. A courtyard overlap names both parts; any other violation (keep-out,
    region, edge, isolation) is listed under each part it names."""
    rows: dict[str, dict] = {}

    def row(r):
        return rows.setdefault(r, {"ref": r, "fixed": bool(getattr(parts.get(r), "fixed", False)), "overlaps": [],
                                   "other": []})
    for v in viol:
        m = _PAIR.match(v)
        if m:
            a, b = m.groups()
            row(a)["overlaps"].append(b)
            row(b)["overlaps"].append(a)
            continue
        refs = [r for r in dict.fromkeys(re.split(r"[\s/.,()']+", v)) if r in parts]
        for r in refs[:2]:  # without the part's own name in front ("R9 courtyard ..." under R9)
            row(r)["other"].append(v[len(r) + 1:] if v.startswith(r + " ") else v)
    return sorted(rows.values(), key=lambda x: (-(len(x["overlaps"]) + len(x["other"])), x["ref"]))


def illegal_text(rows: list[dict], limit: int = 30) -> str:
    """One line per part of `illegal_parts`: `BT2 overlaps M3 (fixed), C1`, `K1: <violation>`."""
    fixed = {x["ref"] for x in rows if x["fixed"]}
    out = []
    for x in rows[:limit]:
        name = x["ref"] + (" (fixed)" if x["fixed"] else "")
        bits = []
        if x["overlaps"]:
            bits.append("overlaps " + ", ".join(o + (" (fixed)" if o in fixed else "") for o in sorted(set(x["overlaps"]))))
        bits += x["other"]
        out.append(f"{name} " + "; ".join(bits))
    if len(rows) > limit:
        out.append(f"and {len(rows) - limit} more parts")
    return " | ".join(out)


SETTLE_POSITIONS = 300  # _settle: grid points per rotation over a region's bounding box


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
        # at most SETTLE_POSITIONS grid points per rotation: a whole stacked board as the region (94 x 57 mm) took
        # two minutes at 2 mm; small regions keep the 2 mm grid
        st = max(step, math.sqrt(max(x1 - x0, 0.0) * max(y1 - y0, 0.0) / SETTLE_POSITIONS))
        best = (pl._total(), p.x, p.y, p.r)
        for a in (0.0, 90.0, 180.0, 270.0):
            for i in range(int((x1 - x0) / st) + 1):
                for j in range(int((y1 - y0) / st) + 1):
                    p.x, p.y, p.r = x0 + i * st, y0 + j * st, a
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
    arch = res.get("stage") == "arch"
    L = [f"# {design.name}: floorplan check" + (" (stage arch: mechanical parts)" if arch else ""), "",
         f"Verdict: {res.get('verdict', 'FAIL')} ({res['seconds']} s)", ""]
    if "depth" not in res:
        L += [f"- {c['msg']}" for c in res["checks"]]
        return "\n".join(L) + "\n"
    bs = ", ".join(f"{b['name']} {b['w']:g} x {b['h']:g} mm" for b in res["boards"])
    L += [f"Construction: {res['construction']}; {bs}; {res['layers']} layers; fill limit {res['fill_max']:.0%}; "
          f"{res['parts']} parts ({res['real_parts']} real, {res.get('drawn_parts', 0)} drawn from item footprints or "
          f"pin rows, {res['parts'] - res['real_parts'] - res.get('drawn_parts', 0)} box placeholders)"
          + (f"; area budget only: {', '.join(res['rest_items'])}" if res.get("rest_items") else "") + ".", ""]
    bad = [c for c in res["checks"] if c["ok"] is False]
    risk = [c for c in res["checks"] if c["ok"] is None]
    if bad:
        back = sorted({c["back_to"] for c in bad if c.get("back_to")})
        L += ["## Failures", ""] + (["Back to: " + ", ".join(back) + " (before parts are committed or the circuit "
                                    "capture ends: these do not get better in layout).", ""] if back else []) \
            + [f"- {c['name']}: {c['msg']}" + (f" [back to {c['back_to']}]" if c.get("back_to") else "")
               for c in bad] + [""]
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
    a = res.get("arch")
    if a:
        L += ["", "## Mechanical parts", "", f"Fixed: {', '.join(a['fixed']) or 'none'}. Movable: "
              f"{', '.join(a['movable']) or 'none'}.", "", "| face | outside exclusive regions mm2 | mechanical mm2 | "
              "left mm2 | rest of the circuit mm2 |", "|---|---|---|---|---|"]
        for x in a["room"]:
            L.append(f"| {x['board']} {x['side']} | {x['pool_mm2']:.0f} | {x['mechanical_mm2']:.0f} | "
                     f"{x['left_mm2']:.0f} | {x['rest_mm2']:.0f} |")
    p = res.get("placement")
    if p:
        c = p["congestion"]
        head = (f"Seed {p['seed']}, {p['iters']} moves, {len(p['violations'])} placement violations, "
                f"{len(p.get('fixed_violations', []))} fixed-rule violations.")
        if p["violations"]:  # no congestion or region-crossing figures from an illegal placement
            L += ["", "## Quick placement", "", head + " The placement is illegal: no congestion or routability "
                  "estimate.", "", "| part | overlaps | other violations |", "|---|---|---|"]
            fixed = {x["ref"] for x in p.get("illegal", []) if x["fixed"]}
            for x in p.get("illegal", []):
                L.append(f"| {x['ref']}{' (fixed)' if x['fixed'] else ''} | "
                         f"{', '.join(o + (' (fixed)' if o in fixed else '') for o in sorted(set(x['overlaps'])))} | "
                         f"{'; '.join(x['other'])} |")
        else:
            L += ["", "## Quick placement", "",
                  head + f" Congestion (RUDY, {p['cell']:g} mm cells): wire {c['demand_mm']:.0f} mm on "
                  f"{c['capacity_mm']:.0f} mm capacity ({c['util']:.0%}), overflow {c['overflow']:.1%}, peak "
                  f"{c['peak']:.1f} x, {len(c['hot'])} cells over.",
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
        if p and not p["violations"]:
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


def run(design, libs, out, iters: int = 20000, seeds: int = 2, extra=None, stage: str = "full") -> tuple[str, dict]:
    """`hwflow floorplan`: the check, build/FLOORPLAN.md and build/floorplan.svg. Returns (report text, status data)."""
    res = check(design, libs, iters=iters, seeds=seeds, extra=extra, stage=stage)
    text = report(design, res)
    sfx = "" if stage == "full" else f"-{stage}"
    (out / f"FLOORPLAN{sfx}.md").write_text(text)
    if "_fp" in res:
        (out / f"floorplan{sfx}.svg").write_text(svg(res))
    return text, {k: v for k, v in res.items() if not k.startswith("_")}
