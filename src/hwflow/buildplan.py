"""The build planner (ADR 0001, 4.1): which stage actions may run now, given the results so far.

One planner, two drivers: `hwflow.pipeline.runner.run_build` (inline, the CLI) and the platform's `BuildWorkflow`
(Temporal) both loop `plan(...)` until it returns `Done`. It encodes the stage order, `until`, the hard-failure stop
(`check`, `sch`, a stage that raised, a blocked layout, unless `force`) and the `layout --auto` search: attempt 1
alone, then the others in parallel, cut off at the first final attempt (clean, or errors on fixed parts only), no
other attempt when attempt 1 is hopeless (`auto_max_errors`) or every part is fixed, a wall-time budget (`budget_s`
against `now - started`), placer seeds `seed0 + (k-1)*seeds` (applied by the attempt), then `layout_select`.

Stdlib only (import-linter contract "the build planner is stdlib-only"): workflows import it, so it reads no file,
clock, environment or setting; everything comes in as arguments. Decisions depend only on result keys and values,
never on completion order.

Keys: `check`, `sch`, `sim`, `layout_attempt.<k>` (k from 1), `layout_select`, `post`, `fab`. A result is
`{"verdict": "pass"|"fail"|"blocked"|"error", "summary": {...}}`; a layout attempt's summary carries `ok`,
`drc_errors`, `unrouted`, `fixed_parts_only` (bool), `fixed_all` (attempt 1: no movable part) and `error` (the
attempt crashed).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

STAGES = ("check", "sch", "sim", "layout", "post", "fab")   # the user-facing stages (`--until`)
HARD = ("check", "sch")                                      # a failure here stops a build
UPSTREAM = {"check": (), "sch": ("check",), "sim": ("sch",), "post": ("layout_select", "sim"),
            "fab": ("layout_select", "sch")}
ATTEMPT_UPSTREAM = ("sch", "sim")
ATTEMPT = "layout_attempt"
SELECT = "layout_select"
# attempt params that change its result (the cache key); `deadline` is a hint, not keyed
ATTEMPT_PARAMS = ("k", "auto", "reuse", "seeds", "iters", "passes")
SELECT_PARAMS = ("auto", "used", "n", "cutoff", "hopeless", "fixed_all", "over", "budget", "retry_layout")


@dataclass(frozen=True)
class RunStage:
    """An action: run this stage now."""
    key: str
    stage: str
    params: Mapping[str, Any] = field(default_factory=dict)
    upstream: tuple[str, ...] = ()


@dataclass(frozen=True)
class Done:
    reason: str   # "complete" | "hard_failure:<stage>"


@dataclass(frozen=True)
class LayoutSearch:
    """The state of the layout attempt search, derived from the attempt results alone."""
    auto: bool
    n: int                       # attempts the search may run
    cutoff: int | None           # the first final attempt
    hopeless: bool               # attempt 1 is far from routable: no other attempt
    fixed_all: bool              # no movable part: one attempt
    deadline: float | None       # started + budget_s (auto only)
    budget: float
    wanted: tuple[int, ...]      # attempts that should be running now (started or not); () = the search is over
    complete: bool               # every attempt up to the cutoff (or n) has a result
    over: bool                   # the search ended on the budget before it was complete
    used: tuple[int, ...]        # attempts the selection ranks


def stage_list(options: Mapping[str, Any]) -> list[str]:
    """The user stages of the run: `stages` (single-stage commands) or the stages up to `until`."""
    if options.get("stages"):
        return [s for s in options["stages"] if s in STAGES]
    until = options.get("until") or "fab"
    return list(STAGES[: STAGES.index(until) + 1]) if until in STAGES else list(STAGES)


def attempt_key(k: int) -> str:
    return f"{ATTEMPT}.{k}"


def attempt_index(key: str) -> int | None:
    head, _, tail = key.partition(".")
    return int(tail) if head == ATTEMPT and tail.isdigit() else None


def attempts_of(results: Mapping[str, Mapping[str, Any]]) -> dict[int, dict]:
    """k -> the attempt's summary, with `error` set when the attempt itself raised (verdict error)."""
    out = {}
    for key, r in results.items():
        k = attempt_index(key)
        if k is None:
            continue
        s = dict(r.get("summary") or {})
        if r.get("verdict") == "error" and "error" not in s:
            s["error"] = s.get("exception") or "the attempt raised"
        out[k] = s
    return out


def _final(s: Mapping[str, Any]) -> bool:
    """An attempt after which more attempts cannot help: it is clean, or its errors are on fixed parts."""
    return not s.get("error") and not s.get("budget_exhausted") and bool(s.get("ok") or s.get("fixed_parts_only"))


