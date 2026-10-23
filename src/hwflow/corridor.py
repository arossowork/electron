"""Corridor proof: can a net whose rules shrink its room (board.isolation between_mm, a floorplan region's conductive
isolation, nets.<name>.layers) still pass between the fixed copper around it at its track width?

`barrier` builds the room F_L(net) of every net from the rules that apply to all nets: clearances, the isolation
distance between a group member and a non-member, keep-out areas, cuts. This module adds the rules that only some
nets carry, each one a hard rule of the flow (the router and the post-layout check enforce it):

  - board.isolation between_mm: a member keeps between_mm from the pads of the group's other members (the
    `between` KiCad clearance rules, routing.isolation_keepouts); before, barrier gave such a pair only the class
    clearance;
  - a floorplan region with isolation_mm and `nets` next to a `conductive: true` keep-out or cut (a screw, a frame):
    the region's nets keep isolation_mm from its shape on every layer (routing.conductive_keepouts, the
    `conductive-isolation` post-layout error);
  - nets.<name>.layers: the net's tracks may lie only on those layers (the `net-layer` post-layout error), so its
    pads connect through the union of those layers only, joined by a via only where it has two or more of them.

The region outline itself is no barrier: copper of a region net outside its region is a warning (region-nets), not
a rule. Every added quantity keeps the bound of barrier: spacings are the smallest any rule allows the pair (a
clearance rule that lowers it, by footprint or one this module cannot read, still wins), a conductive keep-out is
its inscribed polygon grown by the distance, a via site counts where its disc fits on the net's layers only (the
other layers left out, so more sites, never fewer), and other nets' tracks and pours are never obstacles. So the
room is still an upper bound, and a net it cannot connect cannot be routed.

corridor: the net's pads are cut apart at its track width w (class, width_mm, current or budget), where a track may
  neck down to the board minimum for at most routing.NECK_MAX_MM per stretch (barrier.Model.groups with necks: the
  flow accepts such a neck on a current net, as a track-current warning), or at the minimum track itself. A net that
  barrier already cuts apart (without these rules) is left to barrier. The largest track that does connect is found
  by bisection and reported as have_mm against need_mm = w; `at` is where the two sides come closest, and `refs` are
  the parts whose copper or keep-outs bound the corridor there. Back to `architecture` when all of them are fixed
  (move a fixed part, keep-out or connector, give the net another layer, or change a spacing: a product decision),
  else `layout` (the placement of a movable part closes it).

Interface: findings(design, libs, parts=None, board=None) -> [{check, back_to, refs, nets, need_mm, have_mm, msg,
at, fixed, layer, blockers}]; margin(...) gives the widest track per constrained net, failing or not; neck_needed(...)
gives the nets that connect only with necks (the router routes them narrow first).
"""

from __future__ import annotations

import fnmatch
import math

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from . import barrier, floorplan, pcb, routing

NAME = "corridor"
EPS = 1e-6
TOL_MM = 0.1        # an obstacle this close to the corridor's edge at `at` counts as bounding it (message only)
SEARCH_MM = 20.0    # margin(): the widest track looked for


