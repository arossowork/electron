"""Cut a placed sub-circuit out of a design: a small standalone project for the layout regression corpus.

`cutout(project, out, parts=..., region=..., nets=...)` keeps the chosen parts at their exact placement (from the
placement file, default build/placement.json, else their design placement), with their nets, net classes, isolation
groups, floorplan regions clipped to the window, keep-out footprints that reach into it, rules and blocks. A net that
also has pads outside the kept parts ends on a fixed test-point pad at the window border, where the straight line
from its nearest kept pad to its nearest dropped pad crosses the border (the router meets the rest of the board
there). The board outline shrinks to the window. Every part is written `fixed: true`; `cutout.yaml` records the
source, the window, the offset, the terminals, and which parts the source placer was free to move (`free`), so a
test can re-place them from scratch.

Nothing here is specific to a design: the selection, the window and the terminals come from the arguments, the
placement and the board rules."""

from __future__ import annotations

import copy
import fnmatch
import json
import math
import shutil
from pathlib import Path

import yaml
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from ...core import blocks as core_blocks
from ...core import floorplan as fplan
from ...core.design import Design
from ...core.geometry import COURTYARD_MARGIN, Part, rot
from ...core.libs import Libs
from ..board import pcb
from . import placer

TP_SYMBOL = "Connector:TestPoint"
TP_SIZES = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)  # TestPoint:TestPoint_Pad_D<d>mm (KiCad library)
TP_PREFIX = "TPB"
STEP_MM = 0.25  # candidate step along the border
FRAME_MM = 3.0  # border band around the kept parts that holds the terminals (on sides inside the source board)


def _globs(v) -> list[str]:
    if v is None:
        return []
    return [s for x in ([v] if isinstance(v, str) else v) for s in str(x).split(",") if s.strip()]


def _match(name: str, pats) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in pats)


def load_placement(design: Design, libs: Libs, placement: Path | None) -> dict[str, Part]:
    """Parts at the placement of `placement` (placement.json: ref -> [x, y, rot]); parts it lacks keep the design's."""
    parts = placer.build_parts(design, libs)
    pj = Path(placement) if placement else design.dir / "build" / "placement.json"
    saved = json.loads(pj.read_text()) if pj.exists() else {}
    for r, p in parts.items():
        if r in saved and isinstance(saved[r], list):
            p.x, p.y, p.r = map(float, saved[r][:3])
    for u in getattr(parts, "blocks", ()) or ():
        if u.ref in saved:
            u.x, u.y, u.r = map(float, saved[u.ref][:3])
            u.sync(parts)
        else:
            u.pull(parts)
    return parts


def _copper(part: Part, n) -> bool:
    return n not in part.nocu


def _pad_box(part: Part, n):
    x, y = part.pad(n)
    w, h = (part.pad_size or {}).get(n, (0.0, 0.0))
    if part.r % 180:
        w, h = h, w
    return x - w / 2, y - h / 2, x + w / 2, y + h / 2


def _front_cu(part: Part, n) -> bool:
    cu = (part.cu or {}).get(n)
    return cu is None or "*" in cu or "F.Cu" in cu


def _clip_region(r: dict, win, dx: float, dy: float) -> dict | None:
    """A floorplan region clipped to the window and moved by (dx, dy); None when it misses the window."""
    r = dict(r)
    if r.get("circle"):
        cx, cy, rad = map(float, r["circle"])
        if Point(cx, cy).buffer(rad).intersection(win).area <= 1e-6:
            return None
        r["circle"] = [round(cx + dx, 4), round(cy + dy, 4), rad]
        return r
    if r.get("rect"):
        g = box(*map(float, r.pop("rect")))
    elif r.get("poly"):
        g = Polygon([tuple(map(float, p)) for p in r.pop("poly")])
    else:
        return r
    g = g.intersection(win)
    if g.is_empty or g.area <= 1e-6:
        return None
    if g.geom_type != "Polygon":
        g = max(getattr(g, "geoms", [g]), key=lambda q: q.area)
    if abs(g.area - box(*g.bounds).area) < 1e-6:
        x0, y0, x1, y1 = g.bounds
        r["rect"] = [round(x0 + dx, 4), round(y0 + dy, 4), round(x1 + dx, 4), round(y1 + dy, 4)]
    else:
        r["poly"] = [[round(x + dx, 4), round(y + dy, 4)] for x, y in list(g.exterior.coords)[:-1]]
    return r


