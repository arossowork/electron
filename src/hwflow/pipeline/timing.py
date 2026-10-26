"""Where the time of a design went: `hwflow time <proj>`.

Two sources, both in the project's build/ folder:
- build/flow.jsonl: one line per `hwflow time <proj> --mark <stage>` ({at, ts, stage, note}), which /hw-design writes
  when it enters a stage (1-requirements, 1b-partition, 2-architecture, ... 9-firmware, done). A stage lasts until the
  next mark. A stage entered again after a later one is rework (a layout verdict that sends the flow back, a review
  finding that changes the circuit): its time is counted apart.
- build/history.jsonl: one line per build with its `timing` (pipeline.report, from runs.run_timing): the wall time of
  each build stage (check, sch, sim, layout, post, fab) and of its steps (sim_s, mc_s, place_s, route_s, repair_s,
  drc_s, extract_s, post_sim_s).

The summary gives the hours per flow stage and the rework, and the build seconds per stage and step over the builds,
so the next run's time budget is measurable against the target.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

FLOW = "flow.jsonl"


def _rows(p: Path) -> list[dict]:
    out = []
    if p.is_file():
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def mark(project_dir: Path, stage: str, note: str | None = None, at: float | None = None) -> dict:
    """Append a stage mark to build/flow.jsonl."""
    b = Path(project_dir) / "build"
    b.mkdir(parents=True, exist_ok=True)
    t = time.time() if at is None else at
    row = {"at": round(t, 1), "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), "stage": stage}
    if note:
        row["note"] = note
    with open(b / FLOW, "a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def _rank(stage: str) -> float | None:
    """1 -> 1.0, 1b -> 1.5, 6b -> 6.5, done -> 100; None for a name without a stage number."""
    if stage.strip().lower() == "done":
        return 100.0
    m = re.match(r"\s*(\d+)([a-z]?)", stage.lower())
    return None if not m else int(m.group(1)) + (0.5 if m.group(2) else 0.0)


def flow(project_dir: Path, now: float | None = None) -> dict:
    """{stages: {stage: {h, entries, rework_h}}, total_h, rework_h, open} from the marks; the last stage runs until
    `now` unless it is `done`."""
    rows = sorted(_rows(Path(project_dir) / "build" / FLOW), key=lambda r: r.get("at", 0))
    now = time.time() if now is None else now
    stages: dict[str, dict] = {}
    top, total, rework = -1.0, 0.0, 0.0
    for i, r in enumerate(rows):
        st = str(r.get("stage"))
        if _rank(st) == 100.0:
            break
        end = rows[i + 1]["at"] if i + 1 < len(rows) else now
        h = max(0.0, (end - r["at"]) / 3600)
        rk = _rank(st)
        again = rk is not None and rk < top
        s = stages.setdefault(st, {"h": 0.0, "entries": 0, "rework_h": 0.0})
        s["h"] += h
        s["entries"] += 1
        if again:
            s["rework_h"] += h
            rework += h
        total += h
        if rk is not None:
            top = max(top, rk)
    done = bool(rows) and _rank(str(rows[-1].get("stage"))) == 100.0
    return {"stages": {k: {kk: round(v, 2) if isinstance(v, float) else v for kk, v in s.items()}
                       for k, s in stages.items()},
            "total_h": round(total, 2), "rework_h": round(rework, 2), "open": not done and bool(rows)}


def builds(project_dir: Path, last: int | None = None) -> dict:
    """{n, layout_builds, wall_s, stages: {stage: s}, steps: {step: s}, rows: [...]} over the builds of
    build/history.jsonl that carry a timing (the newest `last` when given)."""
    rows = [r for r in _rows(Path(project_dir) / "build" / "history.jsonl") if isinstance(r.get("timing"), dict)]
    if last:
        rows = rows[-last:]
    st: dict[str, float] = {}
    sp: dict[str, float] = {}
    wall = 0.0
    nlay = 0
    for r in rows:
        t = r["timing"]
        wall += float(t.get("wall_s") or 0.0)
        for k, v in (t.get("stages") or {}).items():
            st[k] = st.get(k, 0.0) + float(v or 0.0)
        for k, v in (t.get("steps") or {}).items():
            sp[k] = sp.get(k, 0.0) + float(v or 0.0)
        nlay += bool((t.get("stages") or {}).get("layout"))
    return {"n": len(rows), "layout_builds": nlay, "wall_s": round(wall, 1),
            "stages": {k: round(v, 1) for k, v in st.items()}, "steps": {k: round(v, 1) for k, v in sp.items()},
            "rows": rows}


def _hm(s: float) -> str:
    if s < 120:
        return f"{s:.0f} s"
    return f"{int(s // 3600)} h {int(s % 3600 // 60):02d} min" if s >= 3600 else f"{s / 60:.1f} min"


def summary(project_dir: Path, last: int | None = None, now: float | None = None) -> str:
    f = flow(project_dir, now)
    b = builds(project_dir, last)
    L = [f"# {Path(project_dir).name}: where the time went", ""]
    if f["stages"]:
        L += [f"Flow (build/flow.jsonl marks): {f['total_h']:.2f} h"
              + (" so far" if f["open"] else "") + f", of which rework {f['rework_h']:.2f} h "
              f"(stages entered again after a later stage)", "", "| stage | h | entries | rework h |", "|---|---|---|---|"]
        L += [f"| {k} | {v['h']:.2f} | {v['entries']} | {v['rework_h']:.2f} |" for k, v in f["stages"].items()]
        L.append("")
    else:
        L += ["Flow: no stage marks (`hwflow time <proj> --mark <stage>` when a stage starts).", ""]
    if b["n"]:
        L += [f"Builds (build/history.jsonl): {b['n']} with timing, {b['layout_builds']} ran a layout, "
              f"{_hm(b['wall_s'])} of build wall time", "", "| build stage | total | share |", "|---|---|---|"]
        tot = sum(b["stages"].values()) or 1.0
        L += [f"| {k} | {_hm(v)} | {v / tot:.0%} |" for k, v in sorted(b["stages"].items(), key=lambda kv: -kv[1])]
        if b["steps"]:
            L += ["", "| step | total |", "|---|---|"]
            L += [f"| {k} | {_hm(v)} |" for k, v in sorted(b["steps"].items(), key=lambda kv: -kv[1])]
        L += ["", "Last builds:", ""]
        for r in b["rows"][-5:]:
            t = r["timing"]
            parts = ", ".join(f"{k} {v:.0f} s" for k, v in (t.get("stages") or {}).items() if v)
            L.append(f"- {r.get('ts')}: {_hm(float(t.get('wall_s') or 0))} ({parts or 'all cached'}); "
                     f"{str(r.get('verdict'))[:80]}")
    else:
        L.append("Builds: none with timing in build/history.jsonl yet.")
    return "\n".join(L) + "\n"