class Model(barrier.Model):
    """barrier.Model plus the rules of single nets: between_mm, conductive isolation, nets.<name>.layers."""

    def __init__(self, design, libs, parts=None, board=None, use=None):
        super().__init__(design, libs, parts, board, use)
        self.between = [(set(g["members"]), float(g["between_mm"])) for g in pcb.isolation_groups(design)
                        if g["members"] and g["between_mm"] is not None]
        self.cond = [dict(c, area=c["poly"].buffer(c["mm"], quad_segs=barrier.QS))
                     for c in floorplan.conductive_needs(design)]
        self.layers = pcb.net_layers(design)

    def net_copper(self, net) -> list[str]:
        lays = self.layers.get(net)
        return [L for L in self.copper if L in lays] if lays else self.copper  # none valid: design check reports it

    def clearance(self, net, other, ref=None, iso=True) -> float:
        c = super().clearance(net, other, ref, iso)
        if iso and other is not None and other != net:
            for mem, mm in self.between:
                if net in mem and other in mem:  # the same rule-lowering terms as barrier: max distributes over min
                    c = max(c, min(mm, self.clr_low, self.clr_ref.get(ref, math.inf)))
        return c

    def keepouts(self, net, layer, what="tracks", skip=()):
        out = super().keepouts(net, layer, what, skip)
        return out + [c["area"] for c in self.cond if net in c["nets"]]

    @staticmethod
    def neck_len(a, b, k) -> float:
        """The distance between where the narrow component `k` (grown by 1e-4) touches `a` and where it touches `b`:
        a narrow stretch through k leaves a at the first and enters b at the second, so it is at least that long (a
        tighter lower bound than barrier's a.distance(b), which may be measured at a place k never reaches)."""
        ta, tb = a.intersection(k), b.intersection(k)
        return a.distance(b) if ta.is_empty or tb.is_empty else ta.distance(tb)

    def vias(self, net, drop=frozenset()):
        if len(self.net_copper(net)) < 2:  # one layer: a via joins nothing (and does not end a neck stretch)
            return Polygon()
        return super().vias(net, drop)

    def spacing(self, net, other, ref) -> tuple[float, str]:
        """(distance, the rule that sets it) between `net` copper and a pad of `other` on part `ref`."""
        c = self.clearance(net, other, ref)
        if other is not None and other != net and any(net in m and other in m for m, _ in self.between):
            return c, "between_mm"
        if any((net in m) != (other in m) for m, _ in self.iso):
            return c, "isolation"
        return c, "clearance"


def constrained(design) -> list[str]:
    """Nets whose room these rules shrink: members of an isolation group with between_mm, nets of a floorplan region
    that declares `nets`, nets with `layers`."""
    names = set(design.nets())
    out = set()
    for g in pcb.isolation_groups(design):
        if g["between_mm"] is not None:
            out |= set(g["members"])
    try:
        regs = floorplan.net_regions(design)
    except Exception:  # noqa: BLE001 - an invalid floorplan: design check reports it
        regs = []
    for r in regs:
        out |= {n for n in names if any(fnmatch.fnmatchcase(n, g) for g in r["nets"])}
    out |= set(pcb.net_layers(design))
    return sorted(out & names)


# ------------------------------------------------------------------ the check

def findings(design, libs, parts=None, board=None) -> list[dict]:
    """Corridor findings for the constrained nets with at least two pads of known position (module text)."""
    return barrier.with_budgets(design, parts, lambda: _findings(design, libs, parts, board))


def _nodes(model, design, net, forced) -> list[dict] | None:
    """The net's pads (barrier._nodes), or None when one of them is on none of the net's layers (design check
    reports that; it is no corridor)."""
    nodes = barrier._nodes(model, design, net, forced)
    lays = set(model.net_copper(net))
    if any(not lays & set(n["layers"]) for n in nodes):
        return None
    return nodes


def _connects(model, net, nodes, w, necks) -> bool:
    return len(model.groups(net, nodes, w, necks=necks)) == 1


def _widest(model, net, nodes, lo, hi, necks) -> float:
    """Largest width in [lo, hi] that connects the pads, given that lo does and hi does not."""
    while hi - lo > barrier.BISECT_MM:
        mid = (lo + hi) / 2
        if _connects(model, net, nodes, mid, necks):
            lo = mid
        else:
            hi = mid
    return lo


def _have(model, net, nodes, w, necks=True) -> float:
    """The widest track below w that connects the pads (with necks above the minimum track when `necks`), 0 when
    none does. The pads must not connect at w."""
    f = model.floor
    if necks and _connects(model, net, nodes, f, False):
        return _widest(model, net, nodes, f, w, w > f + EPS)
    if not _connects(model, net, nodes, barrier.BISECT_MM, False):
        return 0.0
    return _widest(model, net, nodes, barrier.BISECT_MM, min(f, w) if necks else w, False)


def _pinch(model, net, nodes, plain) -> tuple | None:
    """(gap, (x, y), layer) where the corridor of the widest plain track `plain` is narrowest: the two sides at the
    next width step come closest there."""
    hi = plain + barrier.BISECT_MM
    g = model.groups(net, nodes, hi)
    return model.gap(net, nodes, g, hi) if len(g) > 1 else None


