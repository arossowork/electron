#!/usr/bin/env python
"""Global-route experiment (docs/asic-ideas.md, "Global and detailed routing"): prediction quality of the global route
and of pin access against KiCadRoutingTools, and the effect of the global-route hints on KRT.

    uv run python scripts/groute_bench.py prep  --seed 1 --profile synth60 --out DIR [--iters 2000]
    uv run python scripts/groute_bench.py route --dir DIR --mode base|groute|layers [--access]
    uv run python scripts/groute_bench.py predict --dir DIR [--truth DIR/base/result.json]

prep: generate a synthetic board (scripts/make_synth_board.py), check + sch, a short placement, the unrouted board.
route: pcb.krt_route on that placement, with the switches of the mode (groute: HWFLOW_GROUTE=1, layers: also
HWFLOW_GROUTE_LAYERS=1), recording every router call (first-attempt open nets, tries), time, vias, open nets.
predict: global route (pad centres and pin access) and pin access, against the nets KRT failed in `truth`.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load(dirpath: Path):
    from hwflow.design import Design
    from hwflow.libs import Libs
    d = Design.load(dirpath)
    return d, Libs(design=d)


def _parts(d, libs, dirpath: Path):
    from hwflow import placer
    parts = placer.build_parts(d, libs)
    saved = json.loads((dirpath / "placement.json").read_text())
    for r, p in parts.items():
        if r in saved:
            p.x, p.y, p.r = saved[r]
    return parts


def prep(a):
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/make_synth_board.py"), "--seed", str(a.seed), "--profile",
                    a.profile, "--out", str(out)], check=True)
    from hwflow import cli, pcb, placer
    if a.shrink != 1.0:  # a denser variant: the same parts on a smaller board
        import yaml
        f = out / "design.yaml"
        raw = yaml.safe_load(f.read_text())
        for k in ("width_mm", "height_mm"):
            raw["board"][k] = round(raw["board"][k] * a.shrink)
        f.write_text(yaml.safe_dump(raw, sort_keys=False, width=160))
    d, libs = _load(out)
    cli.stage_check(d, libs)
    cli.stage_sch(d, libs)
    t = time.time()
    parts, cost, viol = placer.place(d, libs, seeds=1, iters=a.iters, seed0=a.seed)
    (out / "placement.json").write_text(json.dumps({r: [p.x, p.y, p.r] for r, p in parts.items()}))
    pcb.write_board(out / "unrouted.kicad_pcb", pcb.build_board(d, libs, parts))
    print(f"placed in {time.time() - t:.1f} s, cost {cost:.1f}, {len(viol)} violations")


def route(a):
    from hwflow import pcb
    dirpath = Path(a.dir).resolve()
    d, libs = _load(dirpath)
    parts = _parts(d, libs, dirpath)
    if a.mode in ("groute", "layers"):
        os.environ["HWFLOW_GROUTE"] = "1"
    if a.mode == "layers":
        os.environ["HWFLOW_GROUTE_LAYERS"] = "1"
    if a.access:
        os.environ["HWFLOW_GROUTE_ACCESS"] = "1"
    work = dirpath / (a.mode + ("_acc" if a.access else ""))
    work.mkdir(exist_ok=True)
    calls = []
    orig = pcb.route_class

    def rc(base, stem, retries, run, variants=None):
        texts = []

        def run2(args):
            t, rc_ = run(args)
            texts.append(t)
            return t, rc_
        best, tried, logs = orig(base, stem, retries, run2, variants)
        first = {}
        if texts:
            for ln in texts[0].splitlines():
                if ln.startswith("JSON_SUMMARY_MIN:"):
                    first = json.loads(ln.split(":", 1)[1])
        nets = base[base.index("--nets") + 1:]
        nets = nets[:next((i for i, x in enumerate(nets) if x.startswith("--")), len(nets))]
        calls.append({"stem": Path(stem).name, "nets": nets, "tries": len(tried), "tried": tried,
                      "first_open": pcb.krt_open_nets(first), "best_open": pcb.krt_open_nets(best[1]) if best else None})
        return best, tried, logs
    pcb.route_class = rc
    src = work / f"{d.name}_unrouted.kicad_pcb"
    pcb.write_board(src, pcb.build_board(d, libs, parts))
    pcb.write_project(work, d.name, d)
    t = time.time()
    cur, last, notes, npass = pcb.krt_route(d, src, work, f"{d.name}_krt", libs=libs)
    dt = time.time() - t
    rb = pcb.read_board(cur)
    open_final = sorted({n for s in last.values() for n in pcb.krt_open_nets(s)})
    res = {"mode": a.mode, "access": a.access, "route_s": round(dt, 1), "passes": npass,
           "router_calls": len(calls) and sum(c["tries"] for c in calls), "calls": calls,
           "unrouted": sum(pcb.krt_unrouted(s) for s in last.values()), "open_final": open_final,
           "vias": len([v for v in rb["vias"] if v["net"]]), "segments": len(rb["segments"]), "notes": notes}
    (work / "result.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "calls"}))


def predict(a):
    from hwflow import globalroute as G
    from hwflow import pcb
    dirpath = Path(a.dir).resolve()
    d, libs = _load(dirpath)
    board = Path(a.board) if a.board else dirpath / "unrouted.kicad_pcb"
    b = pcb.read_board(board)
    truth = json.loads(Path(a.truth).read_text()) if a.truth else None
    out = {}
    for acc in (False, True):
        t = time.time()
        P = G.build(d, libs, board=b, access=acc)
        res = G.route(P)
        out["access" if acc else "centre"] = {
            "wall_s": round(time.time() - t, 2), "build_s": res.build_s, "route_s": res.route_s,
            "access_s": G.pin_access.last_s if acc else 0, "iterations": res.iterations,
            "nets": len(res.nets), "overflow": res.total_overflow, "overflow_edges": len(res.overflow_edges),
            "failing": res.failing(), "crit": {n: r["crit"] for n, r in res.nets.items()},
            "pressure": {n: r["pressure"] for n, r in res.nets.items()},
            "no_access": [f"{r}.{p}" for r, p in res.no_access]}
        if acc:
            out["pin_access"] = [dict(r) for r in G.access_summary(P.access, 10 ** 6)]
    if truth:
        out["truth"] = {"first_open": sorted({n for c in truth["calls"] for n in c["first_open"]}),
                        "open_final": truth["open_final"],
                        "necked": [n for t in truth["notes"] for n in _necked(t)]}
    (dirpath / ("predict.json" if not a.board else Path(a.board).stem + ".predict.json")).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: {x: y for x, y in v.items() if x not in ("crit", "pressure")} if isinstance(v, dict) else len(v)
                      for k, v in out.items()}, indent=1)[:3000])


def _necked(note: str) -> list[str]:
    """Nets a krt_route note says were necked (router pad-entry neck-down or neck-down to a narrower width)."""
    import re
    if "neck-down" not in note or "rejected" in note or "still open" in note:
        return []
    m = re.search(r"neck-down(?: to [\d.]+ mm)?: (.*)", note)
    body = m.group(1) if m else note.split(":", 1)[-1]
    if "routed with the router's pad-entry neck-down" in note:
        body = note.split(":", 1)[1].split("routed with")[0]
        return [x.strip() for x in body.split(",") if x.strip()]
    return re.findall(r"([^\s,]+) [\d.]+ of [\d.]+ mm narrow", body)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("prep")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--profile", default="synth60")
    p.add_argument("--out", required=True)
    p.add_argument("--iters", type=int, default=2000)
    p.add_argument("--shrink", type=float, default=1.0, help="scale the board sides (density variant)")
    p = sp.add_parser("route")
    p.add_argument("--dir", required=True)
    p.add_argument("--mode", default="base", choices=["base", "groute", "layers"])
    p.add_argument("--access", action="store_true")
    p = sp.add_parser("predict")
    p.add_argument("--dir", required=True)
    p.add_argument("--board")
    p.add_argument("--truth")
    a = ap.parse_args()
    {"prep": prep, "route": route, "predict": predict}[a.cmd](a)


if __name__ == "__main__":
    main()
