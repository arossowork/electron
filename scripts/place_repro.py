#!/usr/bin/env python
"""Placement reproducibility check: run placer.place (the layout flow's placement, as stage_layout calls it) on a
design and print a hash of the placement (every part's x, y, rotation, bit for bit), the violations, the wall time
and the work counters of the bounded steps. The same design and seed must give the same hash under any CPU load.

    uv run python scripts/place_repro.py <project> [--seeds 4] [--iters 30000] [--seed0 1]
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


def placement_hash(parts) -> str:
    h = hashlib.sha256()
    for r in sorted(parts):
        p = parts[r]
        h.update(f"{r}:{float(p.x).hex()}:{float(p.y).hex()}:{float(p.r).hex()};".encode())
    return h.hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--iters", type=int, default=30000)
    ap.add_argument("--seed0", type=int, default=1)
    ap.add_argument("--out", type=Path, help="write the placement here (placement.json format)")
    a = ap.parse_args()
    from hwflow.core.design import Design
    from hwflow.core.libs import Libs
    from hwflow.layout.place import placer
    tmp = Path(tempfile.mkdtemp(prefix="place_repro_"))
    proj = tmp / a.project.name
    shutil.copytree(a.project, proj, ignore=shutil.ignore_patterns("build", "fw"))
    try:
        d = Design.load(proj)
        libs = Libs(design=d)
        placer.WORK.update(pack_evals=0, aborts=[])
        t = time.time()
        parts, cost, viol = placer.place(d, libs, seeds=a.seeds, iters=a.iters, seed0=a.seed0)
        row = {"design": d.name, "hash": placement_hash(parts), "violations": len(viol), "cost": round(cost, 3),
               "seconds": round(time.time() - t, 1), "work": dict(getattr(placer, "WORK", {}))}
        if a.out:
            a.out.write_text(json.dumps({r: [p.x, p.y, p.r] for r, p in parts.items()}, indent=1))
        print(json.dumps(row))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
