#!/usr/bin/env python
"""Calibration harness of the routability predictor (src/hwflow/routability.py, docs/layout-flow.md).

A case is a design (a project directory) with optional board overrides (size, layer count) and a placement seed:
synthetic stress variants of the same netlist (smaller board, fewer layers) give routable and unroutable cases.
Each case lives in <work>/<case>/:

    make   copy the project with the overrides applied (<case>/proj), place it once (placement.json)
    truth  route the fixed placement with the current routing flow (scripts/route_bench.py `base`: route, repair,
           prune, DRC) and keep the outcome: truth.json (unrouted, DRC errors, open nets, vias, track, times)
    pred   the predictor on the same placement: pred.json (seconds, no routing)
    table  one row per case: prediction against outcome

    uv run python scripts/routab_bench.py make|truth|pred|table --work DIR [case ...]

Cases are listed in CASES below (name -> source project, overrides, placement seed); `--import NAME=DIR` reads an
earlier scripts/route_bench.py work directory (placement.json + base/) as a case without routing again.

Dataset (written by `pred`, kept under the work directory, never in git), per case:

    dataset.npz   (np.savez_compressed) res_mm, pos_mm: raster and window-grid cell sides (mm); layers [L] copper
                  layer names; nets [N] net names (net id k is nets[k - 1]); pad [L, nx, ny] int32 net id of the pad
                  copper (0 free, -1 copper of no net); hard [L, nx, ny] bool outline outside, track keep-outs, holes;
                  edt [L, nx, ny] float16 distance (mm) to the nearest obstacle; ratio [gx, gy] float32 the worst
                  window-cut ratio per POS_MM cell. Index [ix, iy], cell centre ((i + 0.5) * res, (j + 0.5) * res).
    labels.json   features: the cheap features of the prediction (board, layers, hard nets and share, verdict,
                  max ratio, hot area, access pads, proofs, corridor_min, line_ratio, ring_ratio, per_net {ratio,
                  exits, corridor, gr}, seconds); placement_violations: the placer's violations of the placement;
                  outcome (with truth.json): unrouted, drc_errors, vias, track_mm, route_s, repair_s, layout_s,
                  wall_s, open_nets {net: count}, drc_types {type: count}, open_at / drc_at [{net | type, at: [x, y]}]
                  (KiCad board coordinates, mm, of the first item of each unconnected pair / DRC error).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(os.environ.get("HWFLOW_SRC") or ROOT / "src")
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "scripts"))

MAIN = Path(os.environ.get("HWFLOW_BENCH_DESIGNS") or ROOT)  # where the source projects are

# name: (project, {board overrides}, scale of width and height, placer seed0)
CASES = {
    "sp_18x13": ("examples/sensor_preamp", {"width_mm": 18, "height_mm": 13, "optimize_area": False}, 1.0, 1),
    "sp_13x10": ("examples/sensor_preamp", {"width_mm": 13, "height_mm": 10, "optimize_area": False}, 1.0, 1),
    "sp_11x8": ("examples/sensor_preamp", {"width_mm": 11, "height_mm": 8, "optimize_area": False}, 1.0, 1),
    "s24": ("synth24", {}, 1.0, 1),
    "s24_x07": ("synth24", {}, 0.7, 1),
    "s24_x06_4L": ("synth24", {"layers": 4}, 0.6, 1),
    "s60_4L": ("evals/bench/synth60", {"layers": 4}, 1.0, 1),
    "s60_x13": ("evals/bench/synth60", {}, 1.3, 1),
    "s60_x085": ("evals/bench/synth60", {}, 0.85, 1),
    "s60_x085_4L": ("evals/bench/synth60", {"layers": 4}, 0.85, 1),
    "ht4_2L": ("projects/home-thermostat-4", {"layers": 2}, 1.0, 1),
    "ht1": ("projects/home-thermostat", {}, 1.0, 1),
    "ht1_2L": ("projects/home-thermostat", {"layers": 2}, 1.0, 1),
    "ht3": ("projects/home-thermostat-3", {}, 1.0, 1),
    "s150": ("evals/bench/synth150", {}, 1.0, 1),
    "s150_4L": ("evals/bench/synth150", {"layers": 4}, 1.0, 1),
}
PLACE_SEEDS, PLACE_ITERS = 1, 8000  # as scripts/route_bench.py


def _yaml():
    import yaml
    return yaml


def _source(src: str, work: Path) -> Path:
    if src == "synth24":  # generated: make_synth_board.py --profile synth60 --parts 24 --seed 11
        dst = work / "_synth24"
        if not (dst / "design.yaml").exists():
            import subprocess
            subprocess.run([sys.executable, str(ROOT / "scripts" / "make_synth_board.py"), "--profile", "synth60",
                            "--parts", "24", "--seed", "11", "--out", str(dst)], check=True,
                           env=dict(os.environ, PYTHONPATH=str(SRC)))
        return dst
    p = Path(src)
    return p if p.is_absolute() else (ROOT / src if (ROOT / src / "design.yaml").exists() else MAIN / src)


def make(case: str, work: Path):
    import route_bench as rb
    src, over, scale, seed0 = CASES[case]
    cdir = work / case
    proj = cdir / "proj"
    cdir.mkdir(parents=True, exist_ok=True)
    s = _source(src, work)
    if proj.exists():
        shutil.rmtree(proj)
    shutil.copytree(s, proj, ignore=lambda d, names: [n for n in names if Path(d) == s and n in rb.SKIP])
    y = _yaml()
    f = proj / "design.yaml"
    raw = y.safe_load(f.read_text())
    b = raw.setdefault("board", {})
    b.update(over)
    if scale != 1.0:
        b["width_mm"] = round(float(b["width_mm"]) * scale, 1)
        b["height_mm"] = round(float(b["height_mm"]) * scale, 1)
    f.write_text(y.safe_dump(raw, sort_keys=False, allow_unicode=True, width=160))
    rb.placement(proj, cdir, None, PLACE_SEEDS, PLACE_ITERS, seed0)
    print(f"{case}: board {b.get('width_mm')} x {b.get('height_mm')} mm, {b.get('layers', 2)} layers")


def truth(case: str, work: Path):
    import route_bench as rb
    cdir = work / case
    t = time.time()
    row = rb.run(cdir / "proj", cdir, cdir / "placement.json", "base")
    row["wall_s"] = round(time.time() - t, 1)
    row.update(_outcome(cdir / "base"))
    (cdir / "truth.json").write_text(json.dumps(row, indent=1))


def _outcome(pdir: Path) -> dict:
    """Open nets and DRC error types of the routed board in <pdir>/build (KiCad DRC json)."""
    import collections
    import re
    out = {}
    js = [p for p in (pdir / "build").glob("*.drc.json") if "_krt" not in p.name and "unrouted" not in p.name]
    if not js:
        return out
    d = json.loads(js[0].read_text())
    nets = collections.Counter()
    for x in d.get("unconnected_items", []):
        m = re.search(r"\[([^\]]+)\]", x["items"][0]["description"])
        nets[m.group(1) if m else "?"] += 1
    out["open_nets"] = dict(nets)
    out["drc_types"] = dict(collections.Counter(v["type"] for v in d.get("violations", []) if v.get("severity") == "error"))

    def at(x):
        p = (x.get("items") or [{}])[0].get("pos") or {}
        return [round(float(p.get("x", 0)), 3), round(float(p.get("y", 0)), 3)]
    out["open_at"] = [{"net": (re.search(r"\[([^\]]+)\]", x["items"][0]["description"]) or [None, "?"])[1], "at": at(x)}
                      for x in d.get("unconnected_items", [])]
    out["drc_at"] = [{"type": v["type"], "at": at(v)} for v in d.get("violations", []) if v.get("severity") == "error"]
    return out


def import_case(name: str, wdir: Path, work: Path):
    """An earlier route_bench work directory (placement.json, base/, results.jsonl) as case `name`."""
    cdir = work / name
    cdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(wdir / "placement.json", cdir / "placement.json")
    if not (cdir / "proj").exists():
        shutil.copytree(wdir / "base", cdir / "proj", ignore=shutil.ignore_patterns("build"))
    rows = [json.loads(x) for x in (wdir / "results.jsonl").read_text().splitlines() if '"base"' in x]
    row = dict(rows[0], **_outcome(wdir / "base"), imported=str(wdir))
    (cdir / "truth.json").write_text(json.dumps(row, indent=1))
    lib = cdir / "proj" / "build"
    lib.mkdir(exist_ok=True)
    for f in ("fp-lib-table", "sym-lib-table"):
        if (wdir / "base" / "build" / f).exists():
            shutil.copy(wdir / "base" / "build" / f, lib / f)


def load(cdir: Path):
    """(design, libs, parts) of a case at its saved placement."""
    from hwflow.layout.place import placer
    from hwflow.core.design import Design
    from hwflow.core.libs import Libs
    d = Design.load(cdir / "proj")
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    saved = json.loads((cdir / "placement.json").read_text())
    for r, p in parts.items():
        if r in saved and not p.fixed:
            p.x, p.y, p.r = saved[r]
    return d, libs, parts


def pred(case: str, work: Path):
    from hwflow.layout.place import routability
    cdir = work / case
    d, libs, parts = load(cdir)
    t = time.time()
    res = routability.predict(d, libs, parts=parts)
    res["seconds"] = round(time.time() - t, 2)
    (cdir / "pred.json").write_text(json.dumps(routability.public(res), indent=1, default=str))
    routability.dataset(res, cdir / "dataset.npz")
    labels(cdir, res, parts, d)
    print(f"{case}: {routability.summary(res)} ({res['seconds']} s)")


def labels(cdir: Path, res: dict, parts: dict, d) -> None:
    """<case>/labels.json next to dataset.npz (format in the module text): the cheap features of the prediction and
    the outcome of the routed board, when truth.json exists."""
    from hwflow.layout.place import placer
    pl = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"])
    feats = {k: res[k] for k in ("board", "layers", "res_mm", "routed_nets", "hard_nets", "hard_share", "verdict",
                                 "max_ratio", "hot_area_mm2", "n_hot_spots", "seconds")}
    feats.update(access_pads=len(res["access"]), proofs=len(res.get("proofs") or []),
                 corridor_min=min([c["margin"] for c in res["corridors"]] or [None], key=lambda v: 99 if v is None else v),
                 line_ratio=max((b or {}).get("ratio", 0) for b in (res["bounds"]["line_x"], res["bounds"]["line_y"])),
                 ring_ratio=max([r["ratio"] for r in res["bounds"]["rings"]] or [0]),
                 per_net=res["per_net"])
    out = {"case": cdir.name, "features": feats, "placement_violations": pl.violations()}
    tp = cdir / "truth.json"
    if tp.exists():
        t = json.loads(tp.read_text())
        t.update(_outcome(cdir / "base") if (cdir / "base").exists() else {})
        out["outcome"] = {k: t.get(k) for k in ("unrouted", "drc_errors", "vias", "track_mm", "route_s", "repair_s",
                                                "layout_s", "wall_s", "open_nets", "drc_types", "open_at", "drc_at")}
    (cdir / "labels.json").write_text(json.dumps(out, indent=1, default=str))


def table(work: Path, cases):
    rows = []
    for c in cases:
        cdir = work / c
        if not (cdir / "truth.json").exists() or not (cdir / "pred.json").exists():
            continue
        t = json.loads((cdir / "truth.json").read_text())
        p = json.loads((cdir / "pred.json").read_text())
        sig = sum(v for n, v in (t.get("open_nets") or {}).items())
        rows.append((c, p, t, sig))
    print("| case | board | verdict | score | hard nets | unrouted | DRC errors | route s |")
    print("|---|---|---|---|---|---|---|---|")
    for c, p, t, sig in rows:
        print(f"| {c} | {p.get('board')} | {p.get('verdict')} | {p.get('score')} | {p.get('hard_nets')} | "
              f"{t['unrouted']} | {t['drc_errors']} | {t.get('route_s')} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "truth", "pred", "table", "import"])
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--import", dest="imp", action="append", default=[], help="NAME=route_bench work dir")
    a = ap.parse_intermixed_args()
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    if a.cmd == "import":
        for x in a.imp:
            n, w = x.split("=", 1)
            import_case(n, Path(w), work)
        return
    cases = a.cases or sorted(p.name for p in work.iterdir() if (p / "placement.json").exists())
    for c in cases:
        {"make": make, "truth": truth, "pred": pred}.get(a.cmd, lambda *_: None)(c, work)
    if a.cmd == "table":
        table(work, cases)


if __name__ == "__main__":
    main()
