#!/usr/bin/env python
"""Layout benchmark: runs the `layout --auto` flow on a fixed set of designs, 3 seeds each, and records quality and time.

Set: every design in examples/, the 2026-10-19 thermostat baseline project, and the synthetic board
evals/bench/synth150 (scripts/make_synth_board.py). With --pcbench, also the real boards of PCBWorld's D3-A set that
scripts/pcbench.py prep imported into build/bench/pcbench/designs/, in two modes (--pcbench-mode route|place|both):
pcb:<id> keeps the human placement (the router and the repair; a summary against PCBWorld's published KRT and
Freerouting rows), pcbp:<id> releases the parts a placer may move (the whole flow; a summary against the human board). The eval fixtures (evals/fixtures/*.yaml) are product briefs and
personas, not buildable designs, so they are not in the set. Every run builds in a fresh copy under the work dir
(default build/bench/work/), never inside evals/, examples/ or projects/.

Per run the flow is `build --until layout --auto` without SPICE: check and sch run once per design copy (layout needs
the library tables from sch, not simulation results), then `pl_stages.layout_auto(attempts=4, workers=1)` runs in a child
process (timeout per run) with timing wrappers on placer.place, krt.run_krt / krt.krt_route, repair.repair and
kicad.drc / kicad.drc_fast. A wrapper records only the outermost call, so the router and the fast DRC inside repair
count as repair. Seeds: run i adds 0, 100, 200 to the placer's seed0 (attempt k then uses seed0 + 4k as usual).

Headline: DRC-clean rate before repair = fraction of runs whose FIRST attempt had 0 DRC errors and 0 unrouted nets
before repair; mean --auto attempts per design. `before_kept` is the same count for the attempt that was kept.

    uv run python scripts/layout_bench.py [--only NAME] [--runs 3] [--workdir DIR] [--timeout 1200]
                                           [--compare build/bench/old.json] [--out FILE] [--pcbench | --pcbench-only] [--pcbench-mode route|place|both]
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
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
DEFAULTS = {"seeds": 4, "iters": 30000, "max_runs": None, "env": {"HWFLOW_KEEPOUT_STRICT": "1"}}  # strict keep-out check set explicitly
SPECIAL = {"synth150": {"max_runs": 1}, "synth150-lite": {"seeds": 1, "iters": 2000, "max_runs": 1},
           "thermostat": {"env": {"HWFLOW_KEEPOUT_STRICT": "0"}},  # comparable to the first baseline
           "thermostat-strict": {"max_runs": 1}}
OPT_IN = {"synth150", "synth150-lite"}  # only with --full-synth / --only
# thermostat-unfixed: the baseline with the placement of every part released except these (connectors, keep-outs,
# panel slot, battery holder, mounting holes); the placer places the rest and keeps `edge:` hints.
KEEP_FIXED = ("J", "KO_", "SLOT", "BT1", "MH")


PCBENCH_DIR = ROOT / "build/bench/pcbench/designs"
# route mode, every part fixed: one placer seed with few iterations (it has nothing to move), one run (the seed offset
# only moves the placer); a human placement may break the placer's legality rules, so it is routed anyway (the oshw cases)
PCB_CFG = {"seeds": 1, "iters": 200, "max_runs": 1, "env": {"HWFLOW_KEEPOUT_STRICT": "1", "HWFLOW_ROUTE_ILLEGAL": "1"}}
# place mode: the default `layout --auto` placer, one run per board unless --runs asks for fewer (the set is the spread).
# An illegal placement is routed too: the human board's fixed parts often break the placer's rules (a pad within
# 0.3 mm of the edge, overlapping connector courtyards), and the child scores legality on the released parts only
PCBP_CFG = {"max_runs": 1, "env": {"HWFLOW_KEEPOUT_STRICT": "1", "HWFLOW_ROUTE_ILLEGAL": "1"}}
PCB_PREFIX = ("pcb:", "pcbp:")


def pcbench_set(mode: str = "route") -> dict[str, Path]:
    dirs = [p.parent for p in sorted(PCBENCH_DIR.glob("*/design.yaml"))]
    s = {f"pcb:{d.name}": d for d in dirs} if mode in ("route", "both") else {}
    if mode in ("place", "both"):
        s.update({f"pcbp:{d.name}": d for d in dirs})
    return s


def design_set() -> dict[str, Path]:
    s: dict[str, Path] = {}
    for p in sorted((ROOT / "examples").glob("*/design.yaml")):
        s[p.parent.name] = p.parent
    base = ROOT / "evals/baselines/home-thermostat-2026-10-19/project"
    if (base / "design.yaml").exists():
        s["thermostat"] = s["thermostat-strict"] = s["thermostat-unfixed"] = base
    if (ROOT / "evals/bench/synth150/design.yaml").exists():
        s["synth150-lite"] = s["synth150"] = ROOT / "evals/bench/synth150"
    if (ROOT / "evals/bench/synth60/design.yaml").exists():
        s["synth60"] = ROOT / "evals/bench/synth60"
    return s


# ---------------------------------------------------------------- child: one design, one seed
def child(rundir: Path, offset: int, result: Path, seeds: int, iters: int):
    from hwflow.layout.board import kicad, pcb
    from hwflow.layout.place import placer
    from hwflow.verify import postlayout
    from hwflow.layout.route import krt, regionroute, repair
    from hwflow.pipeline import stages as pl_stages
    from hwflow.core.design import Design
    from hwflow.core.libs import Libs

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
    wrap(krt, "run_krt", "router")  # the stage calls krt.run_krt / regionroute.route (pcb only re-exports the names)
    wrap(krt, "krt_route", "router")
    wrap(regionroute, "route", "router")
    wrap(repair, "repair", "repair")
    wrap(kicad, "drc_fast", "drc")
    wrap(kicad, "drc", "drc", log_drc)

    d = Design.load(rundir)
    libs = Libs(design=d)
    out = rundir / "build"
    t0 = time.time()
    try:
        r = pl_stages.layout_auto(d, libs, attempts=4, seeds=seeds, iters=iters, workers=1)
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
    if r is not None:
        need = sum(len(v) - 1 for v in d.nets().values() if len(v) > 1)
        unr = res["after"]["unrouted"]
        res["connections"] = need
        res["routability"] = round(1 - min(unr, need) / need, 4) if need else 1.0
        if r.get("blocked"):  # an illegal placement is not routed: nothing is connected
            res["blocked"] = r["blocked"]
            res["routability"] = 0.0
        # DRV as PCBWorld counts it, routing errors only: an error between fixed parts alone (courtyards, pad to pad)
        # comes with the original placement and is the same before routing (repair.fixed_error), so it is kept apart
        drc = r["drc"]
        if r["drc_errors"] + r["drc_warnings"] > len(drc):
            try:
                drc = kicad._violations(json.loads((out / f"{d.name}.drc.json").read_text()))
            except Exception:  # noqa: BLE001
                pass
        fixed = {ref: type("P", (), {"fixed": bool((c.get("placement") or {}).get("fixed"))})() for ref, c in d.components.items()}
        errs = [x for x in drc if x["sev"] == "error" and x["type"] != "unconnected"]
        res["fixed_errors"] = sum(1 for x in errs if repair.fixed_error(x, fixed) is not None)
        res["drv_after"] = len(errs) - res["fixed_errors"]
        # placement legality split by cause: a violation that names a released part is the placer's, one between
        # fixed parts only comes with the human placement
        viol = r.get("placement_violations") or []
        res["placement_violations"] = viol
        res["violations_placer"] = sum(1 for v in viol if any(not fixed[x].fixed for x in re.split(r"[\s/(),';]+", v) if x in fixed))
    result.write_text(json.dumps(res, indent=1))


# ---------------------------------------------------------------- parent
def prep(src: Path, work: Path, name: str) -> Path:
    """Fresh copy of a design in the work dir with check and sch run (library tables, project files)."""
    from hwflow.pipeline import stages as pl_stages
    from hwflow.core.design import Design
    from hwflow.core.libs import Libs
    dst = work / name / "prep"
    shutil.rmtree(dst.parent, ignore_errors=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("build", "__pycache__", ".venv"))
    if name == "thermostat-unfixed":
        import yaml
        raw = yaml.safe_load((dst / "design.yaml").read_text())
        freed = []
        for ref, c in raw["components"].items():
            fp = str(c.get("footprint", ""))
            if ref.startswith(KEEP_FIXED) or "Keepout" in fp or "MountingHole" in fp or "PanelSlot" in fp:
                continue
            pl = c.get("placement")
            if pl:
                for k in ("x", "y", "rot", "fixed"):
                    pl.pop(k, None)
                if not pl:
                    del c["placement"]
                freed.append(ref)
        (dst / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False, width=200))
        print(f"  released {len(freed)} of {len(raw['components'])} parts", flush=True)
    if name.startswith("pcbp_"):  # place mode: release the parts the importer marked free (cutout.yaml `free`)
        import yaml
        raw = yaml.safe_load((dst / "design.yaml").read_text())
        free = json.loads((dst / "pcbench.json").read_text())["free"]
        for ref in free:
            del raw["components"][ref]["placement"]
        (dst / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False, width=200))
        print(f"  released {len(free)} of {len(raw['components'])} parts", flush=True)
    d = Design.load(dst)
    libs = Libs(design=d)
    c = pl_stages.stage_check(d, libs)
    s = pl_stages.stage_sch(d, libs)
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
    a["routability_mean"] = mean(g(lambda r: r.get("routability", 0.0 if r.get("timeout") or r.get("error") else None)))
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
    for sec, keys in (("pcbench", ("routability", "clean_pass", "drv", "wl_mm", "vias", "time_s")),
                      ("pcbench_place", ("legal", "routability", "clean_pass", "drv", "hpwl_ratio", "wl_ratio", "vias_delta", "time_s"))):
        for k in keys:
            x, y = (old.get(sec) or {}).get(k), (new.get(sec) or {}).get(k)
            if x is not None and y is not None:
                out.append(f"{sec.upper()} {k}: {x} -> {y} ({y - x:+.3f})")
    for k in ("clean_before_repair_rate", "attempts_mean_of_designs", "ok_rate"):
        x, y = old.get("aggregate", {}).get(k), new["aggregate"].get(k)
        if x is not None and y is not None:
            out.append(f"ALL {k}: {x} -> {y} ({y - x:+.2f})")
    return "\n".join(out)


def pcbench_summary(doc: dict) -> dict | None:
    """The real-board rows in PCBWorld's terms: routability is the mean over every board (a timeout or a crash counts 0),
    DRV (DRC errors other than unconnected), wirelength and vias are means over the fully routed boards (their RQ2 rule)."""
    rows = [(n, dd["runs"][0]) for n, dd in doc["designs"].items() if n.startswith("pcb:") and dd["runs"]]
    if not rows:
        return None
    full = [r for _, r in rows if r.get("routability") == 1.0]
    s = {"boards": len(rows), "routability": mean([r.get("routability", 0.0) for _, r in rows]),
         "fully_routed": len(full), "clean_pass": mean([int(r.get("routability") == 1.0 and r.get("drv_after") == 0) for _, r in rows]),
         "drv": mean([r.get("drv_after") for r in full]), "wl_mm": mean([r.get("track_mm") for r in full]),
         "vias": mean([r.get("vias_board") for r in full]), "time_s": mean([r.get("wall_s") for _, r in rows]),
         "router_s": mean([(r.get("phase_s") or {}).get("router") for _, r in rows]),
         "fixed_errors": mean([r.get("fixed_errors") for _, r in rows]),
         "failed": [n for n, r in rows if r.get("routability") != 1.0 or r.get("drv_after")]}
    sys.path.insert(0, str(ROOT / "scripts"))
    from pcbench import REFERENCE
    s["reference_d3a_fair95"] = REFERENCE
    return s


def pcbench_table(s: dict) -> str:
    L = ["", f"PCBENCH (PCBWorld D3-A real boards, fixed placement): {s['boards']} boards",
         "  method               rout.   clean    DRV      WL     vias   time_s",
         f"  hwflow               {s['routability']:.3f}   {s['clean_pass']:.2f}  {s['drv'] or 0:6.2f}  {s['wl_mm'] or 0:7.1f}  {s['vias'] or 0:6.2f}  "
         f"{s['time_s']:6.1f} (router {s['router_s'] or 0:.1f})"]
    for k, v in s["reference_d3a_fair95"].items():
        L.append(f"  {k:<20} {v['routability']:.3f}      -   {v['drv']:6.2f}  {v['wl_mm']:7.1f}  {v['vias']:6.2f}  {v['time_s']:6.2f}")
    L.append("  reference rows: PCBWorld RQ2, d3a fair-95 (99-board list minus 4 tool-compat boards), router time only;")
    L.append("  hwflow rows: the boards that pass the DRC filter under KiCad 10, time = whole layout stage;")
    L.append(f"  DRV leaves out errors between fixed parts only (original placement): {s['fixed_errors'] or 0:.2f} per board.")
    if s["failed"]:
        L.append("  not clean: " + ", ".join(s["failed"]))
    return "\n".join(L)


def pcbench_place_summary(doc: dict) -> dict | None:
    """Place mode against the human board: `legal` is the share of boards whose placement has no violation that names a
    released part (one between fixed parts only comes with the human board, `inherited`); every board is routed, and
    routability and clean are as in route mode (a blocked board counts 0); the ratios (hwflow / human) are medians over the
    clean boards: HPWL of the placement and track length; vias_delta is the median of hwflow minus human vias."""
    rows = [(n, dd["runs"][0], dd.get("human") or {}) for n, dd in doc["designs"].items() if n.startswith("pcbp:") and dd["runs"]]
    if not rows:
        return None
    good = lambda r: r.get("routability") == 1.0 and r.get("drv_after") == 0 and not r.get("violations_placer")  # noqa: E731
    clean = [(r, h) for _, r, h in rows if good(r)]

    def ratio(k, hk):
        v = [r[k] / h[hk] for r, h in clean if r.get(k) is not None and h.get(hk)]
        return median(v) if v else None
    legal = lambda r: not (r.get("blocked") or r.get("violations_placer") or r.get("timeout") or r.get("error"))  # noqa: E731
    return {"boards": len(rows), "legal": mean([int(legal(r)) for _, r, _ in rows]),
            "inherited": [n for n, r, _ in rows if r.get("placement_violations") and not r.get("violations_placer")],
            "routability": mean([r.get("routability", 0.0) for _, r, _ in rows]),
            "clean_pass": mean([int(good(r)) for _, r, _ in rows]),
            "drv": mean([r.get("drv_after") for _, r, _ in rows if r.get("routability") == 1.0]),
            "hpwl_ratio": ratio("hpwl_mm", "hpwl_mm"), "wl_ratio": ratio("track_mm", "track_mm"),
            "vias_delta": median([(r.get("vias_board") or 0) - h.get("vias", 0) for r, h in clean]) if clean else None,
            "time_s": mean([r.get("wall_s") for _, r, _ in rows]),
            "placer_s": mean([(r.get("phase_s") or {}).get("placer") for _, r, _ in rows]),
            "blocked": [n for n, r, _ in rows if r.get("blocked") or r.get("violations_placer")],
            "failed": [n for n, r, _ in rows if not r.get("blocked") and not r.get("violations_placer")
                       and (r.get("routability") != 1.0 or r.get("drv_after"))]}


def pcbench_place_table(s: dict) -> str:
    f = lambda x, p=2: "-" if x is None else f"{x:.{p}f}"  # noqa: E731
    vias = "-" if s["vias_delta"] is None else f"{s['vias_delta']:+.1f}"
    L = ["", f"PCBENCH PLACE (PCBWorld D3-A, free parts placed by hwflow, the rest fixed): {s['boards']} boards",
         f"  legal placement {f(s['legal'])}, routability {f(s['routability'], 3)}, clean {f(s['clean_pass'])}, "
         f"DRV {f(s['drv'])} (fully routed boards)",
         f"  against the human board (median over clean boards): HPWL x{f(s['hpwl_ratio'])}, track length x{f(s['wl_ratio'])}, "
         f"vias {vias}",
         f"  time {f(s['time_s'], 1)} s per board (placer {f(s['placer_s'], 1)} s)"]
    if s["blocked"]:
        L.append(f"  placer violations ({len(s['blocked'])}): " + ", ".join(s["blocked"]))
    if s.get("inherited"):
        L.append(f"  violations between fixed parts only, from the human placement, not counted ({len(s['inherited'])}): "
                 + ", ".join(s["inherited"]))
    if s["failed"]:
        L.append(f"  routed, not clean ({len(s['failed'])}): " + ", ".join(s["failed"]))
    return "\n".join(L)


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
    ap.add_argument("--pcbench", action="store_true", help="add the imported PCBWorld D3-A real boards (scripts/pcbench.py prep)")
    ap.add_argument("--pcbench-only", action="store_true", help="only the PCBWorld boards")
    ap.add_argument("--pcbench-mode", choices=("route", "place", "both"), default="route",
                    help="route: human placement, all fixed; place: free parts placed by the flow; both")
    ap.add_argument("--child"); ap.add_argument("--offset", type=int, default=0); ap.add_argument("--result"); ap.add_argument("--seeds", type=int, default=4); ap.add_argument("--iters", type=int, default=30000)
    a = ap.parse_args()
    if a.child:
        return child(Path(a.child), a.offset, Path(a.result), a.seeds, a.iters)
    ds = design_set()
    if a.pcbench or a.pcbench_only:
        pb = pcbench_set(a.pcbench_mode)
        if not pb:
            sys.exit(f"no imported boards in {PCBENCH_DIR}; run `uv run python scripts/pcbench.py prep` first")
        ds = pb if a.pcbench_only else {**ds, **pb}
    if a.list:
        print("\n".join(f"{k}: {v}" for k, v in ds.items()))
        return 0
    for n in OPT_IN:
        if not (a.only and n in a.only) and not (a.full_synth and n == "synth150"):
            ds.pop(n, None)
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
        cfg = {**DEFAULTS, **(PCB_CFG if name.startswith("pcb:") else PCBP_CFG if name.startswith("pcbp:") else SPECIAL.get(name, {}))}
        pd = prep(src, work, name.replace(":", "_"))
        runs = []
        n_runs = min(a.runs, cfg["max_runs"] or a.runs)
        for i in range(n_runs):
            t = time.time()
            r = run_one(pd, name.replace(":", "_"), i, a.timeout, work, cfg["seeds"], cfg["iters"], cfg["env"])
            runs.append(r)
            b = r.get("before_a1") or {}
            print(f"  run {i + 1} (seed +{r.get('offset')}): {'TIMEOUT in ' + str(r.get('running_phase_at_timeout')) if r.get('timeout') else ('ERROR ' + str(r.get('error'))[:150] if r.get('error') else ('ok' if r.get('ok') else 'FAIL'))}"
                  f" attempts={r.get('attempts')} before(a1) err={b.get('errors')} unr={b.get('unrouted')} after={(r.get('after') or {}).get('errors')} "
                  f"rounds={r.get('repair_rounds')} {r.get('phase_s')} wall={r['wall_s']}s", flush=True)
            doc["designs"][name] = {"placer": {"seeds": cfg["seeds"], "iters": cfg["iters"]}, "env": cfg["env"], "runs": runs, "aggregate": aggregate(runs)}
            if name.startswith(PCB_PREFIX) and (src / "pcbench.json").exists():
                doc["designs"][name]["human"] = json.loads((src / "pcbench.json").read_text()).get("human")
        shutil.rmtree(work / name.replace(":", "_") / "prep", ignore_errors=True)
    doc["pcbench"] = pcbench_summary(doc)
    doc["pcbench_place"] = pcbench_place_summary(doc)
    allr = [r for n, dd in doc["designs"].items() if n != "thermostat-strict" and not n.startswith(PCB_PREFIX) for r in dd["runs"]]
    agg = aggregate(allr)
    agg["attempts_mean_of_designs"] = mean([dd["aggregate"]["attempts_mean"] for n, dd in doc["designs"].items()
                                            if n != "thermostat-strict" and not n.startswith(PCB_PREFIX)])
    agg["total_wall_s"] = round(time.time() - t_all, 1)
    doc["aggregate"] = agg
    doc["loadavg_end"] = os.getloadavg()
    out = Path(a.out) if a.out else ROOT / "build/bench" / (datetime.now().strftime("%Y-%m-%d-%H%M") + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1))
    print("\n" + table(doc))
    if doc["pcbench"]:
        print(pcbench_table(doc["pcbench"]))
    if doc["pcbench_place"]:
        print(pcbench_place_table(doc["pcbench_place"]))
    if a.compare:
        print(compare(doc, json.loads(Path(a.compare).read_text())))
    print(f"\nwritten {out}")


if __name__ == "__main__":
    sys.exit(main() or 0)
