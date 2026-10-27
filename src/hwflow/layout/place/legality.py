"""Placement legality: why a placement with violations is illegal, and where the build goes back to.

An illegal placement (courtyard overlap, a part off the board or in a keep-out, a floorplan region broken, isolation)
is never routed (pipeline.stages.stage_layout, docs/layout-flow.md "Placement legality gate"); `hwflow floorplan`
fails on an illegal quick placement the same way. `layout.diagnose` re-exports these names.
"""

from __future__ import annotations

import math
import re

from ...core import floorplan


def _side(design, ref) -> str:
    c = design.components.get(ref)
    return str(c.get("side", "front")) if c else "front"


def board_fill(design, parts: dict, W: float, H: float) -> dict[str, float]:
    out = {"front": 0.0, "back": 0.0}
    for r, p in parts.items():
        if r in design.components:
            x0, y0, x1, y1 = p.box()
            out[_side(design, r) if _side(design, r) in out else "front"] += (x1 - x0) * (y1 - y0)
    area = floorplan.outline(design).area if floorplan.cuts(design) else W * H  # cut regions take board area away
    return {k: v / area if area > 0 else 0.0 for k, v in out.items()}


# placement fill (placer courtyards with their margin over the board area inside margin_mm) above which the settle
# legalizer found no legal placement: legal at 0.87-0.89 (synth60 x0.55, synth150 x0.6), none at 0.99 and above
# (sensor_preamp 16 x 12 mm, synth60 x0.5); scripts/legal_bench.py, docs/layout-flow.md
PLACE_FILL_MAX = 0.95


def placement_verdict(design, parts: dict, W: float, H: float, viol: list[str]) -> dict:
    """Why a placement with violations `viol` is illegal and where to go back to: the fill of each board face (placer
    courtyards with their margin) and of each floorplan region the violations touch, against PLACE_FILL_MAX, the
    fill above which the parts do not place, and the smallest board (same aspect ratio) or region that would hold
    them at that fill. back_to: `architecture` when a face or region is over the limit or the violations involve only
    fixed parts (board size, part count or sizes, or the fixed positions must change), else `layout` (the area allows
    a legal placement; the placer did not find it)."""
    fmax = PLACE_FILL_MAX
    mg = float(design.board.get("margin_mm", 0.0))
    outline = floorplan.outline(design)
    usable = outline.buffer(-mg, join_style="mitre").area if floorplan.cuts(design) else (W - 2 * mg) * (H - 2 * mg)
    fill = {s: v * (outline.area if floorplan.cuts(design) else W * H) / max(usable, 1e-9)
            for s, v in board_fill(design, parts, W, H).items()}
    side = max(fill, key=fill.get)
    f = fill[side]
    names = sorted({x for v in viol for x in re.findall(r"floorplan region '([^']+)'", v)})
    regs, over = [], []
    try:
        rr, _ = floorplan.placer_regions(design, parts)
    except Exception:  # noqa: BLE001 - region rows are context; the board fill stands without them
        rr = []
    for g in rr:
        poly = g.get("poly")
        if poly is None or poly.is_empty:
            continue
        hit = g["name"] in names
        for v in viol:  # an overlap whose parts sit in this region
            m = re.match(r"courtyard overlap (\S+)/(\S+)$", v)
            if m and all(r in parts for r in m.groups()) and any(r in g["members"] for r in m.groups()):
                hit = True
        if not hit:
            continue
        load = sum((parts[r].box()[2] - parts[r].box()[0]) * (parts[r].box()[3] - parts[r].box()[1])
                   for r in g["members"] if r in parts)
        rf = load / poly.area if poly.area > 0 else math.inf
        regs.append({"name": g["name"], "fill": round(rf, 3), "load_mm2": round(load, 1), "area_mm2": round(poly.area, 1)})
        if rf > fmax:
            over.append(f"region {g['name']} is {rf:.0%} full ({load:.0f} of {poly.area:.0f} mm2; at most {fmax:.0%} "
                        f"places: it needs {load / fmax:.0f} mm2, or fewer or smaller members)")
    refs = {r for v in viol for r in re.split(r"[\s/.]+", v) if r in parts}
    fixed_only = bool(refs) and all(parts[r].fixed for r in refs)
    k = math.sqrt(f / fmax) if f > 0 else 1.0  # on the area inside the margin
    head = f"placement illegal: {len(viol)} violation{'s' if len(viol) != 1 else ''} ({'; '.join(viol[:4])}" \
           + ("; ..." if len(viol) > 4 else "") + ")"
    face = (f"the courtyards (with the placer margin) fill {f:.0%} of the {side} face of {W:g} x {H:g} mm inside the "
            f"{mg:g} mm board margin (at most {fmax:.0%} places)")
    if f > fmax or over:
        back = "architecture"
        fits = [f"a board of at least {math.ceil((W - 2 * mg) * k + 2 * mg)} x {math.ceil((H - 2 * mg) * k + 2 * mg)} mm"
                if f > fmax else None, *over]
        msg = f"{head}; {face}: it does not fit; " + "; ".join(x for x in fits if x) + \
              " (or fewer or smaller parts, or parts on the other face)"
    elif fixed_only:
        back = "architecture"
        msg = f"{head}; only fixed parts are involved: their positions in design.yaml must change"
    else:
        back = "layout"
        msg = f"{head}; {face}, so a legal placement should exist: the placer did not find one (another seed, or " \
              f"report it as a placer failure)"
    bud = [v for v in viol if v.startswith("budget ")]
    if bud:  # copper_len budgets (placer.budget_violations): named in full, whatever the other violations are
        msg += "; copper_len budgets the placement already breaks (never routed): " + "; ".join(bud)
    return {"ok": False, "violations": len(viol), "fill": {s: round(v, 3) for s, v in fill.items()}, "regions": regs,
            "back_to": back, "msg": msg, "first": viol[:20]}
