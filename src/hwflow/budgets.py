"""Requirement budgets as layout constraints (design.yaml `budgets:`).

A budget is the part of a requirement margin that layout may spend on copper: the resistance of a current path
(`kind: copper_r`, max_mohm) or the copper length between pads (`kind: copper_len`, max_mm, e.g. a decoupling
capacitor to its IC pin). Before routing, a copper_r budget becomes a track width for the nets of its path
(`widths`); after routing, every budget is measured on the extracted copper (`measure`) and a budget over its limit
is a layout error that names the path, the value, the limit and the worst stretch of copper.
"""

from __future__ import annotations

import math
import re

import numpy as np

KINDS = ("copper_r", "copper_len")
DETOUR = 1.0        # routed length over the Manhattan pad distance (width estimate before routing; `detour:` overrides).
#                     home-thermostat-3: two routed guard paths measured 8.4 and 9.2 mOhm against 15.6 estimated at
#                     1.3 x and 2 vias per leg; 1.0 x and 1 via per leg fit them
VIAS_PER_LEG = 1    # vias per leg in the width estimate (see DETOUR)
WIDTH_STEP = 0.05   # derived widths round up to this grid (mm)
MAX_WIDTH_MM = 3.0  # widest track a budget asks for (`board.budget_max_track_mm`); past it, width alone cannot meet it


def _pads(text: str) -> list[tuple[str, str]]:
    """'M1.3, M2.3' -> [('M1', '3'), ('M2', '3')]: pads joined by a comma are one end (parallel parts, IC pins)."""
    out = []
    for tok in re.split(r"[,|\s]+", text.strip()):
        if tok:
            ref, _, pin = tok.partition(".")
            out.append((ref, pin))
    return out


def legs(design, b: dict) -> tuple[list[dict], list[str]]:
    """The legs of a budget path, each on one net: {text, net, a, b} with pad ends, or b None for a whole net (the
    worst pad pair of that net). Returns (legs, problems)."""
    out, bad = [], []
    path = b.get("path") or []
    for item in [path] if isinstance(path, str) else path:
        item = str(item)
        if "->" not in item:
            if item.strip() not in design.nets():
                bad.append(f"path item {item!r} is neither `pad -> pad` nor a net")
            else:
                out.append({"text": item.strip(), "net": item.strip(), "a": None, "b": None})
            continue
        a, _, c = item.partition("->")
        ends = [_pads(a), _pads(c)]
        nets = set()
        for ref, pin in ends[0] + ends[1]:
            comp = design.components.get(ref)
            net = comp.pins.get(pin) if comp else None
            if net is None:
                bad.append(f"pad {ref}.{pin} in {item!r} is not a mapped pin")
            nets.add(net)
        if not ends[0] or not ends[1]:
            bad.append(f"{item!r} needs pads on both sides of ->")
        elif len(nets) > 1 and None not in nets:
            bad.append(f"{item!r} spans nets {sorted(nets)}: a leg is on one net (a part between two nets ends one "
                       f"leg and starts the next)")
        else:
            out.append({"text": item.strip(), "net": next(iter(nets)), "a": ends[0], "b": ends[1]})
    return out, bad


def budgets(design) -> list[dict]:
    return [dict(b, name=str(b.get("name") or f"budget{i + 1}"))
            for i, b in enumerate(design.raw.get("budgets") or []) if isinstance(b, dict)]


def check(design) -> list[dict]:
    """Static findings for `budgets:` (design.check)."""
    f = []
    for i, b in enumerate(design.raw.get("budgets") or []):
        if not isinstance(b, dict):
            f.append({"sev": "error", "code": "budget", "msg": f"budgets[{i}] is not a mapping"})
            continue
        name = b.get("name") or f"budgets[{i}]"
        kind = b.get("kind", "copper_r")

        def err(msg):
            f.append({"sev": "error", "code": "budget", "msg": f"budget {name}: {msg}"})
        if kind not in KINDS:
            err(f"kind {kind!r} is not one of {', '.join(KINDS)}")
            continue
        lim = "max_mohm" if kind == "copper_r" else "max_mm"
        try:
            if float(b.get(lim)) <= 0:
                err(f"{lim} must be positive")
        except (TypeError, ValueError):
            err(f"needs {lim} (a number)")
        if kind == "copper_r":
            try:
                if float(b.get("current_a")) <= 0:
                    err("current_a must be positive")
            except (TypeError, ValueError):
                err("needs current_a (the current the limit is stated at)")
        if not b.get("source"):
            err("needs `source`: the arithmetic from the requirement to the limit")
        lg, bad = legs(design, b)
        for x in bad:
            err(x)
        if not lg and not bad:
            err("needs a `path`")
    return f


