#!/usr/bin/env python
"""Real-board layout benchmark: the PCBWorld D3-A boards (PCBench corpus) as hwflow designs, for the router and the placer.

PCBWorld (LG AI Research, arXiv 2607.05915, BSD-3) scores routers on real open-source KiCad boards taken from PCBench
(MIT corpus; every board keeps its own license, recorded per board in the manifest). The board files are never copied
into this repository: `prep` clones both repositories at pinned commits under the work dir and rebuilds the set there.

    uv run python scripts/pcbench.py prep [--work build/bench/pcbench] [--limit N] [--workers 12]
    uv run python scripts/layout_bench.py --pcbench-only [--pcbench-mode route|place|both]   # (layout_bench.py)

`prep`:
  1. PCBWorld's own chain, steps 0 and 1 (tools/datagen/pcbench_prep): KiCad 5 -> current board format, then the DRC filter
     that keeps the boards whose original routing passes KiCad DRC under their own rules, so every board is routable. It
     needs kicad-cli and KiCad's pcbnew module in the system interpreter (PCBNEW_PYTHON, default /usr/bin/python3), in a
     child process; PCBWorld pins KiCad 9.0.8, so with KiCad 10 the kept set can differ by a board or two. PCBWorld's
     step 2 (per-net guide widths) parses KiCad 9 net ids and is not run: the importer takes the board's net classes.
  2. hwflow.layout.board.kicad_import.import_board per kept board (the importer of the oshw corpus cases): every part
     fixed at its own placement, net classes as net widths and clearances, the outline as floorplan cut regions, and
     `free` in cutout.yaml (the parts a placer may move: everything but connectors, mechanical parts, switches and
     the like). The board's own copper (kicad_import.human_copper) is the human reference in pcbench.json.

The two benchmark modes (layout_bench.py):
  route  every part fixed at the human placement (HWFLOW_ROUTE_ILLEGAL=1, as the oshw cases: a human placement may
         break the placer's legality rules); comparable to PCBWorld's published router rows (REFERENCE).
  place  the `free` parts released (no x, y, rot), the rest fixed: the whole flow places and routes, and the result is
         compared with the human board (HPWL of the human placement, the human copper length and vias).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # this tree's hwflow

PCBENCH = ("https://github.com/PCBench/PCBench.git", "dec3be75")  # the commit PCBWorld's prepare_pcbench.sh pins
PCBWORLD = ("https://github.com/LGAI-Research/PCBWorld.git", "v1.1.1")
# PCBWorld RQ2 table, d3a fair-95 (methods/baselines/rule_based/eval/RQ2_TABLE.md at v1.1.1): routability is the
# full-set mean, DRV/WL/vias are means over the fully routed boards, time is router seconds per board.
REFERENCE = {"Freerouting": {"routability": 0.98, "drv": 0.09, "wl_mm": 158.82, "vias": 0.72, "time_s": 3.90},
             "KiCadRoutingTools": {"routability": 0.94, "drv": 1.01, "wl_mm": 156.01, "vias": 3.74, "time_s": 0.65}}


def sh(cmd, **kw):
    print("+ " + " ".join(map(str, cmd)), flush=True)
    return subprocess.run(cmd, **kw)


def clone(url: str, rev: str, dst: Path):
    if (dst / ".git").exists() or dst.is_symlink():
        return
    sh(["git", "clone", "--filter=blob:none", "--no-checkout", url, str(dst)], check=True)
    sh(["git", "-C", str(dst), "checkout", "-q", rev], check=True)


def board_hpwl(b: dict) -> float:
    """Half-perimeter wirelength over the pad centres of every net with two or more pads (pcb.read_board dict); the
    same measure as layout_bench.py's hpwl_mm."""
    nets: dict[str, list] = {}
    for fp in b["footprints"].values():
        for p in fp["pads"]:
            if p["net"]:
                nets.setdefault(p["net"], []).append((p["x"], p["y"]))
    return round(sum((max(x for x, _ in v) - min(x for x, _ in v)) + (max(y for _, y in v) - min(y for _, y in v))
                     for v in nets.values() if len(v) > 1), 1)


def hpwl(design_dir: Path) -> float:
    """HPWL of the design's own (human) placement, on the unrouted board hwflow writes for it."""
    import tempfile

    from hwflow.core.design import Design
    from hwflow.core.libs import Libs
    from hwflow.layout.board import pcb
    from hwflow.layout.place import placer
    d = Design.load(design_dir)
    libs = Libs(design=d)
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / "b.kicad_pcb"
        pcb.write_board(f, pcb.build_board(d, libs, placer.build_parts(d, libs)))
        return board_hpwl(pcb.read_board(f))


