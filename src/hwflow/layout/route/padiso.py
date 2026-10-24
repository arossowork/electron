"""Pre-layout check: the pads of a region's nets against the conductive keep-outs and cuts of that region.

A floorplan region with `isolation_mm` and `nets` keeps its copper that far from every `conductive: true` keep-out or
cut (floorplan.conductive_needs: a screw boss, a metal frame). The router keeps tracks and vias out
(routing.conductive_keepouts), but it cannot move a pad: a pad of a fixed part inside that distance fails the layout
(postlayout.conductive_clearances) after a full build. This check finds it in `hwflow check`, on the pads of the
fixed parts (or of every placed part when given `parts` or a routed `board`), with each pad's real outline (a round
pad is a circle, not its bounding box).

Interface: findings(design, libs, parts=None, board=None) -> [{check, back_to, refs, nets, need_mm, have_mm, msg,
at, fixed}], one per pad and keep-out under the distance.
"""

from __future__ import annotations

NAME = "padiso"


def findings(design, libs, parts=None, board=None) -> list[dict]:
    from . import barrier
    from ..place import floorplan
    needs = floorplan.conductive_needs(design)
    if not needs:
        return []
    model = barrier.Model(design, libs, parts, board)
    out = []
    for nd in needs:
        for p in model.pads:
            if p["net"] not in nd["nets"]:
                continue
            dist = p["own"].distance(nd["poly"])
            if dist >= nd["mm"] - 0.01:
                continue
            c = p["own"].centroid
            back = "architecture" if p["fixed"] else "placement"
            fix = ("move the fixed part or the keep-out, or change the region's isolation_mm (a product decision)"
                   if p["fixed"] else "move the part away from the keep-out (placement)")
            out.append({"check": NAME, "back_to": back, "refs": [p["ref"]], "nets": [p["net"]],
                        "need_mm": nd["mm"], "have_mm": round(dist, 2), "at": [round(c.x, 2), round(c.y, 2)],
                        "fixed": p["fixed"],
                        "msg": f"pad {p['ref']}.{p['number']} ({p['net']}) is {dist:.2f} mm from conductive "
                               f"{nd['keepout']}, region {nd['region']} needs {nd['mm']:g} mm (isolation_mm). "
                               f"No router can move a pad. Options: {fix}"})
    return out