def _findings(design, libs, parts, board) -> list[dict]:
    nets = constrained(design)
    if not nets:
        return []
    model = Model(design, libs, parts, board)
    forced = barrier._forced(design, libs) if model.pre else {}
    base = None
    out = []
    for net in nets:
        nodes = _nodes(model, design, net, forced)
        if nodes is None or len(nodes) < 2:
            continue
        w = model.width(net)
        if _connects(model, net, nodes, w, w > model.floor + EPS):
            continue
        base = base or barrier.Model(design, libs, parts, board)
        bn = barrier._nodes(base, design, net, forced)
        if not _connects(base, net, bn, base.floor, False) or \
                (w > base.floor + EPS and not _connects(base, net, bn, w, True)):
            continue  # barrier reports it (barrier or channel) without these rules
        out.append(_finding(model, design, net, nodes, w))
    if not model.pre and out:  # does the finding hold with fixed copper only (the placement of movable parts aside)?
        fm = Model(design, libs, parts, board, use={r for r, v in model.pos.items() if v[3]})
        pads = fm.pads
        for f in out:
            net = f["nets"][0]
            nodes = _nodes(model, design, net, {})
            fm.pads, fm._free = [p for p in pads if p["net"] != net] + nodes, {}
            f["fixed"] = not _connects(fm, net, nodes, f["need_mm"], f["need_mm"] > fm.floor + EPS)
    return out


def _blockers(model, net, at, layer, reach) -> list[dict]:
    """The obstacles within `reach` of the corridor's edge at point `at` on `layer`: pads (with the spacing rule),
    keep-out areas, conductive keep-outs and cuts."""
    P = Point(at)
    out = []
    for p in model.pads:
        if p["net"] == net or layer not in p["layers"]:
            continue
        c, rule = model.spacing(net, p["net"], p["ref"])
        d = p["obst"].distance(P) - c
        if d <= reach:
            out.append({"ref": p["ref"], "what": f"{p['ref']}.{p['number']} ({p['net'] or 'no net'})", "mm": c,
                        "rule": rule, "fixed": model.pos[p["ref"]][3], "d": d})
    for a in model.areas:
        if layer in a["layers"] and "tracks" in a["forbids"] and a["poly"].distance(P) <= reach:
            ref = a["owner"]
            out.append({"ref": ref, "what": f"keep-out {a['name'] or ref}", "mm": 0.0, "rule": "keep-out",
                        "fixed": model.pos[ref][3] if ref in model.pos else True, "d": a["poly"].distance(P)})
    for c in model.cond:
        if net in c["nets"] and c["poly"].distance(P) - c["mm"] <= reach:
            out.append({"ref": None, "what": f"conductive {c['keepout']}", "mm": c["mm"], "rule": "conductive isolation",
                        "fixed": True, "d": c["poly"].distance(P) - c["mm"]})
    for k in floorplan.cuts(model.design):
        if k["poly"].distance(P) - barrier.EDGE_MM <= reach and not any(x["what"] == f"conductive {k['name']}" for x in out):
            out.append({"ref": None, "what": f"cut {k['name']}", "mm": barrier.EDGE_MM, "rule": "edge",
                        "fixed": True, "d": k["poly"].distance(P) - barrier.EDGE_MM})
    return sorted(out, key=lambda x: x["d"])