def layout_search(options: Mapping[str, Any], attempts: Mapping[int, Mapping[str, Any]],
                  settings_layout: Mapping[str, Any], now: float, started: float) -> LayoutSearch:
    """The attempt search of `layout --auto` (and the one attempt of a plain layout)."""
    auto = bool(options.get("auto"))
    n = max(int(options.get("attempts") or 1), 1) if auto else 1
    budget = float(settings_layout.get("budget_s", 3000))
    deadline = started + budget if auto else None
    a1 = attempts.get(1)
    if a1 is None:  # attempt 1 runs alone: when it is final the others are never started
        return LayoutSearch(auto, n, None, False, False, deadline, budget, (1,), False, False, ())
    fixed_all = bool(a1.get("fixed_all"))
    if fixed_all:
        n = 1  # no movable part: another seed gives the same placement
    # each DRC error once and each open net once (`load`); `unrouted` (the unconnected items) is already in
    # drc_errors, so adding it counted every open connection twice (thermo-e2e-6: 41 items on 6 nets -> 82)
    errs = int(a1["load"]) if a1.get("load") is not None else int(a1.get("drc_errors") or 0)
    hopeless = (not _final(a1) and not a1.get("error") and not a1.get("budget_exhausted") and "drc_errors" in a1
                and errs > int(settings_layout.get("auto_max_errors", 40)))
    if hopeless:
        n = 1  # far from routable: other seeds of the same placer and router do not close the gap
    finals = sorted(k for k, s in attempts.items() if _final(s))
    cutoff = finals[0] if finals else None
    need = range(1, (cutoff if cutoff is not None else n) + 1)
    complete = all(k in attempts for k in need)
    # A truncated first attempt is retained even for n=1, without treating its errors as hopeless.
    exhausted = bool(a1.get("budget_exhausted"))
    in_time = not exhausted and (deadline is None or now < deadline)
    wanted = tuple(k for k in need if k not in attempts) if (in_time and not complete) else ()
    over = exhausted or (not complete and not wanted)
    used = tuple(sorted(k for k in attempts if cutoff is None or k <= cutoff))
    return LayoutSearch(auto, n, cutoff, hopeless, fixed_all, deadline, budget, wanted, complete, over, used)


def drop_reason(search: LayoutSearch, k: int) -> str:
    """Why a running attempt `k` is no longer wanted: past the first final attempt, or the budget is used up."""
    return "cutoff" if search.cutoff is not None and k > search.cutoff else "budget"


def _hard(stage: str, verdict: str, options: Mapping[str, Any]) -> bool:
    if verdict == "pass" or not options.get("build", True) or options.get("force"):
        return False
    return stage in HARD or verdict in ("error", "blocked")


def plan(options: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]], settings_layout: Mapping[str, Any],
         now: float, started: float) -> list[RunStage] | Done:
    """Every action runnable now, or Done. `results`: key -> {"verdict", "summary"} of the finished actions;
    `settings_layout`: the run's `layout` settings group (`budget_s`, `auto_max_errors`); `now`/`started`: seconds
    (the driver's clock; `started` = when the layout search began, ignored before it). The driver starts the actions
    it is not running yet (as many as it has slots for) and cancels running ones that are no longer returned (an
    attempt past the cutoff or the budget). Option `layout_blocked` (the driver checked the layout history): the
    layout is not run, `layout_select` with param `blocked` stands for it."""
    for st in stage_list(options):
        if st != "layout":
            if st not in results:
                params = {"mc": int(options.get("mc") or 0)} if st == "sim" else {}
                return [RunStage(st, st, params, UPSTREAM[st])]
            if _hard(st, results[st].get("verdict", "error"), options):
                return Done(f"hard_failure:{st}")
            continue
        if SELECT in results:
            if _hard("layout", results[SELECT].get("verdict", "error"), options):
                return Done("hard_failure:layout")
            continue
        if options.get("layout_blocked"):
            return [RunStage(SELECT, SELECT, {"blocked": True, "retry_layout": bool(options.get("retry_layout"))},
                             ())]
        att = attempts_of(results)
        s = layout_search(options, att, settings_layout, now, started)
        if s.wanted:
            return [RunStage(attempt_key(k), ATTEMPT, attempt_params(options, s, k), ATTEMPT_UPSTREAM)
                    for k in s.wanted]
        params = {"auto": s.auto, "used": list(s.used), "n": s.n, "cutoff": s.cutoff, "hopeless": s.hopeless,
                  "fixed_all": s.fixed_all, "over": s.over, "budget": s.budget,
                  "retry_layout": bool(options.get("retry_layout")),
                  "started": started, "workers": options.get("workers")}  # the last two are hints, not keyed
        return [RunStage(SELECT, SELECT, params, ATTEMPT_UPSTREAM + tuple(attempt_key(k) for k in sorted(att)))]
    return Done("complete")


def attempt_params(options: Mapping[str, Any], search: LayoutSearch, k: int) -> dict:
    return {"k": k, "auto": search.auto, "reuse": bool(options.get("reuse")) and k == 1,
            "seeds": int(options.get("seeds") or 4), "iters": int(options.get("iters") or 30000),
            "passes": int(options.get("passes") or 30), "deadline": search.deadline}


def remaining(options: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Keys of the user stages of the run that have no result (recorded as skipped after a hard failure)."""
    out = []
    for st in stage_list(options):
        key = SELECT if st == "layout" else st
        if key not in results:
            out.append(key)
    return out


def keyed(params: Mapping[str, Any], stage: str) -> dict:
    """The params of an action that change its result (the rest are hints: deadline, started, workers)."""
    names = ATTEMPT_PARAMS if stage == ATTEMPT else SELECT_PARAMS if stage == SELECT else tuple(params)
    return {k: params[k] for k in names if k in params}
