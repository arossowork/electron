#!/usr/bin/env python
"""Placement legality benchmark: run the layout flow's placer (placer.place) on a design, optionally on a resized
board, and report the placement violations left and the courtyard fill, so the legalizer can be checked on designs
where the area allows a legal placement and on over-full ones.

    uv run python scripts/legal_bench.py <project> [--scale 0.7] [--layers N] [--seeds 2] [--iters 10000] [--seed0 1]
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--width", type=float)
    ap.add_argument("--height", type=float)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--iters", type=int, default=10000)
    ap.add_argument("--seed0", type=int, default=1)
    a = ap.parse_args()
    import yaml

    from hwflow.layout.place import placer
    from hwflow.core.design import Design
    from hwflow.core.libs import Libs
    tmp = Path(tempfile.mkdtemp(prefix="legal_"))
    proj = tmp / a.project.name
    shutil.copytree(a.project, proj, ignore=shutil.ignore_patterns("build", "fw"))
    raw = yaml.safe_load((proj / "design.yaml").read_text())
    b = raw.setdefault("board", {})
    b["optimize_area"] = False
    if a.width:
        b["width_mm"], b["height_mm"] = a.width, a.height
    elif a.scale != 1.0:
        b["width_mm"], b["height_mm"] = round(b["width_mm"] * a.scale, 1), round(b["height_mm"] * a.scale, 1)
    (proj / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    d = Design.load(proj)
    libs = Libs(design=d)
    t = time.time()
    parts, cost, viol = placer.place(d, libs, seeds=a.seeds, iters=a.iters, seed0=a.seed0)
    W, H = d.board["width_mm"], d.board["height_mm"]
    yard = sum((p.w - 2 * placer.COURTYARD_MARGIN) * (p.h - 2 * placer.COURTYARD_MARGIN) for p in parts.values())
    kinds = collections.Counter(v.split(" ")[0] if v.startswith(("courtyard", "isolation", "corridor")) else
                                " ".join(v.split(" ")[1:4]) for v in viol)
    m = float(d.board["margin_mm"])
    boxes = sum((p.w * p.h) for p in parts.values())  # placer courtyards with their margin
    row = {"design": d.name, "board": [W, H], "layers": d.board.get("layers"), "parts": len(parts),
           "fill": round(yard / (W * H), 3), "usable_fill": round(boxes / ((W - 2 * m) * (H - 2 * m)), 3),
           "settle": __import__("os").environ.get("HWFLOW_SETTLE", "1"), "violations": len(viol), "kinds": dict(kinds), "first": viol[:6],
           "seconds": round(time.time() - t, 1), "seeds": a.seeds, "iters": a.iters}
    print(json.dumps(row))
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