# ------------------------------------------------------------------ before routing: widths

def via_r(board: dict) -> float:
    """Resistance of one plated via barrel (ohm), as postlayout.Extraction: 25 um plating, board thickness long."""
    from .postlayout import RHO_CU
    return RHO_CU * board["thickness_mm"] * 1e-3 / (math.pi * board["via_drill_mm"] * 1e-3 * 25e-6)


def _est_mm(leg: dict, parts: dict, design) -> float | None:
    """Manhattan distance between the closest pads of the two ends (whole net: the farthest pad pair)."""
    def xy(ref, pin):
        p = parts.get(ref)
        return p.pad(pin) if p is not None and pin in p.pads else None
    if leg["a"] is None:
        pts = [q for q in (xy(r, p) for r, p in design.nets().get(leg["net"], [])) if q]
        return max((abs(p[0] - q[0]) + abs(p[1] - q[1]) for p in pts for q in pts), default=None)
    pa = [q for q in (xy(*e) for e in leg["a"]) if q]
    pb = [q for q in (xy(*e) for e in leg["b"]) if q]
    return min((abs(p[0] - q[0]) + abs(p[1] - q[1]) for p in pa for q in pb), default=None)


def widths(design, parts: dict) -> tuple[dict[str, float], list[dict]]:
    """Track widths the copper_r budgets ask for on this placement: {net: mm} and one row per budget.

    One width for the whole path (it gives the least copper area for a resistance): the estimated length is the
    Manhattan pad distance of each leg times `detour` (DETOUR), and VIAS_PER_LEG vias per leg come off the budget
    first. R = rho * L / (w * t) at 20 C with t = 35 um x copper_oz, as the post-layout extraction. The width rounds
    up to WIDTH_STEP and stops at board.budget_max_track_mm; a budget the cap cannot meet says so with the numbers.
    net_classes takes the larger of this width and the net's own, so a budget never narrows a net."""
    from .postlayout import RHO_CU
    b = design.board
    t_m = 0.035 * float(b["copper_oz"]) * 1e-3
    cap = float(b.get("budget_max_track_mm", MAX_WIDTH_MM))
    rv = via_r(b)
    out, rows = {}, []
    for bd in budgets(design):
        kind = bd.get("kind", "copper_r")
        lg, bad = legs(design, bd)
        if bad or not lg:
            continue
        detour = float(bd.get("detour", DETOUR))
        ests = [_est_mm(x, parts, design) for x in lg]
        if any(e is None for e in ests):
            continue
        L = sum(ests) * detour
        row = {"name": bd["name"], "req": bd.get("req"), "kind": kind, "est_mm": round(L, 1), "detour": detour}
        if kind == "copper_len":
            lim = float(bd["max_mm"])
            row.update(limit=lim, ok=L <= lim, msg=f"{L:.1f} mm estimated ({detour:g} x Manhattan) against "
                                                   f"{lim:g} mm" + ("" if L <= lim else ": the placement is too spread"))
            rows.append(row)
            continue
        lim = float(bd["max_mohm"]) * 1e-3
        vias = VIAS_PER_LEG * len(lg)
        left = lim - vias * rv
        need = RHO_CU * L * 1e-3 / (t_m * left) * 1e3 if left > 0 else math.inf
        w = min(math.ceil(need / WIDTH_STEP - 1e-9) * WIDTH_STEP, cap) if need < cap else cap
        r_at = (RHO_CU * L * 1e-3 / (t_m * w * 1e-3) + vias * rv) * 1e3
        ok = need <= cap
        row.update(limit=float(bd["max_mohm"]), width_mm=round(w, 2),
                   need_mm=round(need, 2) if math.isfinite(need) else None, nets=sorted({x["net"] for x in lg}), est_mohm=round(r_at, 1), ok=ok,
                   msg=(f"{w:.2f} mm on {', '.join(sorted({x['net'] for x in lg}))}: {L:.1f} mm estimated "
                        f"({detour:g} x Manhattan) + {vias} vias = {r_at:.1f} mOhm against {bd['max_mohm']} mOhm"
                        + ("" if ok else f"; width alone cannot meet it (needs {need:.2f} mm, cap {cap:g} mm): "
                                         f"the path is too long for the budget, bring its parts closer")))
        rows.append(row)
        for x in lg:
            out[x["net"]] = max(out.get(x["net"], 0.0), round(w, 2))
    return out, rows


