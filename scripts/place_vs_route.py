#!/usr/bin/env python
"""Place mode against route mode on the PCBWorld sets: of the boards clean with the human placement (route-mode JSON),
the share also clean with hwflow's placement (place-mode JSON; clean as in layout_bench: fully routed, DRV 0, no placer
violation), per set and pooled, with the failure class of each lost board.

    uv run python scripts/place_vs_route.py --route R_a.json --place P_a.json [--route R_b.json --place P_b.json ...]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter


def route_clean(r: dict) -> bool:
    return r.get("routability") == 1.0 and r.get("drv_after") == 0


def place_clean(r: dict) -> bool:
    return route_clean(r) and not r.get("violations_placer")


def why(r: dict) -> str:
    if r.get("timeout"):
        return "timeout"
    if r.get("error"):
        return "error"
    if r.get("violations_placer"):
        v = Counter(str(x).split(":")[0].split(" ")[0] for x in r.get("placement_violations") or [])
        return "placer violation " + ",".join(f"{k}x{n}" for k, n in v.most_common(3))
    if r.get("routability") != 1.0:
        return f"unrouted (rout {r.get('routability'):.2f}, {(r.get('after') or {}).get('unrouted')} open)"
    by = (r.get("after") or {}).get("by_type") or {}
    return "DRV " + ",".join(f"{k}x{n}" for k, n in sorted(by.items(), key=lambda kv: -kv[1]) if k != "unconnected_items")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", action="append", required=True)
    ap.add_argument("--place", action="append", required=True)
    ap.add_argument("-v", action="store_true", help="list every lost board")
    a = ap.parse_args()
    tot_n = tot_d = 0
    for rf, pf in zip(a.route, a.place):
        R, P = json.load(open(rf)), json.load(open(pf))
        den = sorted(n.split(":", 1)[1] for n, dd in R["designs"].items() if dd["runs"] and route_clean(dd["runs"][0]))
        pr = {n.split(":", 1)[1]: dd["runs"][0] for n, dd in P["designs"].items() if dd["runs"]}
        evald = [b for b in den if b in pr]
        num = [b for b in evald if place_clean(pr[b])]
        lost = [b for b in evald if not place_clean(pr[b])]
        tot_n += len(num); tot_d += len(den)
        print(f"{P.get('pcbench_set')}: {len(num)}/{len(den)} = {len(num) / max(len(den), 1):.3f}"
              f" (evaluated {len(evald)}; git {P.get('git')})")
        cls = Counter(why(pr[b]).split(" ")[0] + (" " + why(pr[b]).split(" ")[1] if why(pr[b]).startswith("placer") else "") for b in lost)
        print("   lost by class:", dict(cls))
        if a.v:
            for b in lost:
                r = pr[b]
                print(f"   {b[:40]:40s} {why(r)}  board {r.get('board_mm')} att {r.get('attempts')} wall {r.get('wall_s')}")
    print(f"POOLED: {tot_n}/{tot_d} = {tot_n / max(tot_d, 1):.3f}")


if __name__ == "__main__":
    main()
