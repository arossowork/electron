"""Multi-criteria optimization of component values and part choices against SPICE measures.

design.yaml:
  optimize:
    budget: 80                       # max evaluations
    margin: 0.1                      # keep measures 10% of their limit span inside the limits
    variables:
      - {ref: R1, min: 10k, max: 1meg, series: E96}
      - {ref: U2, choices: [{}, {mpn: OPA344, price_usd: 0.9, spice: {params: {EN: 30n}}}]}
    objectives:                      # weighted, minimized; goal max flips sign
      - {measure: out_noise_uv, weight: 1}
      - {measure: cost_usd, weight: 2}
Every measure with min/max in sim.analyses is a hard constraint.
"""

from __future__ import annotations

import copy
import math
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import spice
from ..core.design import E_SERIES, Design, fmt_value, parse_value


def _ladder(v: dict) -> list:
    if "choices" in v:
        return list(range(len(v["choices"])))
    lo, hi = parse_value(v["min"]), parse_value(v["max"])
    series = E_SERIES[v.get("series", "E24")]
    out = []
    dec = math.floor(math.log10(lo))
    while 10 ** dec <= hi * 10:
        for b in series:
            x = round(b * 10 ** dec, 12)
            if lo * 0.999 <= x <= hi * 1.001:
                out.append(x)
        dec += 1
    return sorted(set(out))


def _deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in b.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def apply_point(design: Design, variables: list[dict], point: list[int]) -> Design:
    raw = copy.deepcopy(design.raw)
    for v, idx in zip(variables, point):
        comp = raw["components"][v["ref"]]
        if "choices" in v:
            raw["components"][v["ref"]] = _deep_merge(comp, v["choices"][idx])
        else:
            unit = ""
            comp["value"] = fmt_value(_ladder(v)[idx], unit)
    d = Design(path=design.path, raw=raw)
    from ..core.design import Component
    for ref, c in raw["components"].items():
        d.components[ref] = Component(ref, c)
    return d


def evaluate(design: Design, workdir: Path) -> dict:
    res = spice.run_all(design, workdir)
    rows = {r["name"]: r for a in res for r in a["rows"]}
    cost, _ = design.cost()
    rows["cost_usd"] = {"name": "cost_usd", "value": cost, "pass": True}
    return rows


def score(rows: dict, objectives: list[dict], ref: dict, margin: float = 0.0) -> tuple[float, int]:
    """Penalty for constraint violations (limits tightened by `margin`) plus weighted objectives."""
    viol = 0.0
    nfail = 0
    for r in rows.values():
        v = r.get("value")
        lo, hi = r.get("min"), r.get("max")
        if v is None:
            if r.get("pass") is False:
                nfail += 1
                viol += 10
            continue
        lo = None if lo is None else float(lo)
        hi = None if hi is None else float(hi)
        span = (hi - lo) if lo is not None and hi is not None else abs(lo if lo is not None else hi or 1.0)
        if lo is not None:
            lo += margin * span
        if hi is not None:
            hi -= margin * span
        bad = False
        if lo is not None and v < lo:
            viol += (lo - v) / max(abs(span), 1e-9)
            bad = True
        if hi is not None and v > hi:
            viol += (v - hi) / max(abs(span), 1e-9)
            bad = True
        nfail += bad
    obj = 0.0
    for o in objectives:
        r = rows.get(o["measure"])
        if not r or r.get("value") is None:
            obj += 10
            continue
        base = abs(ref.get(o["measure"]) or 1.0) or 1.0
        val = r["value"] / base
        obj += float(o.get("weight", 1)) * (-val if o.get("goal") == "max" else val)
    return 1000 * viol + obj, nfail


def run(design: Design, workdir: Path, budget: int | None = None, seed: int = 1, log=print) -> dict:
    spec = design.raw.get("optimize") or {}
    variables = spec.get("variables") or []
    objectives = spec.get("objectives") or []
    budget = budget or int(spec.get("budget", 60))
    if not variables:
        raise ValueError("design.yaml has no optimize.variables")
    ladders = [_ladder(v) for v in variables]
    # start at the current value (nearest ladder step)
    start = []
    for v, lad in zip(variables, ladders):
        if "choices" in v:
            start.append(0)
        else:
            cur = parse_value(design.components[v["ref"]].get("value"))
            start.append(min(range(len(lad)), key=lambda i: abs(math.log(lad[i] / cur))))
    cache: dict[tuple, tuple] = {}
    evals = [0]
    base_rows = evaluate(apply_point(design, variables, start), workdir / "opt" / "e0")
    ref = {k: r.get("value") for k, r in base_rows.items()}

    def f(point):
        key = tuple(point)
        if key not in cache:
            evals[0] += 1
            rows = evaluate(apply_point(design, variables, point), workdir / "opt" / f"e{evals[0]}")
            cache[key] = (*score(rows, objectives, ref, float(spec.get("margin", 0.1))), rows)
        return cache[key]

    best = list(start)
    bs, bf, _ = f(best)
    log(f"start score={bs:.4g} fails={bf}")
    rng = random.Random(seed)
    step = 4
    with ThreadPoolExecutor(max_workers=6) as pool:
        while evals[0] < budget:
            cands = []
            for i, lad in enumerate(ladders):
                for d in (-step, step):
                    p = list(best)
                    p[i] = min(len(lad) - 1, max(0, p[i] + d))
                    if p != best and tuple(p) not in cache:
                        cands.append(p)
            if not cands:
                if step > 1:
                    step = max(1, step // 2)
                    continue
                # random kick around the best point
                cands = []
                for _ in range(3):
                    p = [min(len(l) - 1, max(0, b + rng.randint(-3, 3))) for b, l in zip(best, ladders)]
                    if tuple(p) not in cache:
                        cands.append(p)
                if not cands:
                    break
            cands = cands[: max(1, budget - evals[0])]
            results = list(pool.map(lambda p: (p, f(p)), cands))
            improved = False
            for p, (s, nf, _) in results:
                if s < bs - 1e-9:
                    best, bs, bf, improved = p, s, nf, True
            if improved:
                log(f"eval {evals[0]}: score={bs:.4g} fails={bf} " +
                    ", ".join(_describe(v, l, i) for v, l, i in zip(variables, ladders, best)))
            elif step > 1:
                step = max(1, step // 2)
    rows = cache[tuple(best)][2] if tuple(best) in cache else base_rows
    return {"evals": evals[0], "score": bs, "fails": bf,
            "start": {v["ref"]: _describe(v, l, i) for v, l, i in zip(variables, ladders, start)},
            "best": {v["ref"]: _describe(v, l, i) for v, l, i in zip(variables, ladders, best)},
            "point": best, "measures": {k: r.get("value") for k, r in rows.items()},
            "baseline": ref}


def _describe(v, lad, i):
    if "choices" in v:
        ch = v["choices"][i]
        return ch.get("mpn") or ch.get("value") or f"choice{i}"
    return fmt_value(lad[i])


def apply(design: Design, result: dict):
    spec = design.raw.get("optimize") or {}
    d2 = apply_point(design, spec["variables"], result["point"])
    design.raw = d2.raw
    design.components = d2.components
    design.save()
