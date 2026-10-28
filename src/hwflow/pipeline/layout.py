"""The layout stage as two steps (ADR 0001, 4.1): `attempt` places and routes one candidate board, `select` ranks the
attempts the search used, keeps the best board and diagnoses a failed one. hwflow.buildplan decides which attempts run
(attempt 1 alone, the cutoff, the hopeless limit, the budget); the CLI runs attempts 2.. in child processes and the
platform as parallel Temporal activities.

A plain layout (no `--auto`) is one attempt in build/ itself and a select that only diagnoses and records it. An
`--auto` attempt k works in build/auto/a<k>/ as layout_auto always did; the select step sees every finished attempt's
files there again (materialized from its outputs) and leaves build/auto/ as layout_auto leaves it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..core.design import Design
from ..core.libs import Libs
from . import stages as st
from .status import _out


def attempt_dir(k: int, auto: bool) -> str:
    """Build-relative folder of attempt k's files ("" = build/ itself)."""
    return f"auto/a{k}/" if auto else ""


def attempt(d: Design, libs: Libs, p: Mapping[str, Any]) -> dict:
    """The `layout_attempt` stage: params k (from 1), auto, reuse, seeds, iters, passes, deadline (a hint). The result
    is the attempt's layout result with an `attempt` entry {k, line, error, fixed_all} that select drops. A crash of an
    `--auto` attempt ends that attempt (result `ok: False` with `attempt.error`), not the search; a plain layout that
    crashes raises (the stage's verdict is error, as before the split)."""
    k = int(p.get("k") or 1)
    seeds, iters, passes = int(p.get("seeds") or 4), int(p.get("iters") or 30000), int(p.get("passes") or 30)
    if not p.get("auto"):
        r = st.stage_layout(d, libs, reuse=bool(p.get("reuse")), seeds=seeds, iters=iters, passes=passes,
                            eco=None if p.get("reuse") else st.eco_path(_out(d)))
        return {**r, "attempt": {"k": 1}}
    out = _out(d)
    fixed_all = st.all_fixed(d, libs) if k == 1 else False
    size0 = (d.raw.get("board") or {}).get("width_mm"), (d.raw.get("board") or {}).get("height_mm")
    kw = dict(size0=size0, seeds=seeds, iters=iters, passes=passes, reuse=bool(p.get("reuse")))
    from . import portfolio
    if k == 1 and portfolio.on():  # HWFLOW_PORTFOLIO=1: attempt 1 is the strategy portfolio (files in auto/a1/)
        # the later attempts are stages of their own and run with the default knobs (layout_auto, in one process,
        # gives them the best strategy's knobs)
        def run(k_, workdir=None, env=None, placement=None):
            return st.run_attempt(d, libs, out, k_, deadline=p.get("deadline"), workdir=workdir, placement=placement,
                                  **kw)
        res = portfolio.first_attempt(out, d, libs, deadline=p.get("deadline"), attempt=run,
                                      place=lambda k_, path: st.place_shared(d, libs, out, k_, path,
                                                                             **{x: kw[x] for x in kw if x != "passes"}))
    else:
        res = st.run_attempt(d, libs, out, k - 1, deadline=p.get("deadline"), **kw)
    if "error" in res:
        return {"ok": False, "attempt": {"k": k, "error": res["error"], "fixed_all": fixed_all}}
    return {**res["r"], "attempt": {"k": k, "line": res["line"], "fixed_all": fixed_all}}


def summarize_attempt(r: dict) -> dict:
    """What the planner and the progress line need (<= 4 KiB)."""
    a = r.get("attempt") or {}
    if "exception" in r:
        return {"ok": False, "error": str(r["exception"])[:300], "exception": str(r["exception"])[:300]}
    if a.get("error"):
        return {"ok": False, "error": str(a["error"])[:300], "fixed_all": bool(a.get("fixed_all")), "k": a.get("k")}
    s = {"ok": bool(r.get("ok")), "drc_errors": r.get("drc_errors", 0), "unrouted": r.get("unrouted", 0),
         **({"load": r["load"]} if "load" in r else {}),
         "vias": r.get("vias"), "board_mm": r.get("board_mm"), "fixed_parts_only": bool(r.get("fixed_parts_only")),
         "fixed_all": bool(a.get("fixed_all")), "k": a.get("k"),
         "budget_exhausted": bool(r.get("budget_exhausted"))}
    if a.get("line"):
        s["line"] = str(a["line"])[:1000]
    if r.get("blocked"):
        s["blocked"] = str(r["blocked"])[:300]
    return s


def _strip(r: dict) -> dict:
    return {k: v for k, v in r.items() if k != "attempt"}


def select(d: Design, libs: Libs, attempts: Mapping[int, dict], p: Mapping[str, Any]) -> dict:
    """The `layout_select` stage: `attempts` = k -> the finished attempts' results (their files are in build/ for a
    plain layout, build/auto/a<k>/ for --auto); params from hwflow.buildplan.plan (used, n, cutoff, hopeless,
    fixed_all, over, budget, retry_layout; hints started, workers). Appends the layout history entry."""
    from .. import buildplan
    retry = bool(p.get("retry_layout"))
    if not p.get("auto"):
        r = attempts.get(1)
        if r is None:
            raise RuntimeError("layout: attempt 1 has no result")
        r = _strip(r)
        if "exception" in r:
            return r  # the plain layout raised: the stage's verdict is error, nothing is recorded (as before)
        if not r["ok"]:
            r["diagnosis"] = st.layout_diagnosis(d, libs, [r])
        st._record_layout(d, r, retry)
        return r
    results: dict[int, dict] = {}
    for k, r in attempts.items():
        a = r.get("attempt") or {}
        if "exception" in r:
            results[k - 1] = {"k": k, "error": f"attempt {k}: {r['exception'][:120]}"}
        elif a.get("error"):
            results[k - 1] = {"k": k, "error": a["error"]}
        else:
            results[k - 1] = {"k": k, "r": _strip(r), "line": a.get("line", "")}
    s = buildplan.LayoutSearch(auto=True, n=int(p["n"]), cutoff=p.get("cutoff"), hopeless=bool(p.get("hopeless")),
                               fixed_all=bool(p.get("fixed_all")), deadline=None, budget=float(p.get("budget") or 0),
                               wanted=(), complete=not p.get("over"), over=bool(p.get("over")),
                               used=tuple(int(k) for k in p.get("used") or ()))
    import time
    r = st.auto_select(d, libs, results, s, workers=p.get("workers"), t0=float(p.get("started") or time.time()))
    st._record_layout(d, r, retry)
    return r


def blocked_result(project_dir: Path, retry: bool = False) -> dict | None:
    """The layout result when the layout history of `project_dir/build` blocks this structure (the layout is then not
    scheduled), else None. The platform calls it on a scratch copy holding the project's layout_history state."""
    d = Design.load(Path(project_dir) / "design.yaml")
    return st._layout_blocked(d, retry)
