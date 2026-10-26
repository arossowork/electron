#!/usr/bin/env python
"""Layout regression corpus: small cutouts of real designs (tests/fixtures/layout_corpus/<case>/, made with
`hwflow cutout`) with their measured outcome in expected.yaml.

    uv run python scripts/layout_corpus.py summary [CASE ...] [--jobs N] [--timeout S] [--json FILE]
        routes every case (real KRT, `hwflow layout` in a scratch copy) and prints case, expected, actual, time
    uv run python scripts/layout_corpus.py measure [CASE ...] [--fast] [--route] [--write] [--jobs N]
        measures the fast metrics and/or the routed outcome; --write stores them in expected.yaml

The fast metrics (`fast`) need no router: `hwflow check`, placement legality of the fixed placement, corridor
channels, RUDY congestion and pin access, and a from-scratch re-placement of the parts the source placer was free
to move (`replace`). tests/test_layout_corpus.py asserts them against expected.yaml (fast tier) and routes each case
(slow tier). Nothing here depends on the router branches under test; a case is data only."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "layout_corpus"
CASE_FILES = ("design.yaml", "cutout.yaml", "lib", "blocks")
FAST_CHECK_S = 15.0  # `hwflow check` runs in the fast tier when it took at most this long when measured
REPLACE_MAX_FREE = 20  # the fast tier re-places from scratch the cases with at most this many free parts


def case_dirs(names=None) -> list[Path]:
    out = sorted(p for p in CORPUS.iterdir() if (p / "design.yaml").exists()) if CORPUS.exists() else []
    if names:
        out = [p for p in out if p.name in set(names)]
    return out


def expected(case: Path) -> dict:
    f = Path(case) / "expected.yaml"
    return yaml.safe_load(f.read_text()) or {} if f.exists() else {}


def stage(case: Path, dst: Path) -> Path:
    """A scratch copy of the case (the flow writes build/ next to design.yaml)."""
    dst = Path(dst) / Path(case).name
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for f in CASE_FILES:
        s = Path(case) / f
        if s.is_dir():
            shutil.copytree(s, dst / f)
        elif s.exists():
            shutil.copy2(s, dst / f)
    return dst


# ---------------------------------------------------------------- fast metrics (no router)


def fast(case: Path, check: bool = True) -> dict:
    """Pre-routing metrics of the fixed placement, from a scratch copy (`check`: also run `hwflow check`, the slow
    part on a dense case: its feasibility proofs)."""
    from hwflow.core.design import Design
    from hwflow.core.libs import Libs
    from hwflow.layout.place import corridor, pinaccess, placer
    from hwflow.layout.board import pcb, routing
    from hwflow.pipeline import stages

    with tempfile.TemporaryDirectory() as tmp:
        w = stage(case, Path(tmp))
        t0 = time.time()
        d = Design.load(w / "design.yaml")
        libs = Libs(design=d)
        chk = stages.stage_check(d, libs) if check else None
        t_check = time.time() - t0
        parts = placer.build_parts(d, libs)
        pl = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"])
        viol = pl.violations()
        fixed = pl.fixed_violations()
        cors = pl.corridors()
        cost = pl.cost()
        cong, ovf = pl.congestion(), pl.max_overflow()
        t1 = time.time()
        m = corridor.Model(d, libs, parts=parts)
        acc = pinaccess.pin_access(d, m)
        cls = pcb.net_classes(d)
        floor = routing.neck_floor(d.board)
        necked = sorted({a["net"] for a in acc.values() if a["n"] == 0 and a["floor"] > 0
                         and float(cls.get(a["net"], {}).get("width", d.board["track_mm"])) > floor + 1e-9})
        zero = sorted({a["net"] for a in acc.values() if a["n"] == 0})
        no_access = sorted(f"{r}.{p}" for (r, p), a in acc.items() if a["no_access"])
        t_acc = time.time() - t1
    return {
        "check": {"ok": chk["ok"], "errors": chk["errors"], "warnings": chk["warnings"],
                  "codes": sorted({f.get("code", "?") for f in chk["findings"] if f["sev"] == "error"})} if chk else None,
        "parts": len(parts), "nets": len(d.nets()), "board_mm": [d.board["width_mm"], d.board["height_mm"]],
        "placement_violations": sorted(viol), "fixed_violations": sorted(fixed),
        "corridors": {c["net"]: round(c["width"] - c["track"], 3) for c in cors},
        "placement_cost": round(cost, 1), "rudy": {"congestion": round(cong, 1), "max_overflow": round(ovf, 2)},
        "pin_access": {"pads": len(acc), "nets_no_exit_at_width": zero, "necked": necked, "no_access": no_access},
        "seconds": {"check": round(t_check, 1), "pin_access": round(t_acc, 1)},
    }


def replace(case: Path, seeds: int = 1, iters: int = 4000) -> dict:
    """Re-place, from scratch, the parts the source placer was free to move (cutout.yaml `free`); the others and the
    terminals stay. Returns {free, violations, seconds}."""
    from hwflow.core.design import Design
    from hwflow.core.libs import Libs
    from hwflow.layout.place import cutout, placer

    with tempfile.TemporaryDirectory() as tmp:
        w = stage(case, Path(tmp))
        free = cutout.unfix(w / "design.yaml")
        d = Design.load(w / "design.yaml")
        libs = Libs(design=d)
        t0 = time.time()
        _, _, viol = placer.place(d, libs, seeds=seeds, iters=iters)
    return {"free": len(free), "violations": sorted(viol), "seconds": round(time.time() - t0, 1)}


# ---------------------------------------------------------------- routed outcome (real KRT)

_NET = re.compile(r"\[([^\]]+)\]")


def route(case: Path, workdir: Path | None = None, timeout: float = 3600) -> dict:
    """`hwflow layout` on a scratch copy (placement fixed, every flow stage as on a real board). Returns
    {ok, unrouted, open_nets, drc_errors, drc_types, placement_violations, router, route_groups, seconds}."""
    tmp = None
    if workdir is None:
        tmp = tempfile.mkdtemp(prefix="corpus-")
        workdir = Path(tmp)
    w = stage(case, Path(workdir))
    t0 = time.time()
    env = dict(os.environ)
    try:
        p = subprocess.run([sys.executable, "-m", "hwflow.cli", "layout", str(w)], capture_output=True, text=True,
                           timeout=timeout, env=env, cwd=str(ROOT))
        log = p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout:.0f} s", "seconds": round(time.time() - t0, 1)}
    wall = time.time() - t0
    st = w / "build" / "status.json"
    if not st.exists():
        return {"ok": False, "error": log[-800:], "seconds": round(wall, 1)}
    L = json.loads(st.read_text()).get("layout") or {}
    if p.returncode not in (0, 1) or "Traceback" in log or "router" not in L:
        tail = [ln for ln in log.splitlines() if ln.strip()][-3:]
        return {"ok": False, "error": " | ".join(tail)[-400:], "seconds": round(wall, 1)}
    open_nets, types = set(), {}
    for f in (w / "build").glob("*.drc.json"):  # the full KiCad report (status.json keeps 40 items)
        rep = json.loads(f.read_text())
        for x in rep.get("unconnected_items") or []:
            open_nets |= {n for it in x.get("items") or [] for n in _NET.findall(str(it.get("description", "")))}
        for x in rep.get("violations") or []:
            if x.get("severity") == "error":
                types[x["type"]] = types.get(x["type"], 0) + 1
    out = {"ok": bool(L.get("ok")), "unrouted": L.get("unrouted", 0), "open_nets": sorted(open_nets),
           "drc_errors": L.get("drc_errors", 0), "drc_types": types,
           "placement_violations": len(L.get("placement_violations") or []),
           "isolation": [{"rule": r.get("rule"), "ok": bool(r.get("ok")), "mm": r.get("mm"), "limit": r.get("limit"),
                          "drc_clearance": r.get("drc_clearance"), "drc_creepage": r.get("drc_creepage")}
                         for r in L.get("isolation") or []],
           "router": str(L.get("router", ""))[:300],
           "route_groups": [{k: g.get(k) for k in ("name", "nets", "unrouted", "seconds")}
                            for g in L.get("route_groups") or []],
           "seconds": round(wall, 1), "timing": L.get("timing")}
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def slack(n: int) -> int:
    """Run-to-run noise allowed on a failure count. The repair loop and the layout deadline are wall-time budgets, so
    the same code on a loaded machine can leave a few more items open (measured: ht4_s1_k2in 3 -> 5 unrouted,
    kb_audio 84 -> 96). A count recorded as 0 stays strict, so a clean case may not get one failure."""
    return 0 if n <= 0 else max(2, math.ceil(0.15 * n))


def regressions(exp: dict, got: dict) -> list[str]:
    """What got is worse than the recorded outcome beyond `slack`: more unrouted items, DRC errors, placement
    violations or open nets. Strict (no slack): ok -> not ok, clearance and creepage DRC counts, isolation rows."""
    bad = []
    if exp.get("error"):  # the flow crashed when measured: any result is no worse
        return []
    if got.get("error"):
        return [f"no result: {got['error'][:200]}"]
    for k in ("unrouted", "drc_errors", "placement_violations"):
        if got.get(k, 0) > exp.get(k, 0) + slack(exp.get(k, 0)):
            bad.append(f"{k} {exp.get(k, 0)} -> {got.get(k, 0)}")
    if exp.get("ok") and not got.get("ok"):
        bad.append("ok -> not ok")
    n_open = len(exp.get("open_nets") or [])
    if len(got.get("open_nets") or []) > n_open + slack(n_open):
        bad.append(f"open nets {len(exp.get('open_nets') or [])} -> {len(got.get('open_nets') or [])}")
    for t in ("clearance", "creepage"):  # isolation and clearance DRC per type, from the full KiCad report
        a, b = (exp.get("drc_types") or {}).get(t, 0), (got.get("drc_types") or {}).get(t, 0)
        if b > a:
            bad.append(f"{t} DRC {a} -> {b}")
    gi = {r["rule"]: r for r in got.get("isolation") or []}
    for r in exp.get("isolation") or []:  # copper of other nets inside an isolation distance, on any layer
        if r.get("ok") and not (gi.get(r["rule"]) or {}).get("ok"):
            bad.append(f"isolation {r['rule']}: {r.get('mm')} -> {(gi.get(r['rule']) or {}).get('mm')} mm "
                       f"(limit {r.get('limit')})")
    return bad


def improvements(exp: dict, got: dict) -> list[str]:
    if exp.get("error"):
        return [] if got.get("error") else ["crash -> result"]
    if got.get("error"):
        return []
    out = []
    for k in ("unrouted", "drc_errors", "placement_violations"):
        if got.get(k, 0) < exp.get(k, 0):
            out.append(f"{k} {exp.get(k, 0)} -> {got.get(k, 0)}")
    if got.get("ok") and not exp.get("ok"):
        out.append("not ok -> ok")
    return out


def _route_one(args):
    case, timeout = args
    return case.name, route(case, timeout=timeout)


def _fmt(r: dict) -> str:
    if not r:
        return "-"
    if r.get("error"):
        return "error"
    return (f"{'ok' if r.get('ok') else 'FAIL'} u{r.get('unrouted', 0)} drc{r.get('drc_errors', 0)} "
            f"pv{r.get('placement_violations', 0)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=["summary", "measure"])
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=3600)
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--route", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    cases = case_dirs(a.cases)
    if not cases:
        print("no case")
        return 1
    rows = {}
    do_route = a.what == "summary" or a.route
    if a.what == "measure" and a.fast:
        for c in cases:
            t = time.time()
            f = fast(c)
            free = len((yaml.safe_load((c / "cutout.yaml").read_text()) or {}).get("free") or [])
            r = replace(c) if 0 < free <= REPLACE_MAX_FREE else None
            row = rows.setdefault(c.name, {})
            row["fast"] = f
            row["tiers"] = {"check": "fast" if f["seconds"]["check"] <= FAST_CHECK_S else "slow",
                            "replace": "fast" if r else "none"}
            if r:
                row["replace"] = r
            print(f"{c.name}: fast {time.time() - t:.1f} s, violations {len(f['placement_violations'])}, "
                  + (f"re-placement {r['seconds']} s {len(r['violations'])} violations" if r else "no re-placement"))
    if do_route:
        with ProcessPoolExecutor(max_workers=max(1, a.jobs)) as ex:
            for name, r in ex.map(_route_one, [(c, a.timeout) for c in cases]):
                rows.setdefault(name, {})["route"] = r
                print(f"{name}: {_fmt(r)} {r.get('seconds')} s open {r.get('open_nets')}", flush=True)
    if a.write:
        for c in cases:
            e = expected(c)
            for k, v in rows.get(c.name, {}).items():
                e[k] = v
            e = json.loads(json.dumps(e, default=float))  # numpy scalars -> plain numbers
            e["measured"] = time.strftime("%Y-%m-%d")
            (c / "expected.yaml").write_text(yaml.safe_dump(e, sort_keys=False, width=120))
    if a.what == "summary":
        print(f"\n{'case':28} {'expected':24} {'actual':24} {'exp s':>7} {'act s':>7}  verdict")
        worse = 0
        for c in cases:
            e = expected(c).get("route") or {}
            g = rows[c.name]["route"]
            reg, imp = regressions(e, g), improvements(e, g)
            worse += bool(reg)
            v = "REGRESSION " + "; ".join(reg) if reg else ("better: " + "; ".join(imp) if imp else "same")
            print(f"{c.name:28} {_fmt(e):24} {_fmt(g):24} {e.get('seconds', '-'):>7} {g.get('seconds', '-'):>7}  {v}")
        if a.json:
            Path(a.json).write_text(json.dumps(rows, indent=1))
        return 1 if worse else 0
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
