#!/usr/bin/env python
"""Layout benchmark: runs the `layout --auto` flow on a fixed set of designs, 3 seeds each, and records quality and time.

Set: every design in examples/, the 2026-10-19 thermostat baseline project, and the synthetic board
evals/bench/synth150 (scripts/make_synth_board.py). The eval fixtures (evals/fixtures/*.yaml) are product briefs and
personas, not buildable designs, so they are not in the set. Every run builds in a fresh copy under the work dir
(default build/bench/work/), never inside evals/, examples/ or projects/.

Per run the flow is `build --until layout --auto` without SPICE: check and sch run once per design copy (layout needs
the library tables from sch, not simulation results), then `cli.layout_auto(attempts=4, workers=1)` runs in a child
process (timeout per run) with timing wrappers on placer.place, pcb.run_krt / pcb.krt_route, repair.repair and
kicad.drc / kicad.drc_fast. A wrapper records only the outermost call, so the router and the fast DRC inside repair
count as repair. Seeds: run i adds 0, 100, 200 to the placer's seed0 (attempt k then uses seed0 + 4k as usual).

Headline: DRC-clean rate before repair = fraction of runs whose FIRST attempt had 0 DRC errors and 0 unrouted nets
before repair; mean --auto attempts per design. `before_kept` is the same count for the attempt that was kept.

    uv run python scripts/layout_bench.py [--only NAME] [--runs 3] [--workdir DIR] [--timeout 1200]
                                           [--compare build/bench/old.json] [--out FILE]
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # always benchmark this tree's hwflow

OFFSETS = [0, 100, 200]
PHASES = ("placer", "router", "repair", "drc")


# Per-entry settings: placer seeds/iters (defaults of `layout --auto`: 4 x 30000) and a cap on the runs. The placer costs
# O(parts^2) per iteration (about 32 ms at 150 parts), so the full synthetic board needs about an hour of placer per
# attempt and times out at the default 1200 s; it runs once as a timeout record. `synth150-lite` is the same board with
# 1 seed x 2000 iterations, so that the router, repair, DRC and extraction of a 150 part board are measured.
DEFAULTS = {"seeds": 4, "iters": 30000, "max_runs": None, "env": {}}
SPECIAL = {"synth150": {"max_runs": 1}, "synth150-lite": {"seeds": 1, "iters": 2000, "max_runs": 1},
           "thermostat-strict": {"max_runs": 1, "env": {"HWFLOW_KEEPOUT_STRICT": "1"}}}  # L14: keep-out check without fp-lib-table


def design_set() -> dict[str, Path]:
    s: dict[str, Path] = {}
    for p in sorted((ROOT / "examples").glob("*/design.yaml")):
        s[p.parent.name] = p.parent
    base = ROOT / "evals/baselines/home-thermostat-2026-10-19/project"
    if (base / "design.yaml").exists():
        s["thermostat"] = s["thermostat-strict"] = base
    if (ROOT / "evals/bench/synth150/design.yaml").exists():
        s["synth150-lite"] = s["synth150"] = ROOT / "evals/bench/synth150"
    return s


# ---------------------------------------------------------------- child: one design, one seed
def child(rundir: Path, offset: int, result: Path, seeds: int, iters: int):
    from hwflow import cli, kicad, pcb, placer, postlayout, repair
    from hwflow.design import Design
    from hwflow.libs import Libs

    events = (rundir / "bench_events.jsonl").open("a")
    totals = {k: 0.0 for k in PHASES}
    calls = {k: 0 for k in PHASES}
    stack: list[str] = []
    drc_log: dict[str, list] = {}  # board dir -> list of DRC violation lists (outermost calls only)

    def wrap(mod, name, phase, post=None):
        if not hasattr(mod, name):
            return
        f = getattr(mod, name)

        @functools.wraps(f)
        def w(*a, **k):
            if stack:  # nested in another phase (router inside repair, fast DRC inside repair)
                return f(*a, **k)
            stack.append(phase)
            t = time.time()
            events.write(json.dumps({"start": phase, "t": t}) + "\n"); events.flush()
            try:
                res = f(*a, **k)
                if post:
                    post(a, k, res)
                return res
            finally:
                dt = time.time() - t
                stack.pop()
                totals[phase] += dt
                calls[phase] += 1
                events.write(json.dumps({"end": phase, "dt": round(dt, 2)}) + "\n"); events.flush()
        setattr(mod, name, w)

    def seed_shift(f):
        @functools.wraps(f)
        def w(*a, **k):
            a = list(a)
            if "seed0" in k:
                k["seed0"] += offset
            elif len(a) >= 6:
                a[5] += offset
            else:
                k["seed0"] = 1 + offset
            return f(*a, **k)
        return w

    def log_drc(a, k, res):
        board = a[0] if a else k.get("pcb")
        drc_log.setdefault(str(Path(board).parent), []).append(res)

    placer.place = seed_shift(placer.place)
    wrap(placer, "place", "placer")
    wrap(pcb, "run_krt", "router")
    wrap(pcb, "krt_route", "router")
    wrap(repair, "repair", "repair")
    wrap(kicad, "drc_fast", "drc")
    wrap(kicad, "drc", "drc", log_drc)

    d = Design.load(rundir)
    libs = Libs(design=d)
    out = rundir / "build"
    t0 = time.time()
    try:
        r = cli.layout_auto(d, libs, attempts=4, seeds=seeds, iters=iters, workers=1)
        err = None
    except Exception as e:  # noqa: BLE001
        r, err = None, f"{type(e).__name__}: {str(e)[:300]}"
    wall = time.time() - t0
    res: dict = {"offset": offset, "wall_s": round(wall, 1), "error": err,
                 "phase_s": {k: round(v, 1) for k, v in totals.items()},
                 "phase_calls": calls}
    res["phase_s"]["other"] = round(wall - sum(totals.values()), 1)

    def counts(drc):
        errs = [x for x in drc if x["sev"] == "error"]
        return {"errors": len(errs), "unrouted": sum(x["type"] == "unconnected" for x in errs),
                "by_type": dict(Counter(x["type"] for x in errs)), "warnings": len(drc) - len(errs)}

    if r is not None:
        a = r.get("auto") or {}
        res["ok"] = bool(r["ok"])
        res["attempts"] = len(a.get("attempts", []))
        res["kept"] = a.get("kept")
        res["stopped"] = a.get("stopped")
        res["board_mm"] = r["board_mm"]
        res["vias"] = r["vias"]
        res["placement_violations"] = len(r["placement_violations"])
        res["placement_cost"] = r["placement_cost"]
        rep = r.get("repair")
        res["repair_rounds"] = rep["rounds"] if rep else 0
        res["repair_text"] = rep.get("text") if rep else None
        aut = out / "auto"
        first, last = {}, {}
        for path, lst in drc_log.items():
            first[path], last[path] = counts(lst[0]), counts(lst[-1])
        keys = sorted(first, key=lambda p: Path(p).name)

        def pick(n):
            m = [p for p in keys if Path(p).name == f"a{n}"]
            return first[m[0]] if m else None
        res["before_a1"] = pick(1)
        res["before_kept"] = pick(res["kept"]) or (res["before_a1"] if res["kept"] == 1 else None)
        # after: error count from the stage result (it includes checks that are not in the .drc.json, e.g. the strict
        # keep-out check); by type from the stage result's DRC list when it is complete, else from the full DRC json
        res["after"] = {"errors": r["drc_errors"], "unrouted": r["unrouted"], "warnings": r["drc_warnings"],
                        "by_type": dict(Counter(x["type"] for x in r["drc"] if x["sev"] == "error"))}
        if r["drc_errors"] + r["drc_warnings"] > len(r["drc"]):
            try:
                res["after"]["by_type"] = counts(kicad._violations(json.loads((out / f"{d.name}.drc.json").read_text())))["by_type"]
            except Exception:  # noqa: BLE001
                pass
        res["attempt_lines"] = a.get("attempts", [])
        # board metrics from the final board
        board = out / f"{d.name}.kicad_pcb"
        try:
            b = pcb.read_board(board)
            res["track_mm"] = round(sum(((s["x2"] - s["x1"]) ** 2 + (s["y2"] - s["y1"]) ** 2) ** 0.5 for s in b["segments"]), 1)
            res["segments"] = len(b["segments"])
            res["vias_board"] = len(b["vias"])
            nets: dict[str, list] = {}
            for fp in b["footprints"].values():
                for p in fp["pads"]:
                    if p["net"]:
                        nets.setdefault(p["net"], []).append((p["x"], p["y"]))
            res["hpwl_mm"] = round(sum((max(x for x, _ in v) - min(x for x, _ in v)) + (max(y for _, y in v) - min(y for _, y in v))
                                       for v in nets.values() if len(v) > 1), 1)
        except Exception as e:  # noqa: BLE001
            res["board_error"] = f"{type(e).__name__}: {e}"
        # post-layout checks, in-process, no SPICE
        try:
            t = time.time()
            ex = postlayout.Extraction(d, board)
            chk = postlayout.layout_checks(d, ex)
            res["postlayout_s"] = round(time.time() - t, 1)
            res["postlayout"] = dict(Counter(f"{x['sev']}:{x['code']}" for x in chk))
            res["postlayout_warnings"] = sum(1 for x in chk if x["sev"] != "info")
        except Exception as e:  # noqa: BLE001
            res["postlayout_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    result.write_text(json.dumps(res, indent=1))


# ---------------------------------------------------------------- parent
def prep(src: Path, work: Path, name: str) -> Path:
    """Fresh copy of a design in the work dir with check and sch run (library tables, project files)."""
    from hwflow import cli
    from hwflow.design import Design
    from hwflow.libs import Libs
    dst = work / name / "prep"
    shutil.rmtree(dst.parent, ignore_errors=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("build", "__pycache__", ".venv"))
    d = Design.load(dst)
    libs = Libs(design=d)
    c = cli.stage_check(d, libs)
    s = cli.stage_sch(d, libs)
    print(f"  prep {name}: check {'OK' if c['ok'] else 'FAIL'} ({c['errors']} errors), sch {'OK' if s['ok'] else 'FAIL'} ({s['errors']} ERC errors, ignored)", flush=True)
    return dst


def run_one(prepdir: Path, name: str, i: int, timeout: int, work: Path, seeds: int, iters: int, env: dict | None = None) -> dict:
    rd = work / name / f"run{i + 1}"
    shutil.rmtree(rd, ignore_errors=True)
    shutil.copytree(prepdir, rd)
    res_file = rd / "bench_result.json"
    log = (rd / "bench_child.log").open("w")
    t0 = time.time()
    p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child", str(rd), "--offset", str(OFFSETS[i % 3] + 300 * (i // 3)),
                          "--result", str(res_file), "--seeds", str(seeds), "--iters", str(iters)], stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=ROOT,
                         env={**os.environ, **(env or {})})
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        ev, tot, cur = [], {k: 0.0 for k in PHASES}, None
        try:
            for ln in (rd / "bench_events.jsonl").read_text().splitlines():
                e = json.loads(ln)
                if "start" in e:
                    cur = e["start"]
                else:
                    tot[e["end"]] += e["dt"]
                    cur = None
        except Exception:  # noqa: BLE001
            pass
        return {"offset": OFFSETS[i % 3], "timeout": True, "wall_s": round(time.time() - t0, 1), "ok": False,
                "phase_s": {k: round(v, 1) for k, v in tot.items()}, "running_phase_at_timeout": cur}
    if not res_file.exists():
        return {"offset": OFFSETS[i % 3], "error": "child crashed: " + (rd / "bench_child.log").read_text()[-400:], "ok": False,
                "wall_s": round(time.time() - t0, 1)}
    return json.loads(res_file.read_text())


def mean(v):
    v = [x for x in v if x is not None]
    return round(statistics.mean(v), 2) if v else None


def median(v):
    v = [x for x in v if x is not None]
    return round(statistics.median(v), 2) if v else None


def clean(c):
    return None if c is None else int(c["errors"] == 0 and c["unrouted"] == 0)


def aggregate(runs: list[dict]) -> dict:
    g = lambda f: [f(r) for r in runs]  # noqa: E731
    a = {"runs": len(runs), "ok_rate": mean(g(lambda r: int(bool(r.get("ok"))))),
         "clean_before_repair_rate": mean(g(lambda r: clean(r.get("before_a1")))),
         "clean_before_repair_kept_rate": mean(g(lambda r: clean(r.get("before_kept")))),
         "timeouts": sum(1 for r in runs if r.get("timeout")), "errors": sum(1 for r in runs if r.get("error"))}
    for k, f in (("attempts", lambda r: r.get("attempts")), ("hpwl_mm", lambda r: r.get("hpwl_mm")),
                 ("vias", lambda r: r.get("vias")), ("track_mm", lambda r: r.get("track_mm")),
                 ("repair_rounds", lambda r: r.get("repair_rounds")), ("wall_s", lambda r: r.get("wall_s")),
                 ("errors_before_a1", lambda r: (r.get("before_a1") or {}).get("errors")),
                 ("unrouted_before_a1", lambda r: (r.get("before_a1") or {}).get("unrouted")),
                 ("errors_before_kept", lambda r: (r.get("before_kept") or {}).get("errors")),
                 ("drc_after", lambda r: (r.get("after") or {}).get("errors")),
                 ("postlayout_warnings", lambda r: r.get("postlayout_warnings"))):
        a[k + "_mean"], a[k + "_median"] = mean(g(f)), median(g(f))
    for ph in PHASES + ("other",):
        a[f"{ph}_s_mean"] = mean(g(lambda r, ph=ph: (r.get("phase_s") or {}).get(ph)))
        a[f"{ph}_s_median"] = median(g(lambda r, ph=ph: (r.get("phase_s") or {}).get(ph)))
    codes: Counter = Counter()
    types: Counter = Counter()
    for r in runs:
        codes.update(r.get("postlayout") or {})
        types.update((r.get("after") or {}).get("by_type") or {})
    n = max(len(runs), 1)
    a["postlayout_by_code_mean"] = {k: round(v / n, 2) for k, v in sorted(codes.items())}
    a["drc_after_by_type_mean"] = {k: round(v / n, 2) for k, v in sorted(types.items())}
    return a


def table(doc: dict) -> str:
    cols = ("design", "runs", "ok", "clean1", "att", "hpwl", "unr1", "err1", "rnds", "drc2", "vias", "trk", "pl_w", "t_plc", "t_rte", "t_rep", "t_drc", "t_tot")
    rows = [cols]
    f = lambda x, p=1: "-" if x is None else (f"{x:.{p}f}" if isinstance(x, float) else str(x))  # noqa: E731
    for name, dd in doc["designs"].items():
        a = dd["aggregate"]
        rows.append((name, str(a["runs"]), f(a["ok_rate"], 2), f(a["clean_before_repair_rate"], 2), f(a["attempts_mean"]),
                     f(a["hpwl_mm_mean"], 0), f(a["unrouted_before_a1_mean"]), f(a["errors_before_a1_mean"]), f(a["repair_rounds_mean"]),
                     f(a["drc_after_mean"]), f(a["vias_mean"], 0), f(a["track_mm_mean"], 0), f(a["postlayout_warnings_mean"]),
                     f(a["placer_s_mean"], 0), f(a["router_s_mean"], 0), f(a["repair_s_mean"], 0), f(a["drc_s_mean"], 0),
                     f(a["wall_s_mean"], 0)))
    w = [max(len(r[i]) for r in rows) for i in range(len(cols))]
    lines = ["  ".join(c.ljust(w[i]) if i == 0 else c.rjust(w[i]) for i, c in enumerate(r)) for r in rows]
    h = doc["aggregate"]
    lines.append("")
    lines.append("clean1 = fraction of runs whose first attempt had 0 DRC errors and 0 unrouted before repair; att = mean --auto attempts;")
    lines.append("unr1/err1 = unrouted nets / DRC errors of the first attempt before repair; rnds = repair rounds; drc2 = DRC errors after;")
    lines.append("pl_w = post-layout warnings; t_* = mean seconds in placer, router (outside repair), repair (incl. its router and DRC), DRC.")
    lines.append(f"HEADLINE: DRC-clean rate before repair {h['clean_before_repair_rate']} (kept attempt: {h['clean_before_repair_kept_rate']}), "
                 f"mean attempts per design {h['attempts_mean_of_designs']}, ok rate {h['ok_rate']}")
    return "\n".join(lines)


def compare(new: dict, old: dict) -> str:
    keys = ("clean_before_repair_rate", "attempts_mean", "ok_rate", "unrouted_before_a1_mean", "errors_before_a1_mean", "repair_rounds_mean",
            "drc_after_mean", "hpwl_mm_mean", "vias_mean", "track_mm_mean", "postlayout_warnings_mean", "placer_s_mean",
            "router_s_mean", "repair_s_mean", "drc_s_mean", "wall_s_mean")
    out = ["", "COMPARE (new vs old: old -> new (delta))"]
    for name, dd in new["designs"].items():
        o = (old.get("designs", {}).get(name) or {}).get("aggregate")
        if not o:
            continue
        parts = []
        for k in keys:
            x, y = o.get(k), dd["aggregate"].get(k)
            if x is None or y is None:
                continue
            parts.append(f"{k} {x} -> {y} ({y - x:+.2f})")
        out.append(f"{name}:\n    " + "\n    ".join(parts))
    for k in ("clean_before_repair_rate", "attempts_mean_of_designs", "ok_rate"):
        x, y = old.get("aggregate", {}).get(k), new["aggregate"].get(k)
        if x is not None and y is not None:
            out.append(f"ALL {k}: {x} -> {y} ({y - x:+.2f})")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", help="design name (repeatable)")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--workdir", default=str(ROOT / "build/bench/work"))
    ap.add_argument("--timeout", type=int, default=1200, help="seconds per design run")
    ap.add_argument("--compare", help="an earlier bench JSON to print deltas against")
    ap.add_argument("--out", help="output JSON (default build/bench/<date-HHMM>.json)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--full-synth", action="store_true", help="also run synth150 with the default placer settings (about an hour of placer per attempt: a timeout record)")
    ap.add_argument("--child"); ap.add_argument("--offset", type=int, default=0); ap.add_argument("--result"); ap.add_argument("--seeds", type=int, default=4); ap.add_argument("--iters", type=int, default=30000)
    a = ap.parse_args()
    if a.child:
        return child(Path(a.child), a.offset, Path(a.result), a.seeds, a.iters)
    ds = design_set()
    if a.list:
        print("\n".join(f"{k}: {v}" for k, v in ds.items()))
        return 0
    if not a.full_synth and not (a.only and "synth150" in a.only):
        ds.pop("synth150", None)
    if a.only:
        miss = [x for x in a.only if x not in ds]
        if miss:
            sys.exit(f"unknown design {miss}; available: {list(ds)}")
        ds = {k: v for k, v in ds.items() if k in a.only}
    work = Path(a.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    doc = {"date": datetime.now().isoformat(timespec="seconds"), "runs": a.runs, "offsets": OFFSETS, "timeout_s": a.timeout, "loadavg_start": os.getloadavg(), "cpus": os.cpu_count(),
           "git": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "designs": {}}
    t_all = time.time()
    for name, src in ds.items():
        print(f"[{name}]", flush=True)
        cfg = {**DEFAULTS, **SPECIAL.get(name, {})}
        pd = prep(src, work, name)
        runs = []
        n_runs = min(a.runs, cfg["max_runs"] or a.runs)
        for i in range(n_runs):
            t = time.time()
            r = run_one(pd, name, i, a.timeout, work, cfg["seeds"], cfg["iters"], cfg["env"])
            runs.append(r)
            b = r.get("before_a1") or {}
            print(f"  run {i + 1} (seed +{r.get('offset')}): {'TIMEOUT in ' + str(r.get('running_phase_at_timeout')) if r.get('timeout') else ('ERROR ' + str(r.get('error'))[:150] if r.get('error') else ('ok' if r.get('ok') else 'FAIL'))}"
                  f" attempts={r.get('attempts')} before(a1) err={b.get('errors')} unr={b.get('unrouted')} after={(r.get('after') or {}).get('errors')} "
                  f"rounds={r.get('repair_rounds')} {r.get('phase_s')} wall={r['wall_s']}s", flush=True)
            doc["designs"][name] = {"placer": {"seeds": cfg["seeds"], "iters": cfg["iters"]}, "env": cfg["env"], "runs": runs, "aggregate": aggregate(runs)}
        shutil.rmtree(work / name / "prep", ignore_errors=True)
    allr = [r for n, dd in doc["designs"].items() if n != "thermostat-strict" for r in dd["runs"]]
    agg = aggregate(allr)
    agg["attempts_mean_of_designs"] = mean([dd["aggregate"]["attempts_mean"] for n, dd in doc["designs"].items() if n != "thermostat-strict"])
    agg["total_wall_s"] = round(time.time() - t_all, 1)
    doc["aggregate"] = agg
    doc["loadavg_end"] = os.getloadavg()
    out = Path(a.out) if a.out else ROOT / "build/bench" / (datetime.now().strftime("%Y-%m-%d-%H%M") + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1))
    print("\n" + table(doc))
    if a.compare:
        print(compare(doc, json.loads(Path(a.compare).read_text())))
    print(f"\nwritten {out}")


if __name__ == "__main__":
    sys.exit(main() or 0)
