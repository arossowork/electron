"""Stage time budgets of /hw-design: `hwflow budget <proj>`.

The stage marks of `hwflow time --mark` (build/flow.jsonl) give the elapsed minutes per stage; this module compares
them with a budget per stage, so the lead and its agents can see at a checkpoint whether a stage should stop
polishing and return its best state with the open items listed. Rework (a stage entered again) spends the same
stage's budget. Evidence for the numbers: docs/stage-budget.md.

Waiting for the product person is marked `wait` (`hwflow time <proj> --mark wait` before a turn ends on questions;
the stage is marked again on resume): it has no budget and is not work time.

Budgets come from DEFAULT_MIN, then `<proj>/budget.yaml` ({stage: minutes}, keys like `3-parts` or `3`), then the
environment `HWFLOW_STAGE_BUDGETS="3-parts=40,7-layout=60"`. A stage key matches every mark with the same stage
number (`3-parts`, `3-parts-fix` and `3` are one stage).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import timing

# minutes of agent wall time per stage (not counting the wait for product answers, see docs/stage-budget.md)
DEFAULT_MIN: dict[str, float] = {
    "1-requirements": 10, "1b-partition": 8, "2-architecture": 20, "3-parts": 30, "4-circuit": 40,
    "5-review": 25, "6-optimize": 15, "6b-floorplan": 5, "7-layout": 45, "8-signoff": 30,
    "9-firmware": 45,  # in parallel with 7-layout: not on the critical path
}
WAIT = "wait"  # `hwflow time --mark wait` while the product person answers: no budget, not in the work total
NEAR = 0.8  # share of the budget at which a stage is told to wrap up
TARGET_MIN = 360.0


def _key(stage: str) -> str | None:
    m = re.match(r"\s*(\d+[a-z]?)(?![a-z])", str(stage).lower())
    return m.group(1) if m else None


def budgets(project_dir: Path | None = None, env: str | None = None) -> dict[str, float]:
    """Stage name -> minutes, with the project file and the environment applied."""
    out = dict(DEFAULT_MIN)
    by_key = {_key(k): k for k in out}

    def put(k: str, v) -> None:
        try:
            v = float(v)
        except (TypeError, ValueError):
            return
        name = by_key.get(_key(k)) or str(k)
        out[name] = int(v) if v.is_integer() else v
        by_key.setdefault(_key(name), name)

    f = Path(project_dir) / "budget.yaml" if project_dir else None
    if f and f.is_file():
        import yaml
        data = yaml.safe_load(f.read_text()) or {}
        for k, v in (data.items() if isinstance(data, dict) else []):
            put(str(k), v)
    if env is None:
        from ..core.settings import get_settings
        env = get_settings().tools.stage_budgets
    for item in env.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            put(k.strip(), v.strip())
    return out


def status(project_dir: Path, now: float | None = None, env: str | None = None) -> dict:
    """{current, stages: [{stage, min, budget_min, share, status, entries, rework_min}], total_min, planned_min,
    target_min, action}. status: ok | near (>= 80 %) | over | none (no budget)."""
    pd = Path(project_dir)
    now = time.time() if now is None else now
    b = budgets(pd, env)
    by_key = {_key(k): k for k in b}
    rows = sorted(timing._rows(pd / "build" / timing.FLOW), key=lambda r: r.get("at", 0))
    agg: dict[str, dict] = {}
    current, top, total, done = None, -1.0, 0.0, False
    for i, r in enumerate(rows):  # as timing.flow, in unrounded minutes; one row per stage number
        st = str(r.get("stage"))
        rk = timing._rank(st)
        if rk == 100.0:
            done = True
            break
        mins = max(0.0, ((rows[i + 1]["at"] if i + 1 < len(rows) else now) - r["at"]) / 60)
        k = by_key.get(_key(st), st)
        a = agg.setdefault(k, {"min": 0.0, "entries": 0, "rework_min": 0.0})
        a["min"] += mins
        a["entries"] += 1
        if rk is not None and rk < top:
            a["rework_min"] += mins
        top = max(top, rk) if rk is not None else top
        total += mins
        current = k
    current = None if done else current
    out = []
    for k, a in agg.items():
        bm = b.get(k)
        share = a["min"] / bm if bm else None
        st = "none" if share is None else ("over" if share > 1.0 else "near" if share >= NEAR else "ok")
        out.append({"stage": k, "min": round(a["min"], 1), "budget_min": bm,
                    "share": None if share is None else round(share, 2), "status": st,
                    "entries": a["entries"], "rework_min": round(a["rework_min"], 1)})
    cur = next((r for r in out if r["stage"] == current), None)
    action = None
    if cur and cur["status"] == "over":
        action = ("over budget: stop polishing; leave the files consistent, write the open items (with numbers) to "
                  "state.md or your checkpoint, and return or move to the next stage; an open must that needs a "
                  "product decision goes to the inbox")
    elif cur and cur["status"] == "near":
        action = "near budget: finish the current step, no new iteration; list what is left"
    planned = sum(v for k, v in b.items() if _key(k) != "9")  # firmware runs beside layout
    wait = sum(r["min"] for r in out if r["stage"] == WAIT)
    return {"current": current, "stages": out, "total_min": round(total, 1), "work_min": round(total - wait, 1),
            "wait_min": round(wait, 1), "planned_min": planned, "target_min": TARGET_MIN, "action": action,
            "done": done}


def line(stage: str, project_dir: Path | None = None) -> str:
    """One line for `hwflow time --mark`: the budget of the stage that starts."""
    b = budgets(project_dir)
    k = {_key(x): x for x in b}.get(_key(stage))
    return f"budget {b[k]:g} min (hwflow budget <proj> at each checkpoint)" if k else ""


def summary(project_dir: Path, now: float | None = None, env: str | None = None) -> str:
    s = status(project_dir, now, env)
    name = Path(project_dir).name
    if not s["stages"]:
        return f"budget {name}: no stage marks yet (`hwflow time <proj> --mark <stage>` when a stage starts)\n"
    L = []
    cur = next((r for r in s["stages"] if r["stage"] == s["current"]), None)
    if cur:
        left = (cur["budget_min"] or 0) - cur["min"]
        L.append(f"budget {name}: now {cur['stage']}, {cur['min']:.0f} of {cur['budget_min'] or '?'} min "
                 f"({cur['status']}" + (f", {left:.0f} min left)" if cur["budget_min"] and left >= 0 else ")"))
    else:
        L.append(f"budget {name}: done")
    if s["action"]:
        L.append(f"action: {s['action']}")
    L += ["", "| stage | min | budget | status | rework min |", "|---|---|---|---|---|"]
    L += [f"| {r['stage']} | {r['min']:.0f} | {r['budget_min'] if r['budget_min'] is not None else '-'} | "
          f"{r['status']} | {r['rework_min']:.0f} |" for r in s["stages"]]
    L += ["", f"work {s['work_min']:.0f} min (+ {s['wait_min']:.0f} min waiting for answers); planned {s['planned_min']:.0f} min; target {s['target_min']:.0f} "
              "min"]
    return "\n".join(L) + "\n"