def import_one(board: Path, out: Path, bid: str, meta: dict | None) -> dict:
    from hwflow.layout.board import kicad_import
    lics = (meta or {}).get("licenses") or []
    lic = ", ".join(x.get("spdx_id", "?") for x in (lics if isinstance(lics, list) else [lics])) or "unknown"
    source = {"url": (meta or {}).get("source"), "license": lic, "corpus": f"PCBench {PCBENCH[1]} via PCBWorld {PCBWORLD[1]} D3-A",
              "id": bid}
    shutil.rmtree(out, ignore_errors=True)
    try:
        rec = kicad_import.import_board(board, out, name=re.sub(r"^\d{4}_", "", bid)[:48], pro=board.with_suffix(".kicad_pro"),
                                        source=source, lib="pcbench")
    except kicad_import.UnsupportedBoard as e:
        shutil.rmtree(out, ignore_errors=True)
        return {"id": bid, "ok": False, "reason": str(e).split(": ", 1)[-1]}
    cop = kicad_import.human_copper(board, rec["offset"], rec["net_renames"])
    human = kicad_import.copper_metrics(cop)
    try:
        human["hpwl_mm"] = hpwl(out)
    except Exception as e:  # noqa: BLE001
        human["hpwl_error"] = f"{type(e).__name__}: {str(e)[:120]}"
    pc = {"id": bid, "license": lic, "source": source["url"], "free": rec["free"], "outline_fill": rec["outline_fill"],
          "not_imported": rec["not_imported"], "warnings": rec["warnings"], "human": human}
    (out / "pcbench.json").write_text(json.dumps(pc, indent=1))
    return {"ok": True, **pc}


def _import_job(a):
    try:
        return import_one(*a)
    except Exception as e:  # noqa: BLE001
        return {"id": a[2], "ok": False, "reason": f"import: {type(e).__name__}: {str(e)[:200]}"}


def prep(work: Path, limit: int, workers: int):
    ext = work / "ext"
    ext.mkdir(parents=True, exist_ok=True)
    clone(*PCBENCH, ext / "PCBench")
    clone(*PCBWORLD, ext / "PCBWorld")
    ids = json.loads((ext / "PCBWorld/configs/datasets/d3.json").read_text())["easy"]["test"]
    ids = ids[:limit] if limit else ids
    src = work / "src"
    shutil.rmtree(src, ignore_errors=True)
    src.mkdir()
    for i in ids:  # D3 ids are <NNNN>_<PCBench folder>
        (src / i.split("_", 1)[1]).symlink_to((ext / "PCBench/PCBs" / i.split("_", 1)[1]).resolve())
    pp = ext / "PCBWorld/tools/datagen/pcbench_prep"
    py = os.environ.get("PCBNEW_PYTHON", "/usr/bin/python3")
    env = {**os.environ, "PYTHONPATH": str(ext / "PCBWorld"), "KICAD_CLI": shutil.which("kicad-cli") or "kicad-cli",
           "PCBNEW_PYTHON": py, "PCBENCH_PCBS_ROOT": str(src), "PCBENCH_V9_ROOT": str(work / "v9"),
           "PCBENCH_NEWDRC_OUT": str(work / "newdrc")}
    sh([py, str(pp / "convert_v9.py"), "--workers", str(workers)], env=env, cwd=ext / "PCBWorld", check=True)
    sh([py, str(pp / "drc_fix_v9.py"), "--workers", str(workers)], env=env, cwd=ext / "PCBWorld")  # exit 1: some boards fail
    jobs, rows = [], []
    shutil.rmtree(work / "designs", ignore_errors=True)
    for i in ids:
        folder = work / "newdrc" / i.split("_", 1)[1]
        board = folder / "processed_v9.kicad_pcb"
        if not board.exists():
            rows.append({"id": i, "ok": False, "reason": "fails KiCad DRC with its own routing (PCBWorld step 1)"})
            continue
        meta = json.loads((folder / "metadata.json").read_text()) if (folder / "metadata.json").exists() else None
        jobs.append((board, work / "designs" / re.sub(r"[^A-Za-z0-9_.()-]", "_", i), i, meta))
    with ProcessPoolExecutor(workers) as ex:
        rows += list(ex.map(_import_job, jobs))
    rows.sort(key=lambda r: r["id"])
    man = {"pcbench": PCBENCH, "pcbworld": PCBWORLD,
           "kicad": subprocess.run(["kicad-cli", "version"], capture_output=True, text=True).stdout.strip(),
           "boards": rows, "imported": sum(r["ok"] for r in rows), "of": len(ids)}
    (work / "manifest.json").write_text(json.dumps(man, indent=1))
    for r in rows:
        if not r["ok"]:
            print(f"  skip {r['id']}: {r['reason']}")
    print(f"imported {man['imported']} of {len(ids)} D3-A boards into {work / 'designs'}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prep")
    p.add_argument("--work", default=str(ROOT / "build/bench/pcbench"))
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a = ap.parse_args()
    prep(Path(a.work).resolve(), a.limit, a.workers)


if __name__ == "__main__":
    sys.exit(main() or 0)
