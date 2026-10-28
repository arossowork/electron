#!/usr/bin/env python
"""Routing reproducibility check: route a saved placement (build/placement.json) with KiCadRoutingTools
(krt.run_krt, every net, no route cache) `--runs` times in fresh work folders and print a hash of the copper (every
track and via, bit for bit), the unrouted count and the wall time of each run.

    uv run python scripts/route_repro.py <project> [--runs 2] [--placement path]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def copper_hash(tracks, vias) -> str:
    h = hashlib.sha256()
    for t in sorted(json.dumps(t, sort_keys=True) for t in tracks):
        h.update(t.encode())
    for v in sorted(json.dumps(v, sort_keys=True) for v in vias):
        h.update(v.encode())
    return h.hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--placement", type=Path)
    ap.add_argument("--groups", action="store_true", help="route group by group (regionroute), as the layout stage")
    a = ap.parse_args()
    from hwflow.core.design import Design
    from hwflow.core.libs import Libs
    from hwflow.layout.place import placer
    from hwflow.layout.route import krt
    saved = json.loads((a.placement or a.project / "build" / "placement.json").read_text())
    for k in range(a.runs):
        tmp = Path(tempfile.mkdtemp(prefix="route_repro_"))
        proj = tmp / a.project.name
        shutil.copytree(a.project, proj, ignore=shutil.ignore_patterns("build", "fw"))
        try:
            d = Design.load(proj)
            libs = Libs(design=d)
            if saved.get("__board_mm__"):
                d.raw.setdefault("board", {})["width_mm"], d.raw["board"]["height_mm"] = saved["__board_mm__"]
            parts = placer.build_parts(d, libs)
            for r, p in parts.items():
                if r in saved and not p.fixed:
                    p.x, p.y, p.r = saved[r]
            from hwflow.sim import budgets
            d.budget_widths = budgets.widths(d, parts)[0]
            wd = proj / "build"
            wd.mkdir(exist_ok=True)
            t = time.time()
            res = None
            if a.groups:
                from hwflow.layout.route import regionroute
                res = regionroute.route(d, libs, parts, wd, wd / "routecache")
            tracks, vias, summary, unrouted = res[:4] if res else krt.run_krt(d, libs, parts, wd)
            print(json.dumps({"run": k + 1, "hash": copper_hash(tracks, vias), "tracks": len(tracks), "vias": len(vias),
                              "unrouted": unrouted, "groups": bool(res), "seconds": round(time.time() - t, 1)}), flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
