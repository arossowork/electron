#!/usr/bin/env python
"""Calibration of the routability predictor: per net and per board, prediction (pred.json) against the routed
outcome (truth.json) of every case in a scripts/routab_bench.py work directory.

    uv run python scripts/routab_eval.py --work DIR [--json OUT]

Per net (pour nets left out): flags `access` (a pad with <= ACCESS_MAX exits), `corridor` (margin under
CORRIDOR_MARGIN_MM), `ratio>=t` (window ratio at its pads) for several t; precision, recall and the base rate over all
cases. Per board: hard nets predicted against open signal nets and DRC errors (Spearman rank correlation), and the
verdict against the outcome class (routed clean / not)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _rank(v):
    s = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and v[s[j + 1]] == v[s[i]]:
            j += 1
        for k in range(i, j + 1):
            r[s[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a, b) -> float:
    ra, rb = _rank(a), _rank(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return cov / (va * vb) if va and vb else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    rows, nets = [], []
    for c in sorted(a.work.iterdir()):
        if not (c / "truth.json").exists() or not (c / "pred.json").exists():
            continue
        t = json.loads((c / "truth.json").read_text())
        p = json.loads((c / "pred.json").read_text())
        pour = {"GND"} if "GND" in (t.get("open_nets") or {}) else set()
        open_ = {n for n in (t.get("open_nets") or {}) if n not in pour}
        blocked = "placement illegal" in (t.get("router") or "") or t.get("blocked")
        rows.append({"case": c.name, "board": p["board"], "layers": p["layers"], "routed_nets": p["routed_nets"],
                     "hard": p["hard_nets"], "share": p["hard_share"], "verdict": p["verdict"],
                     "max_ratio": p["max_ratio"], "hot_area": p["hot_area_mm2"], "access": len(p["access"]),
                     "open_signal": len(open_), "unrouted": t["unrouted"], "drc": t["drc_errors"],
                     "clean": t["unrouted"] == 0 and t["drc_errors"] == 0 and not blocked, "blocked": bool(blocked),
                     "route_s": t.get("route_s"), "pred_s": p.get("seconds"),
                     "line": max([(b or {}).get("ratio", 0) for b in ((p.get("bounds") or {}).get("line_x"),
                                                                     (p.get("bounds") or {}).get("line_y"))] or [0]),
                     "ring": max([r["ratio"] for r in (p.get("bounds") or {}).get("rings", [])] or [0]),
                     "proofs": len(p.get("proofs") or [])})
        for n, f in (p.get("per_net") or {}).items():
            nets.append({"case": c.name, "net": n, "open": n in open_, **f, "hard": n in p["hard"]})
    print("| case | board | L | nets | hard | share | max ratio | hot mm2 | access pads | line | ring | proofs | pred s "
          "| route s | open signal | unrouted | DRC | verdict |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['case']} | {r['board'][0]:g}x{r['board'][1]:g} | {r['layers']} | {r['routed_nets']} | {r['hard']} | "
              f"{r['share']:.2f} | {r['max_ratio']:.2f} | {r['hot_area']:.0f} | {r['access']} | {r['line']:.2f} | "
              f"{r['ring']:.2f} | {r['proofs']} | {r['pred_s']} | {r['route_s']} | {r['open_signal']} | "
              f"{r['unrouted']} | {r['drc']} | {r['verdict']}{' (blocked)' if r['blocked'] else ''} |")
    ok = [r for r in rows if not r["blocked"]]
    if len(ok) > 2:
        for key in ("share", "hard", "max_ratio", "hot_area", "access", "line", "ring"):
            print(f"spearman({key}, open signal) = {spearman([r[key] for r in ok], [r['open_signal'] for r in ok]):.2f}; "
                  f"(, unrouted+DRC) = {spearman([r[key] for r in ok], [r['unrouted'] + r['drc'] for r in ok]):.2f}")
    base = sum(n["open"] for n in nets) / max(len(nets), 1)
    print(f"\nper net: {len(nets)} nets, {sum(n['open'] for n in nets)} open (base rate {base:.2f})")
    flags = {"access<=1": lambda n: n.get("exits", 99) <= 1, "access==0": lambda n: n.get("exits", 99) == 0,
             "corridor<0.3": lambda n: n.get("corridor", 9) < 0.3, "hard": lambda n: n["hard"]}
    for t in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0):
        flags[f"ratio>={t}"] = (lambda t: lambda n: n.get("ratio", 0) >= t)(t)
    flags["gr"] = lambda n: n.get("gr", 0) == 1
    flags["access<=1 & ratio>=1"] = lambda n: n.get("exits", 99) <= 1 and n.get("ratio", 0) >= 1
    flags["access<=1 | corridor"] = lambda n: n.get("exits", 99) <= 1 or n.get("corridor", 9) < 0.3
    for name, f in flags.items():
        sel = [n for n in nets if f(n)]
        tp = sum(n["open"] for n in sel)
        allp = sum(n["open"] for n in nets)
        print(f"  {name:24s} flagged {len(sel):4d}  precision {tp / max(len(sel), 1):.2f}  recall {tp / max(allp, 1):.2f}")
    if a.json:
        a.json.write_text(json.dumps({"rows": rows, "nets": nets}, indent=1))


if __name__ == "__main__":
    main()