def _iso_req(groups: list[dict], clearance: float, a: str | None, b: str | None) -> float:
    """Copper distance two nets keep: board clearance, an isolation group's distance between a member and another
    net (+0.5 mm, the placer's margin), between_mm between two members (+0.1 mm)."""
    req = clearance
    if a == b and a is not None:
        return 0.0
    for g in groups:
        ma, mb = a in g["nets"], b in g["nets"]
        if ma != mb:
            req = max(req, float(g.get("clearance_mm") or 0) + 0.5)
        elif ma and mb and g.get("between_mm"):
            req = max(req, float(g["between_mm"]) + 0.1)
    return req


def _tp_footprint(width: float) -> tuple[str, float]:
    for d in TP_SIZES:
        if d >= width + 0.2 - 1e-9:
            return f"TestPoint:TestPoint_Pad_D{d:.1f}mm", d
    d = TP_SIZES[-1]
    return f"TestPoint:TestPoint_Pad_D{d:.1f}mm", d


def cutout(project, out, *, parts=None, region=None, nets=None, placement=None, frame: float = FRAME_MM,
           name: str | None = None, source: str | None = None, grow: int = 3) -> dict:
    """Write the cutout project to `out` and return its record (also written to out/cutout.yaml). When a terminal
    finds no border position that meets every rule, the frame grows by 2 mm and the cut runs again (`grow` times)."""
    for k in range(grow + 1):
        rec = _cutout(project, out, parts=parts, region=region, nets=nets, placement=placement, frame=frame,
                      extra=2.0 * k, name=name, source=source)
        if not any("no border position" in w for w in rec["warnings"]):
            break
    return rec