# ------------------------------------------------------------------ after routing: measurement

def _net_graph(ex, net: str):
    find = ex.uf.find
    els = [(find(e["a"]), find(e["b"]), e) for e in ex.elements if e["net"] == net]
    return [(a, c, e) for a, c, e in els if a != c]


def _solve(els, A: list, B: list):
    """Resistance of the resistor network `els` from the pad nodes A to the pad nodes B, with 1 A split equally
    over the pads of each end (parallel parts or several pins of one IC share the current: the part between them
    dominates the split). The resistance is the copper loss at 1 A, sum I^2 R, which for one pad per end is the
    plain two-point resistance and otherwise the copper share of the drop through equal parallel parts.
    Returns (ohm, [(element, current at 1 A)]) or None when a pad of A or B is not connected to the others."""
    adj: dict = {}
    for a, c, _ in els:
        adj.setdefault(a, []).append(c)
        adj.setdefault(c, []).append(a)
    seen, stack = {A[0]}, [A[0]]
    while stack:
        n = stack.pop()
        for m in adj.get(n, ()):
            if m not in seen:
                seen.add(m)
                stack.append(m)
    if any(n not in seen for n in A + B):
        return None
    nodes = sorted(seen, key=str)
    idx = {n: i for i, n in enumerate(nodes)}
    G = np.zeros((len(nodes), len(nodes)))
    use = [(a, c, e) for a, c, e in els if a in seen]
    for a, c, e in use:
        g = 1.0 / max(e["R"], 1e-9)
        i, j = idx[a], idx[c]
        G[i, i] += g
        G[j, j] += g
        G[i, j] -= g
        G[j, i] -= g
    inj = np.zeros(len(nodes))
    for n in A:
        inj[idx[n]] += 1.0 / len(A)
    for n in B:
        inj[idx[n]] -= 1.0 / len(B)
    ref = idx[B[0]]
    keep = [i for i in range(len(nodes)) if i != ref]
    v = np.zeros(len(nodes))
    if keep:
        v[keep] = np.linalg.solve(G[np.ix_(keep, keep)], inj[keep])
    cur = [(e, (v[idx[a]] - v[idx[c]]) / max(e["R"], 1e-9)) for a, c, e in use]
    return sum(i * i * e["R"] for e, i in cur), cur


def _roots(ex, pads) -> list | None:
    """Copper node of each pad, or None when a pad has no copper node."""
    if any(p not in ex.pad_nodes for p in pads):
        return None
    return [ex.uf.find(ex.pad_nodes[p]) for p in pads]


def _leg_r(ex, leg: dict, design):
    """(ohm, currents, pad pair text) of one leg on the routed copper, or None when it is not connected."""
    els = _net_graph(ex, leg["net"])
    if leg["a"] is not None:
        A, B = _roots(ex, leg["a"]), _roots(ex, leg["b"])
        r = _solve(els, A, B) if A and B else None
        return (r[0], r[1], leg["text"]) if r else None
    pads = [p for p in design.nets().get(leg["net"], []) if p in ex.pad_nodes]
    worst = None
    for i, p in enumerate(pads):
        for q in pads[i + 1:]:
            r = _solve(els, _roots(ex, [p]), _roots(ex, [q]))
            if r is None:
                return None
            if worst is None or r[0] > worst[0]:
                worst = (r[0], r[1], f"{p[0]}.{p[1]} -> {q[0]}.{q[1]}")
    return worst


