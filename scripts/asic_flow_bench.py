#!/usr/bin/env python
"""Combined-flow proxies (docs/asic-ideas.md, "Combined flow"): one placement sample of one design under one variant,
measured with the default cost model, the shapely pin access and the global router.

    uv run python scripts/asic_flow_bench.py --design DIR --variant NAME --seed0 S --out FILE.json [--seeds 4]
                                              [--iters 30000]
    uv run python scripts/asic_flow_bench.py --table FILE.json ...      # medians per design and variant

Variants (environment of the placement only; the measurement runs with every switch off):
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ASIC = {"HWFLOW_FLOW": "asic"}
VARIANTS = {
    "baseline": {},
    "rudy": {**ASIC, "HWFLOW_ASIC_DENSITY": "rudy"},
    "groute": {**ASIC, "HWFLOW_ASIC_DENSITY": "groute"},
    "groute70": {**ASIC, "HWFLOW_ASIC_DENSITY": "groute", "HWFLOW_ASIC_GR_TARGET": "0.7"},
    "noaccess": {**ASIC, "HWFLOW_ASIC_DENSITY": "rudy", "HWFLOW_ASIC_ACCESS": "0"},
    "nodensity": {**ASIC, "HWFLOW_ASIC_DENSITY": "none"},
    "nomacro": {**ASIC, "HWFLOW_ASIC_DENSITY": "rudy", "HWFLOW_ASIC_MACRO": "0"},
    "nodp": {**ASIC, "HWFLOW_ASIC_DENSITY": "rudy", "HWFLOW_ASIC_DP": "0"},
    "noanneal": {**ASIC, "HWFLOW_ASIC_DENSITY": "rudy", "HWFLOW_ASIC_ANNEAL": "0"},
}
__doc__ += "\n".join(f"    {k}: {v or 'today'}" for k, v in VARIANTS.items()) + "\n"


def sample(a):
    for k in [k for k in os.environ if k.startswith("HWFLOW_ASIC_") or k == "HWFLOW_FLOW"]:
        del os.environ[k]
    env = VARIANTS[a.variant]
    if a.density and "HWFLOW_ASIC_DENSITY" in env:
        env = {**env, "HWFLOW_ASIC_DENSITY": a.density}
    os.environ.update(env)
    from hwflow import asicflow, corridor, globalroute, placer
    from hwflow.design import Design
    from hwflow.libs import Libs
    d = Design.load(Path(a.design))
    libs = Libs(design=d)
    n0, f0 = len(placer.STAGES), len(asicflow.FEEDBACK)
    t = time.time()
    parts, cost, viol = placer.place(d, libs, seeds=a.seeds, iters=a.iters, seed0=a.seed0)
    place_s = time.time() - t
    stages = placer.STAGES[n0:]
    fb = asicflow.FEEDBACK[f0:]
    for k in env:
        del os.environ[k]
    b = d.board
    pl = placer.Placer(d, parts, b["width_mm"], b["height_mm"])
    pl.cost()
    hpwl, ovf = pl.t_wire, pl.congestion()
    os.environ["HWFLOW_RUDY_PADS"] = "1"
    plp = placer.Placer(d, parts, b["width_mm"], b["height_mm"])
    plp.cost()
    ovfp = plp.congestion()
    del os.environ["HWFLOW_RUDY_PADS"]
    t = time.time()
    m = corridor.Model(d, libs, parts=parts)
    acc = globalroute.pin_access(d, m)
    gn = d.ground_nets() if b.get("gnd_pour") else set()
    rows = [v for v in acc.values() if v["net"] not in gn]
    flagged = [v for v in rows if v["n"] <= 1]
    zero = [v for v in rows if v["n"] == 0 and v["floor"] > 0]
    acc_s = time.time() - t
    t = time.time()
    gr = globalroute.route(globalroute.build(d, libs, parts=parts, model=m))
    gr_s = time.time() - t
    out = {"design": Path(a.design).name, "variant": a.variant, "seed0": a.seed0, "place_s": round(place_s, 1),
           "hpwl": round(hpwl, 1), "ovf": round(ovf, 2), "ovf_pads": round(ovfp, 2), "violations": len(viol),
           "viol": viol[:5], "access_pads": len(flagged), "access_nets": len({v["net"] for v in flagged}),
           "zero_exit_pads": len(zero), "zero_exit_nets": sorted({v["net"] for v in zero}),
           "gr_overflow": gr.total_overflow, "gr_edges": len(gr.overflow_edges), "gr_failing": len(gr.failing()),
           "acc_s": round(acc_s, 1), "gr_s": round(gr_s, 1), "stages": stages, "feedback": fb}
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k not in ("stages", "feedback")}))


def table(files):
    rows = [json.loads(Path(f).read_text()) for f in files]
    keys = ("place_s", "hpwl", "ovf", "ovf_pads", "gr_overflow", "access_pads", "zero_exit_pads", "violations")
    by = {}
    for r in rows:
        by.setdefault((r["design"], r["variant"]), []).append(r)
    print("| design | variant | n | " + " | ".join(keys) + " | stage s |")
    print("|---" * (len(keys) + 4) + "|")
    for (dn, v), rs in sorted(by.items()):
        cells = []
        for k in keys:
            xs = [r[k] for r in rs]
            med = statistics.median(xs)
            cells.append(f"{med:g} [{min(xs):g}-{max(xs):g}]" if k in ("hpwl", "ovf_pads", "gr_overflow") else f"{med:g}"
                         if k != "violations" else str(sum(xs)))
        st = {}
        for r in rs:
            for s in r["stages"]:
                st.setdefault(s["stage"], []).append(s["s"])
        stxt = ", ".join(f"{k} {statistics.median(x):.1f}" for k, x in st.items())
        print(f"| {dn} | {v} | {len(rs)} | " + " | ".join(cells) + f" | {stxt} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design")
    ap.add_argument("--variant", choices=list(VARIANTS))
    ap.add_argument("--density", help="override the density source of the variant (rudy, groute, none)")
    ap.add_argument("--seed0", type=int, default=1)
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--iters", type=int, default=30000)
    ap.add_argument("--out")
    ap.add_argument("--table", nargs="+")
    a = ap.parse_args()
    if a.table:
        table(a.table)
    else:
        sample(a)


if __name__ == "__main__":
    main()
