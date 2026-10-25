#!/usr/bin/env python
"""Routing-only benchmark: one design, one fixed placement, the routing flow under one or more variants.

A variant is `base` (the current flow, environment as given) or `NAME:KEY=VALUE[,KEY=VALUE...]`, the same flow with
those environment variables set (e.g. `legacy:HWFLOW_FLOW=legacy`). The guide-driven router this script was first
written for (branch asic-router, HWFLOW_ASIC_GUIDES; results in docs/asic-ideas.md, "Guide-driven routing") is not
in this tree, so its modes are not offered.

The design is copied to <work>/<name>/ (never touched in place), its schematic stage runs once (layout needs the
library tables), the placement is fixed (placement.json: from --placement, or one placer run saved to
<work>/placement.json and reused), and pipeline.stages.stage_layout runs with reuse=True: route (region groups),
repair, prune, DRC, as in the layout flow, with no placement. The route cache of the copy is cleared first. Then the
post-layout stage runs and the requirement trace is counted. One JSON line per run goes to <work>/results.jsonl.

    uv run python scripts/route_bench.py <project> --work DIR [--placement FILE] [--mode base|NAME:K=V,...]...
                                         [--place-seeds 1] [--place-iters 8000] [--seed0 1]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = ("build", "scratch", "fw", ".usage.jsonl")


def hwflow(*args) -> subprocess.CompletedProcess:
    """`hwflow <args>` from this source tree."""
    return subprocess.run([sys.executable, "-m", "hwflow.cli", *args], cwd=ROOT, capture_output=True, text=True)


def parse_mode(m: str) -> tuple[str, dict[str, str]]:
    """`base` or `NAME:KEY=VALUE[,KEY=VALUE...]` -> (name, environment)."""
    if m == "base":
        return m, {}
    name, sep, rest = m.partition(":")
    env = dict(kv.split("=", 1) for kv in rest.split(",") if "=" in kv) if sep else {}
    if not name or not env:
        raise argparse.ArgumentTypeError(f"mode {m!r}: base or NAME:KEY=VALUE[,KEY=VALUE...]")
    return name, env


def prepare(project: Path, dst: Path):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(project, dst, ignore=lambda d, names: [n for n in names if Path(d) == project and n in SKIP])
    r = hwflow("build", str(dst), "--until", "sch", "--force")
    if r.returncode:
        print(f"schematic stage of {dst} exited {r.returncode}:\n{(r.stdout + r.stderr)[-2000:]}")


def placement(project: Path, work: Path, given: Path | None, seeds: int, iters: int, seed0: int = 1) -> Path:
    pj = work / "placement.json"
    if given:
        shutil.copy(given, pj)
    if pj.exists():
        return pj
    from hwflow.core.design import Design
    from hwflow.core.libs import Libs
    from hwflow.layout.place import placer
    prep = work / "place"
    prepare(project, prep)
    d = Design.load(prep)
    libs = Libs(design=d)
    t = time.time()
    declared = [d.board.get("width_mm"), d.board.get("height_mm")]
    parts, cost, viol = placer.place(d, libs, seeds=seeds, iters=iters, seed0=seed0)
    pj.write_text(json.dumps({"__design_board__": declared,
                              "__board_mm__": [d.board["width_mm"], d.board["height_mm"]],
                              **{r: [p.x, p.y, p.r] for r, p in parts.items()}}, indent=1))
    print(f"placement: {time.time() - t:.0f} s, cost {cost:.1f}, violations {viol}")
    return pj


def run(project: Path, work: Path, pj: Path, name: str, env: dict[str, str]) -> dict:
    from hwflow.core.design import Design
    from hwflow.core.libs import Libs
    from hwflow.pipeline import stages
    from hwflow.pipeline.status import _save_status
    from hwflow.pipeline.trace import trace
    dst = work / name
    prepare(project, dst)
    (dst / "build").mkdir(exist_ok=True)
    shutil.copy(pj, dst / "build" / "placement.json")
    shutil.rmtree(dst / "build" / "routecache", ignore_errors=True)
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)  # get_settings() reads the environment on each call
    try:
        d = Design.load(dst)
        libs = Libs(design=d)
        load0 = os.getloadavg()[0]
        t = time.time()
        r = stages.stage_layout(d, libs, reuse=True)
        secs = round(time.time() - t, 1)
        stages._record_layout(d, r, False)
        _save_status(d, "layout", r)
        try:
            _save_status(d, "post", stages.stage_post(d))
        except Exception as e:  # noqa: BLE001 - the routing numbers stand without it
            print(f"post: {type(e).__name__}: {e}")
        tr = trace(Design.load(dst))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    rep = r.get("repair") or {}
    row = {"design": d.name, "mode": name, "env": env, "unrouted": r["unrouted"], "drc_errors": r["drc_errors"],
           "drc_before_repair": rep.get("before"), "vias": r["vias"], "segments": r["segments"],
           "track_mm": round(sum(n["length_mm"] for n in (r.get("nets") or {}).values()), 1),
           "ok": r["ok"], "route_s": (r.get("timing") or {}).get("route_s"),
           "repair_s": (r.get("timing") or {}).get("repair_s"), "layout_s": secs, "load_start": round(load0, 1),
           "load_end": round(os.getloadavg()[0], 1), "router": str(r.get("router", ""))[:300],
           "trace": {k: tr.get(k) for k in ("covered", "failing", "unverified", "total")}}
    print(json.dumps(row))
    with (work / "results.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--placement", type=Path)
    ap.add_argument("--mode", action="append", type=parse_mode,
                    help="base, or NAME:KEY=VALUE[,KEY=VALUE...] (repeatable; default: base)")
    ap.add_argument("--place-seeds", type=int, default=1)
    ap.add_argument("--place-iters", type=int, default=8000)
    ap.add_argument("--seed0", type=int, default=1)
    a = ap.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    project = a.project.resolve()
    pj = placement(project, a.work.resolve(), a.placement, a.place_seeds, a.place_iters, a.seed0)
    for name, env in a.mode or [("base", {})]:
        run(project, a.work.resolve(), pj, name, env)


if __name__ == "__main__":
    main()