def measure(design, ex) -> list[dict]:
    """Every budget measured on the routed copper of the extraction `ex` (postlayout.Extraction).

    copper_r: the series resistance of each leg between its pad ends (pads of one end shorted together, vias
    included), summed over the legs; the worst stretch is the (net, layer, width) group that takes the largest share
    of it (I^2 R at 1 A, which adds up to the path resistance), with the length of its copper that carries at least
    10% of the current. copper_len: the shortest copper path of each leg, summed."""
    rows = []
    for bd in budgets(design):
        kind = bd.get("kind", "copper_r")
        lg, bad = legs(design, bd)
        if bad or not lg:
            continue
        row = {"name": bd["name"], "req": bd.get("req"), "kind": kind, "path": [x["text"] for x in lg], "legs": []}
        missing = []
        if kind == "copper_len":
            lim, tot = float(bd["max_mm"]), 0.0
            for x in lg:
                best = None
                for pa in _roots(ex, x["a"] or []) or []:
                    for pb in _roots(ex, x["b"] or []) or []:
                        d = ex.path_len(pa, pb)
                        best = d if best is None or (d is not None and d < best) else best
                if best is None:
                    missing.append(x["text"])
                    continue
                tot += best
                row["legs"].append({"leg": x["text"], "mm": round(float(best), 2)})
            row.update(limit=lim, unit="mm", value=None if missing else round(tot, 2))
        else:
            lim, tot, groups = float(bd["max_mohm"]), 0.0, {}
            for x in lg:
                r = _leg_r(ex, x, design)
                if r is None:
                    missing.append(x["text"])
                    continue
                tot += float(r[0])
                row["legs"].append({"leg": r[2], "net": x["net"], "mohm": round(float(r[0]) * 1e3, 2)})
                for e, i in r[1]:
                    key = (x["net"], e.get("layer", "via"), e.get("w"))
                    g = groups.setdefault(key, [0.0, 0.0])
                    g[0] += float(i * i * e["R"])
                    g[1] += float(e["len"]) if abs(i) >= 0.1 else 0.0
            row.update(limit=lim, unit="mOhm", value=None if missing else round(tot * 1e3, 2),
                       current_a=float(bd["current_a"]))
            if groups:
                (net, layer, w), (p, mm) = max(groups.items(), key=lambda kv: kv[1][0])
                row["worst"] = (f"{net} vias {p * 1e3:.1f} mOhm" if w is None else
                                f"{net} {layer} {w:.2f} mm wide, {mm:.1f} mm long, {p * 1e3:.1f} mOhm")
            if not missing:
                row["drop_mv"] = round(tot * float(bd["current_a"]) * 1e3, 2)
        row["ok"] = not missing and row["value"] <= lim + 1e-9
        row["msg"] = _msg(row, missing)
        rows.append(row)
    return rows


def _msg(r: dict, missing: list[str]) -> str:
    head = f"budget {r['name']}{' (' + r['req'] + ')' if r.get('req') else ''}: "
    if missing:
        return head + f"not measured: {'; '.join(missing)} not connected by tracks"
    s = head + f"{' + '.join(r['path'])}: {r['value']:g} {r['unit']} (limit {r['limit']:g} {r['unit']}"
    if r.get("drop_mv") is not None:
        s += f", {r['drop_mv']:.1f} mV at {r['current_a']:g} A"
    s += ")"
    if r.get("worst"):
        s += f"; worst {r['worst']}"
    return s + ("" if r["ok"] else " FAIL")


def findings(design, ex) -> list[dict]:
    """Post-layout layout checks: a budget over its limit (or not connected) is an error."""
    return [{"sev": "info" if r["ok"] else "error", "code": "budget", "msg": r["msg"]} for r in measure(design, ex)]
