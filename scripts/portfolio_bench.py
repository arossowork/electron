#!/usr/bin/env python
"""Layout portfolio (HWFLOW_PORTFOLIO=1) against the sequential `layout --auto` (the default) on a copy of a design.

The design is copied to <work>/<label>/ (never touched in place), its schematic stage runs once, then
`hwflow layout <copy> --auto` runs in a session of its own while this script samples the session's processes
(/proc: RSS sum and CPU seconds) every 2 s. One JSON line per run goes to <work>/results.jsonl: wall time, CPU
seconds, peak memory, verdict, unrouted, DRC errors, vias, track, the attempt kept and the portfolio table.

    uv run python scripts/portfolio_bench.py <project> --work DIR --mode seq|pf [--seed0 1] [--label L]
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
sys.path.insert(0, str(ROOT / "src"))
SKIP = ("build", "scratch", "fw", ".usage.jsonl")


def pids(root: int) -> list[int]:
    """`root` and its descendants (the portfolio strategies run in sessions of their own, so a process-group kill
    would miss them)."""
    kids: dict = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                s = open(f"/proc/{d}/stat").read()
            except OSError:
                continue
            kids.setdefault(int(s[s.rfind(")") + 2:].split()[1]), []).append(int(d))
    out, todo = [], [root]
    while todo:
        x = todo.pop()
        out.append(x)
        todo += kids.get(x, [])
    return out


def tree(root: int) -> tuple[int, float]:
    """(RSS bytes, CPU seconds incl. waited children) of `root` and its descendants (portfolio strategies start
    sessions of their own, so a session sum would miss them)."""
    page, tick = os.sysconf("SC_PAGE_SIZE"), os.sysconf("SC_CLK_TCK")
    info = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                s = open(f"/proc/{d}/stat").read()
            except OSError:
                continue
            rest = s[s.rfind(")") + 2:].split()
            info[int(d)] = (int(rest[1]), int(rest[21]) * page, sum(int(rest[i]) for i in (11, 12, 13, 14)) / tick)
    kids: dict = {}
    for pid, (pp, _, _) in info.items():
        kids.setdefault(pp, []).append(pid)
    rss = cpu = 0
    todo = [root]
    while todo:
        x = todo.pop()
        if x in info:
            rss += info[x][1]
            cpu += info[x][2]
        todo += kids.get(x, [])
    return rss, cpu


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--mode", choices=["seq", "pf"], required=True)
    ap.add_argument("--seed0", type=int, default=1)
    ap.add_argument("--label")
    ap.add_argument("--limit", type=float, default=4500, help="wall seconds, then the run is killed")
    ap.add_argument("--env", action="append", default=[], metavar="K=V", help="more environment of the layout run")
    a = ap.parse_args()
    project = a.project.resolve()
    label = a.label or f"{project.name}_{a.mode}_s{a.seed0}"
    dst = a.work.resolve() / label
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(project, dst, ignore=lambda d, names: [n for n in names if Path(d) == project and n in SKIP])
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), HWFLOW_SEED0=str(a.seed0),
               HWFLOW_PORTFOLIO="1" if a.mode == "pf" else "0", **dict(kv.split("=", 1) for kv in a.env))
    cli = [sys.executable, "-m", "hwflow.cli"]
    subprocess.run(cli + ["build", str(dst), "--until", "sch", "--force"], cwd=ROOT, env=env, capture_output=True)
    load0 = os.getloadavg()[0]
    t0 = time.time()
    with open(dst / "layout.log", "w") as log:
        p = subprocess.Popen(cli + ["layout", str(dst), "--auto"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True)
        peak, cpu = 0, 0.0
        while p.poll() is None:
            if time.time() - t0 > a.limit:
                import signal
                for x in (signal.SIGTERM, signal.SIGKILL):
                    for q in pids(p.pid):
                        try:
                            os.kill(q, x)
                        except ProcessLookupError:
                            pass
                    time.sleep(5)
                p.wait()
                break
            rss, c = tree(p.pid)
            peak, cpu = max(peak, rss), max(cpu, c)
            time.sleep(2)
    wall = round(time.time() - t0, 1)
    st = json.loads((dst / "build" / "status.json").read_text()) if (dst / "build" / "status.json").exists() else {}
    r = st.get("layout") or {}
    pf = r.get("portfolio") or {}
    row = {"design": project.name, "mode": a.mode, "seed0": a.seed0, "exit": p.returncode, "killed": time.time() - t0 > a.limit, "wall_s": wall,
           "cpu_s": round(cpu, 1), "peak_mb": round(peak / 2 ** 20), "ok": r.get("ok"), "unrouted": r.get("unrouted"),
           "drc_errors": r.get("drc_errors"), "violations": r.get("placement_violations"), "vias": r.get("vias"),
           "track_mm": round(sum(n.get("length_mm", 0) for n in (r.get("nets") or {}).values()), 1),
           "kept": (r.get("auto") or {}).get("kept"), "attempts": (r.get("auto") or {}).get("attempts"),
           "stopped": (r.get("auto") or {}).get("stopped"), "strategy": pf.get("strategy"), "fallback": pf.get("fallback"), "place_s": pf.get("place_s"),
           "portfolio": pf.get("rows"), "pf_peak_mb": pf.get("peak_mb"), "load_start": round(load0, 1),
           "load_end": round(os.getloadavg()[0], 1), "env": a.env,
           "firstpass": r.get("firstpass"), "auto_rows": (r.get("auto") or {}).get("attempts")}
    print(json.dumps(row, default=str))
    with (a.work / "results.jsonl").open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")


if __name__ == "__main__":
    main()