def _cutout(project, out, *, parts, region, nets, placement, frame, extra, name, source) -> dict:
    d = Design.load(project)
    libs = Libs(design=d)
    P = load_placement(d, libs, placement)
    W, H = float(d.board["width_mm"]), float(d.board["height_mm"])
    fp_raw = d.raw.get("floorplan") or {}
    if fp_raw.get("construction") == "stacked":
        raise ValueError("cutout: stacked floorplans are not supported")
    regions_raw = fp_raw.get("regions") or []

    # ---- selection and window
    sel: set[str] = set()
    pats, npats = _globs(parts), _globs(nets)
    if pats:
        sel |= {r for r in P if _match(r, pats)}
    base = None
    if region is not None:
        if isinstance(region, str) and not region.replace(",", "").replace(".", "").replace("-", "").strip().isdigit() \
                and any(isinstance(g, dict) and g.get("name") == region for g in regions_raw):
            g = next(g for g in regions_raw if g.get("name") == region)
            if g.get("rect"):
                base = box(*map(float, g["rect"]))
            elif g.get("poly"):
                base = Polygon([tuple(map(float, p)) for p in g["poly"]])
            else:
                cx, cy, rad = map(float, g["circle"])
                base = Point(cx, cy).buffer(rad)
        else:
            vals = [float(v) for v in (region.split(",") if isinstance(region, str) else region)]
            base = box(*vals)
        for r, p in P.items():
            x0, y0, x1, y1 = p.box()
            if base.contains(Point((x0 + x1) / 2, (y0 + y1) / 2)) and any(_copper(p, n) for n in p.pads):
                sel.add(r)
    if npats:
        for n, conns in d.nets().items():
            if _match(n, npats):
                sel |= {r for r, _ in conns if r in P}
    # a layout block stays whole: one member selected selects the block
    for u in getattr(P, "blocks", ()) or ():
        mem = [m[0] for m in u.members]
        if sel & set(mem):
            sel |= set(mem)
    sel = {r for r in sel if r in P and not d.components[r].get("virtual")}
    if not sel:
        raise ValueError("cutout: the selection holds no part")
    boxes = [P[r].box() for r in sel]
    bx0, by0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    bx1, by1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    if base is not None:
        b0 = base.bounds
        bx0, by0, bx1, by1 = min(bx0, b0[0]), min(by0, b0[1]), max(bx1, b0[2]), max(by1, b0[3])
    wx0, wy0 = max(0.0, bx0 - frame), max(0.0, by0 - frame)
    wx1, wy1 = min(W, bx1 + frame), min(H, by1 + frame)
    # snap to 0.05 mm so the outline and the offset print short
    wx0, wy0 = math.floor(wx0 * 20) / 20, math.floor(wy0 * 20) / 20
    wx1, wy1 = math.ceil(wx1 * 20) / 20, math.ceil(wy1 * 20) / 20
    win = box(wx0, wy0, wx1, wy1)
    # parts fully inside the window are obstacles of the source board: they stay; keep-out markers that reach in stay
    dropped = []
    BIG = 1e4
    wide = box(-BIG if wx0 <= 1e-6 else wx0, -BIG if wy0 <= 1e-6 else wy0,
               BIG if wx1 >= W - 1e-6 else wx1, BIG if wy1 >= H - 1e-6 else wy1)
    for r, p in P.items():
        if r in sel or d.components[r].get("virtual"):
            continue
        has_cu = any(_copper(p, n) for n in p.pads)
        if not has_cu:  # a keep-out marker (rule areas) or a bare hole: kept when it reaches into the window
            own = [a for a in (P.areas or []) if a.get("owner") == r]
            if any(win.intersects(a["poly"]) for a in own) or (not own and (p.fixed or win.intersects(box(*p.box())))):
                sel.add(r)
            continue
        b = box(*p.box())
        if wide.contains(b):  # inside, or overhanging only where the window side is the source board edge
            sel.add(r)
        elif win.intersects(b) and b.intersection(win).area > 0.01:
            dropped.append(r)
    for u in getattr(P, "blocks", ()) or ():  # a block with a member outside: the whole block leaves
        mem = [m[0] for m in u.members]
        if sel & set(mem) and not set(mem) <= sel:
            dropped += sorted(sel & set(mem))
            sel -= set(mem)
    if extra:  # more border for the terminals; the parts in it stay out
        wx0, wy0 = max(0.0, wx0 - extra), max(0.0, wy0 - extra)
        wx1, wy1 = min(W, wx1 + extra), min(H, wy1 + extra)
        win = box(wx0, wy0, wx1, wy1)
    # a part's board cutout (an Edge.Cuts slot) that the window cuts through: the part leaves, its shape clipped to
    # the window becomes a cut region of the new board (a notch where the slot crossed the window border)
    slot_cuts = []
    for r in sorted(sel):
        for a in (P.areas or []):
            if a.get("owner") == r and str(a.get("name", "")).endswith(" cutout") and not win.contains(a["poly"]):
                g = a["poly"].intersection(win)
                if not g.is_empty and g.area > 1e-6:
                    slot_cuts.append((r, g))
                sel.discard(r)
                dropped.append(r)
                break
    dx, dy = -wx0, -wy0
    Wc, Hc = round(wx1 - wx0, 4), round(wy1 - wy0, 4)

    # ---- the new design
    raw = copy.deepcopy({k: v for k, v in d.raw.items() if k not in ("sim", "optimize", "components", "blocks",
                                                                      "budgets", "floorplan", "nets", "libraries")})
    raw["project"] = f"{d.name}_cut_{name}" if name else f"{d.name}_cut"
    raw["description"] = f"layout cutout of {d.name}" + (f" ({name})" if name else "")
    board = raw.setdefault("board", {})
    board["width_mm"], board["height_mm"] = Wc, Hc
    board.pop("optimize_area", None)
    comps: dict = {}
    free = []
    blk_members = set()
    keep_blocks = {}
    for bnd in core_blocks.bindings(d):
        refs = set(bnd["parts"].values())
        if refs and refs <= sel:
            e = copy.deepcopy(d.raw["blocks"][bnd["name"]])
            unit = next((u for u in getattr(P, "blocks", ()) or () if u.name == bnd["name"]), None)
            at = dict(e.get("at") or {})
            if unit is not None:
                at.update(x=round(unit.x + dx, 4), y=round(unit.y + dy, 4), rot=unit.r % 360)
            if not (e.get("at") or {}).get("fixed", bool(e.get("at"))):
                free.append(f"block:{bnd['name']}")
            at["fixed"] = True
            e["at"] = at
            keep_blocks[bnd["name"]] = e
            blk_members |= refs
    for r in sorted(sel, key=lambda s: (fnmatch.fnmatchcase(s, "*[0-9]"), s)):
        c = d.components[r]
        data = copy.deepcopy(c.data)
        if data.pop("from_block", None):
            continue  # instantiate-mode block parts come back from the block entry
        data.pop("spice", None)
        if data.get("decouples") and data["decouples"] not in sel:
            data.pop("decouples")
        src_pl = data.get("placement") or {}
        p = P[r]
        if r in blk_members:
            data.pop("placement", None)
        else:
            if not ("x" in src_pl and "y" in src_pl and src_pl.get("fixed", True)):
                free.append(r)
            pl = {"x": round(p.x + dx, 4), "y": round(p.y + dy, 4), "rot": p.r % 360, "fixed": True}
            data["placement"] = pl
        comps[r] = data
    kept_nets = {n for r in comps for n in (comps[r].get("pins") or {}).values()}
    for bn, e in keep_blocks.items():
        kept_nets |= {str(v) for v in (e.get("nets") or {}).values()}

    # ---- terminals
    classes = pcb.net_classes(d)
    iso = [{"nets": set(g["members"]), "clearance_mm": g["clearance_mm"], "between_mm": g["between_mm"]}
           for g in pcb.isolation_groups(d)]
    clearance = float(d.board.get("clearance_mm", 0.2))
    margin = float(d.board.get("margin_mm", 1.0))
    ground = d.ground_nets() if d.board.get("gnd_pour", True) else set()
    on_edge = {"left": wx0 <= 1e-6, "top": wy0 <= 1e-6, "right": wx1 >= W - 1e-6, "bottom": wy1 >= H - 1e-6}
    terms = []
    for n, conns in d.nets().items():
        ins = [(r, pin) for r, pin in conns if r in sel and r in P and pin in P[r].pads and _copper(P[r], pin)]
        outs = [(r, pin) for r, pin in conns if r not in sel and r in P and pin in P[r].pads and _copper(P[r], pin)]
        if not ins or not outs or n in ground:
            continue
        best = min(((math.dist(P[a].pad(pa), P[b].pad(pb)), (a, pa), (b, pb)) for a, pa in ins for b, pb in outs))
        (a, pa), (b, pb) = best[1], best[2]
        w = float(classes.get(n, {}).get("width", d.board.get("track_mm", 0.25)))
        w = max(w, float((d.net_info(n) or {}).get("min_width_mm") or 0))
        fpid, dia = _tp_footprint(w)
        terms.append({"net": n, "from": f"{a}.{pa}", "to": f"{b}.{pb}", "p": P[a].pad(pa), "q": P[b].pad(pb),
                      "fp": fpid, "dia": dia})
    tp_parts = {}
    for i, t in enumerate(sorted(terms, key=lambda t: (not any(t["net"] in g["nets"] for g in iso), -t["dia"], t["net"]))):
        t["ref"] = f"{TP_PREFIX}{i + 1}"
        tp_parts[t["ref"]] = t

    # region rules of the new design (the TPs of a region's nets join that region as members)
    fpl = None
    if slot_cuts and not fp_raw:
        fp_raw = {"construction": "single", "regions": []}
    if fp_raw:
        fpl = copy.deepcopy(fp_raw)
        fpl["regions"] = list(fpl.get("regions") or [])
        for r, g in slot_cuts:  # source board coordinates: clipped and moved with the other regions below
            if g.geom_type != "Polygon":
                g = max(g.geoms, key=lambda q: q.area)
            fpl["regions"].append({"name": f"{r}_cut", "cut": True,
                                   "poly": [[round(x, 4), round(y, 4)] for x, y in list(g.exterior.coords)[:-1]],
                                   "source": f"cutout: the Edge.Cuts slot of {r}, clipped to the window"})
        regs = []
        for g in fpl.get("regions") or []:
            cg = _clip_region(g, win, dx, dy)
            if cg is not None:
                regs.append(cg)
        for g in regs:
            gn = _globs(g.get("nets"))
            if gn:
                mine = [t["ref"] for t in terms if _match(t["net"], gn)]
                if mine:
                    g["items"] = list(g.get("items") or []) + mine
        names = {g["name"] for g in regs}
        items = []
        for it in fpl.get("items") or []:
            if it.get("refs") or it.get("groups"):
                refs = [r for r in comps if _match(r, _globs(it.get("refs")))] + \
                       [r for r in comps if comps[r].get("group") in (it.get("groups") or [])]
                if not refs:
                    continue
                it = dict(it)
                if it.get("at"):
                    it["at"] = [round(float(it["at"][0]) + dx, 4), round(float(it["at"][1]) + dy, 4)]
                if it.get("region") and it["region"] not in names:
                    it.pop("region")
                items.append(it)
        inames = {it["name"] for it in items}
        for g in regs:
            if g.get("items"):
                g["items"] = [e for e in g["items"] if e in inames or any(fnmatch.fnmatchcase(r, e) for r in comps)
                              or e in tp_parts]
            if g.get("allow"):
                g["allow"] = [e for e in g["allow"] if e in inames or any(fnmatch.fnmatchcase(r, e) for r in comps)]
            if g.get("from"):
                g["from"] = [e for e in g["from"] if e in names or e in fplan.KINDS]
        fpl["regions"] = regs
        fpl["items"] = items
        fpl.pop("nets", None)
    # every TP is a fixed one-pad part; place them one by one along the border
    fpdata = {}
    for t in terms:
        if t["fp"] not in fpdata:
            fpdata[t["fp"]] = libs.footprint(t["fp"])
    kept = {r: P[r] for r in comps if r in P}
    for r in blk_members:
        kept[r] = P[r]
    cut_polys = []
    for g in (fpl or {}).get("regions") or []:
        if g.get("cut"):
            cg = dict(g)
            if cg.get("circle"):
                cut_polys.append(Point(cg["circle"][0], cg["circle"][1]).buffer(cg["circle"][2]))
            elif cg.get("rect"):
                cut_polys.append(box(*cg["rect"]))
            elif cg.get("poly"):
                cut_polys.append(Polygon(cg["poly"]))
    outline = box(0, 0, Wc, Hc)
    if cut_polys:
        outline = outline.difference(unary_union(cut_polys))
    # moved copies of the kept parts (new board coordinates)
    K = {}
    for r, p in kept.items():
        q = copy.copy(p)
        q.x, q.y = p.x + dx, p.y + dy
        K[r] = q
    pad_obs = [(_pad_box(q, n), d.components[r].pins.get(n), r) for r, q in K.items() for n in q.pads
               if _copper(q, n) and _front_cu(q, n)]
    yards = [(q.side_box("front") or q.box()) if q.sides else q.box() for q in K.values()
             if any(_copper(q, n) for n in q.pads)]
    from shapely import affinity
    # rule areas of the kept parts that keep footprints out (keep-out zones, a part's Edge.Cuts slot)
    ko_areas = [(affinity.translate(a["poly"], dx, dy), a["name"]) for a in (P.areas or [])
                if a.get("owner") in kept and "footprints" in set(a.get("forbids") or ())]
    excl = []  # exclusive regions: (poly, region nets globs, member TP refs)
    for g in (fpl or {}).get("regions") or []:
        if g.get("cut"):
            continue
        poly = (Point(g["circle"][0], g["circle"][1]).buffer(g["circle"][2]) if g.get("circle")
                else box(*g["rect"]) if g.get("rect") else Polygon(g["poly"]) if g.get("poly") else None)
        if poly is None:
            continue
        excl.append({"poly": poly, "excl": g.get("kind") in fplan.EXCLUSIVE, "kind": g.get("kind"),
                     "nets": _globs(g.get("nets")),
                     "members": set(g.get("items") or []), "name": g["name"]})
    placed = []
    warnings = []
    inner = outline.buffer(-margin)
    for t in sorted(tp_parts.values(), key=lambda t: t["ref"]):
        f = fpdata[t["fp"]]
        x0, y0, x1, y1 = f["bbox"]
        half = max(x1 - x0, y1 - y0) / 2 + COURTYARD_MARGIN
        rp = t["dia"] / 2
        cand_pts = []
        for ring in range(3):  # the border line first, then two rows further in when it is full
            ins = margin + half + ring * (2 * half + clearance)
            ix0, iy0, ix1, iy1 = ins, ins, Wc - ins, Hc - ins
            if ix1 <= ix0 or iy1 <= iy0:
                break
            # border sides inside the source board, as segments of the inset rectangle (the router meets the rest
            # of the board there)
            sides = []
            if not on_edge["top"]:
                sides.append(((ix0, iy0), (ix1, iy0)))
            if not on_edge["right"]:
                sides.append(((ix1, iy0), (ix1, iy1)))
            if not on_edge["bottom"]:
                sides.append(((ix1, iy1), (ix0, iy1)))
            if not on_edge["left"]:
                sides.append(((ix0, iy1), (ix0, iy0)))
            for (ax, ay), (bx, by) in sides:
                L = math.hypot(bx - ax, by - ay)
                k = max(1, int(L / STEP_MM))
                for j in range(k + 1):
                    cand_pts.append((ring, ax + (bx - ax) * j / k, ay + (by - ay) * j / k))
        px, py = t["p"][0] + dx, t["p"][1] + dy
        qx, qy = t["q"][0] + dx, t["q"][1] + dy
        if not cand_pts:
            warnings.append(f"{t['net']}: the window is the whole board, no terminal")
            continue
        inside_q = 0 <= qx <= Wc and 0 <= qy <= Hc
        if not inside_q:
            lo, hi = 0.0, 1.0
            for _ in range(40):
                mid = (lo + hi) / 2
                mx, my = px + (qx - px) * mid, py + (qy - py) * mid
                if 0 <= mx <= Wc and 0 <= my <= Hc:
                    lo = mid
                else:
                    hi = mid
            tx, ty = px + (qx - px) * lo, py + (qy - py) * lo
        else:
            tx, ty = qx, qy
        cand_pts.sort(key=lambda c: math.hypot(c[1] - tx, c[2] - ty) + 4.0 * c[0])  # 4 mm along the border per row
        net = t["net"]
        best, best_bad = None, None
        for _ring, cx, cy in cand_pts:
            bad, why = 0.0, []
            yb = (cx - half, cy - half, cx + half, cy + half)
            if not inner.contains(box(*yb)):
                bad += 10
                why.append("outline")
            for b in yards:
                if b[0] < yb[2] and yb[0] < b[2] and b[1] < yb[3] and yb[1] < b[3]:
                    bad += 5
                    why.append("courtyard")
                    break
            for (b, pn, _r) in pad_obs:
                if pn == net:
                    continue
                need = rp + _iso_req(iso, clearance, net, pn)
                ddx = max(b[0] - cx, 0, cx - b[2])
                ddy = max(b[1] - cy, 0, cy - b[3])
                if math.hypot(ddx, ddy) < need:
                    bad += 1 + need - math.hypot(ddx, ddy)
                    why.append(f"pad {_r} ({pn})")
            for o in placed:
                need = rp + o["dia"] / 2 + _iso_req(iso, clearance, net, o["net"])
                cheb = max(abs(o["x"] - cx), abs(o["y"] - cy))
                if cheb < half + o["half"] + 0.05:  # courtyards apart
                    bad += 1 + half + o["half"] + 0.05 - cheb
                    why.append(o["ref"])
                elif math.hypot(o["x"] - cx, o["y"] - cy) < need:
                    bad += 1 + need - math.hypot(o["x"] - cx, o["y"] - cy)
                    why.append(o["ref"])
            pt = box(*yb)
            for poly, nm in ko_areas:
                if poly.intersects(pt):
                    bad += 3
                    why.append(f"keep-out {nm}")
                    break
            in_mains = any(g["kind"] == "mains" and t["ref"] in g["members"] for g in excl)
            for g in excl:
                # placer rule (floorplan.placer_regions): a barrier region lets the mains members in
                mem = t["ref"] in g["members"]
                allowed = mem or (g["kind"] == "barrier" and in_mains)
                if mem and not g["poly"].contains(pt):
                    bad += 3
                    why.append(f"outside region {g['name']}")
                elif g["excl"] and not allowed and g["poly"].intersects(pt):
                    bad += 3
                    why.append(f"inside region {g['name']}")
            if bad == 0:
                best = (cx, cy)
                break
            if best_bad is None or bad < best_bad[0]:
                best_bad = (bad, (cx, cy), why)
        if best is None:
            best = best_bad[1]
            warnings.append(f"{t['ref']} ({net}): no border position meets every rule (penalty {best_bad[0]:.2f}: {', '.join(best_bad[2][:4])})")
        t["x"], t["y"] = round(best[0], 4), round(best[1], 4)
        t["half"] = half
        placed.append(t)
    for t in placed:
        comps[t["ref"]] = {"value": "TP", "symbol": TP_SYMBOL, "footprint": t["fp"], "pins": {"1": t["net"]},
                           "group": "cutout_terminal",
                           "placement": {"x": t["x"], "y": t["y"], "rot": 0, "fixed": True}}
    raw["components"] = comps
    nets_raw = {n: v for n, v in (d.raw.get("nets") or {}).items() if n in kept_nets}
    if nets_raw:
        raw["nets"] = nets_raw
    if keep_blocks:
        raw["blocks"] = keep_blocks
    if fpl:
        raw["floorplan"] = fpl
    # isolation groups and budgets that still apply
    if board.get("isolation"):
        gs = []
        for g in board["isolation"]:
            g = dict(g)
            if isinstance(g.get("nets"), list):
                g["nets"] = [x for x in g["nets"] if any(fnmatch.fnmatchcase(n, x) for n in kept_nets)]
                if not g["nets"]:
                    continue
            gs.append(g)
        board["isolation"] = gs
        if not gs:
            board.pop("isolation")
    bud = []
    import re
    for b in d.raw.get("budgets") or []:
        refs = set(re.findall(r"([A-Za-z_][A-Za-z_0-9]*)\.[A-Za-z0-9_]+", " ".join(map(str, b.get("path") or []))))
        if refs and refs <= set(comps):
            bud.append(b)
    if bud:
        raw["budgets"] = bud

    # ---- files
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    lib_used = {"sym": set(), "fp": set()}
    proj_root = d.dir.resolve()
    for r, c in comps.items():
        if c.get("symbol") and ":" in c["symbol"]:
            try:
                sp = libs._sym_lib_path(c["symbol"].split(":", 1)[0])
                if proj_root in sp.resolve().parents:
                    lib_used["sym"].add(sp)
            except FileNotFoundError:
                pass
        if c.get("footprint") and ":" in c["footprint"]:
            fpp = libs.footprint_path(c["footprint"])
            if proj_root in fpp.resolve().parents:
                lib_used["fp"].add(fpp)
    for e in keep_blocks.values():
        f = core_blocks.find(str(e.get("use")), d)
        if f is not None and proj_root in f.resolve().parents:
            dst = out / "blocks" / f.parent.name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(f.parent, dst)
    if (out / "lib").exists():
        shutil.rmtree(out / "lib")
    for sp in sorted(lib_used["sym"]):
        dst = out / "lib" / "symbols" / sp.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if sp.is_dir() else shutil.copy2)(sp, dst)
    for fpp in sorted(lib_used["fp"]):
        dst = out / "lib" / "footprints" / fpp.parent.name / fpp.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fpp, dst)
    (out / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False, width=120, allow_unicode=True))
    rec = {"source": source or str(d.dir), "source_project": d.name,
           "placement": str(placement) if placement else "build/placement.json",
           "selection": {"parts": pats or None, "region": region if isinstance(region, (str, type(None))) else list(region),
                         "nets": npats or None},
           "window": [wx0, wy0, wx1, wy1], "frame_mm": frame + extra, "offset": [dx, dy], "board_mm": [Wc, Hc],
           "free": sorted(free), "dropped_neighbors": sorted(set(dropped)),
           "terminals": [{"ref": t["ref"], "net": t["net"], "from": t["from"], "to": t["to"], "at": [t["x"], t["y"]],
                          "footprint": t["fp"]} for t in placed],
           "warnings": warnings}
    (out / "cutout.yaml").write_text(yaml.safe_dump(rec, sort_keys=False, width=120))
    return rec


def unfix(design_path, record: dict | None = None) -> list[str]:
    """Make the parts the source placer was free to move (`free` of cutout.yaml) movable again in the design at
    `design_path` (the re-placement test). Returns the refs made free."""
    p = Path(design_path)
    rec = record if record is not None else yaml.safe_load((p.parent / "cutout.yaml").read_text())
    raw = yaml.safe_load(p.read_text())
    free = [r for r in rec.get("free") or [] if r in raw.get("components", {})]
    for r in free:
        raw["components"][r].pop("placement", None)
    for b in rec.get("free") or []:
        if b.startswith("block:") and b[6:] in (raw.get("blocks") or {}):
            raw["blocks"][b[6:]].pop("at", None)
            free.append(b)
    p.write_text(yaml.safe_dump(raw, sort_keys=False, width=120, allow_unicode=True))
    return free


__all__ = ["cutout", "unfix", "load_placement", "rot"]