def _finding(model, design, net, nodes, w, fixed_only=False) -> dict:
    """`fixed_only`: the model holds the fixed parts only (placer.corridor_blocks), as before placement."""
    alone = model.pre or fixed_only
    necks = w > model.floor + EPS
    have = _have(model, net, nodes, w, necks)
    plain = _have(model, net, nodes, w, False) if necks else have
    g = model.groups(net, nodes, w, necks=necks)
    gap = _pinch(model, net, nodes, plain)  # the narrowest place of the widest corridor
    at = [round(gap[1][0], 2), round(gap[1][1], 2)] if gap else None
    layer = gap[2] if gap else model.net_copper(net)[0]
    hi = plain + barrier.BISECT_MM
    blk = _blockers(model, net, gap[1], layer, gap[0] / 2 + hi / 2 + TOL_MM) if gap else []
    refs = sorted({b["ref"] for b in blk if b["ref"]})
    back = "architecture" if all(b["fixed"] for b in blk) else "layout"
    lays = model.net_copper(net)
    a, b = barrier._label(nodes, g[0]), barrier._label(nodes, g[-1])
    seen, why = set(), []
    for x in blk:
        if x["what"] not in seen:
            seen.add(x["what"])
            why.append(f"{x['what']} {x['mm']:g} mm {x['rule']}" if x["rule"] != "keep-out" else x["what"])
    where = f", narrowest at ({at[0]}, {at[1]}) on {layer}," if at else ""
    neck = (f" ({have:.2f} mm with necks of up to {routing.NECK_MAX_MM:g} mm to {model.floor:g} mm)"
            if necks else "")
    opts = ("move the fixed part, connector or keep-out that makes the corridor, give the net another layer, or "
            "change a spacing (a product decision)" if back == "architecture" else
            "move the movable parts named off the corridor (placement)")
    msg = (f"{net} ({w:g} mm track, layers {', '.join(lays)}): between pads {a} and {b} the widest corridor"
           f"{where} fits a track of {plain:.2f} mm{neck}, need {w:g} mm. Bounded by "
           f"{'; '.join(why[:6]) or 'the board edge'}. No router can route it with "
           f"{'the fixed parts and keep-outs alone' if alone else 'this placement'}. Options: {opts}")
    return {"check": NAME, "back_to": back, "refs": refs, "nets": [net], "need_mm": round(w, 3),
            "have_mm": round(have, 3), "plain_mm": round(plain, 3), "msg": msg, "at": at, "layer": layer,
            "fixed": alone, "blockers": [{k: v for k, v in x.items() if k != "d"} for x in blk]}


def neck_needed(design, libs, parts=None, board=None) -> dict[str, dict]:
    """The constrained nets whose pads the corridor proof connects at their track width only with necks (a track
    narrowed to the board minimum for at most routing.NECK_MAX_MM per stretch): net -> {need_mm, plain_mm}, plain_mm
    the widest track that connects without a neck. A full-width router pass cannot route such a net (the room is an
    upper bound), so pcb.krt_route routes it narrow first and widens it back (routing.widen)."""
    def run():
        nets = constrained(design)
        if not nets:
            return {}
        model = Model(design, libs, parts, board)
        forced = barrier._forced(design, libs) if model.pre else {}
        out = {}
        for net in nets:
            nodes = _nodes(model, design, net, forced)
            if nodes is None or len(nodes) < 2:
                continue
            w = model.width(net)
            if w <= model.floor + EPS or _connects(model, net, nodes, w, False) \
                    or not _connects(model, net, nodes, w, True):
                continue
            out[net] = {"need_mm": round(w, 3), "plain_mm": round(_have(model, net, nodes, w, False), 3)}
        return out
    return barrier.with_budgets(design, parts, run)


def margin(design, libs, parts=None, board=None) -> list[dict]:
    """Calibration: per constrained net, the widest track that connects its pads without necks and with them (up to
    SEARCH_MM), against its width, and where the narrowest place of the no-neck corridor lies."""
    def run():
        model = Model(design, libs, parts, board)
        forced = barrier._forced(design, libs) if model.pre else {}
        rows = []
        for net in constrained(design):
            nodes = _nodes(model, design, net, forced)
            if nodes is None or len(nodes) < 2:
                continue
            w = model.width(net)
            row = {"net": net, "width": w, "layers": model.net_copper(net), "pads": len(nodes)}
            for key, necks in (("plain", False), ("necks", True)):
                if not _connects(model, net, nodes, barrier.BISECT_MM, False):
                    row[key] = 0.0
                elif _connects(model, net, nodes, SEARCH_MM, necks):
                    row[key] = SEARCH_MM
                else:
                    row[key] = round(_widest(model, net, nodes, barrier.BISECT_MM, SEARCH_MM, necks), 3)
            gap = _pinch(model, net, nodes, row["plain"]) if row["plain"] < SEARCH_MM else None
            if gap:
                reach = gap[0] / 2 + (row["plain"] + barrier.BISECT_MM) / 2 + TOL_MM
                row["at"], row["layer"] = [round(gap[1][0], 2), round(gap[1][1], 2)], gap[2]
                row["bounded_by"] = [x["what"] for x in _blockers(model, net, gap[1], gap[2], reach)]
            rows.append(row)
        return rows
    return barrier.with_budgets(design, parts, run)
