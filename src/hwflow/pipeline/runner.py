"""The ordered stage list, skip logic and the build loop."""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..core.design import Design
from ..core.libs import Libs
from ..core.settings import get_settings
from .hooks import registry
from .report import _brief, write_report
from .stages import _auto_summary, _layout_blocked, _record_layout, layout_auto, layout_diagnosis, stage_check, stage_fab, stage_layout, stage_post, stage_sch, stage_sim
from .status import _design_hash, _inputs_hash, _out, _save_status, _stale_stages, _status

STAGES = ["check", "sch", "sim", "layout", "post", "fab"]


@dataclass
class BuildOptions:
    """What a run needs, independent of argparse (the CLI builds it from its arguments)."""
    until: str = "fab"                # last stage of a build; ignored when `stages` is set
    stages: list[str] | None = None   # explicit stage list (the single-stage commands)
    build: bool = True                # the `build` command: skip unchanged stages, stop at a hard failure
    mc: int = 0
    reuse: bool = False
    force: bool = False               # continue past failing stages
    rebuild: bool = False             # run every stage even when its inputs are unchanged
    auto: bool = False                # layout stage as in `layout --auto`
    attempts: int = 4
    retry_layout: bool = False
    workers: int | None = None
    layout_search: bool = False       # pass seeds/iters/passes to the layout stage (the `layout` command)
    seeds: int = 4
    iters: int = 30000
    passes: int = 30
    brief: bool = False


def _check_hooks_changed(d: Design, prev: dict) -> bool:
    """The extra check findings of the registered hooks no longer match the last result (their inputs live outside
    design.yaml)."""
    reg = registry()
    if not reg.check_state:
        return False
    f: list[dict] = []
    for h in reg.check_findings:
        f += h(d)
    return any(prev.get(k) != v for s in reg.check_state for k, v in s(d, f).items())


def _can_skip(d: Design, st: str, prev, inputs: str, mc: int = 0) -> bool:
    """A stage is skipped when it passed last time with the same inputs and its outputs are still there."""
    if not isinstance(prev, dict) or not prev.get("ok") or "exception" in prev or prev.get("inputs_hash") != inputs:
        return False
    if st in _stale_stages(_status(d), _design_hash(d)):  # e.g. post older than the board it was run on
        return False
    out = _out(d)
    need = {"sch": out / f"{d.name}.kicad_sch", "layout": out / f"{d.name}.kicad_pcb", "fab": out / "fab"}.get(st)
    if need is not None and not need.exists():
        return False
    if st == "sim" and mc and prev.get("mc_runs") != mc:
        return False
    if st == "check" and _check_hooks_changed(d, prev):
        return False
    return True


def run_stage(d: Design, libs: Libs, st: str, o: BuildOptions) -> dict:
    """One stage, as the build runs it."""
    if st == "check":
        return stage_check(d, libs)
    if st == "sch":
        return stage_sch(d, libs)
    if st == "sim":
        return stage_sim(d, o.mc)
    if st == "layout" and (blk := _layout_blocked(d, o.retry_layout)):
        return blk
    if st == "layout":
        kw = {"seeds": o.seeds, "iters": o.iters, "passes": o.passes} if o.layout_search else {}
        if o.auto:
            r = layout_auto(d, libs, reuse=o.reuse, attempts=o.attempts, workers=o.workers, **kw)
        else:
            r = stage_layout(d, libs, reuse=o.reuse, **kw)
            if not r["ok"]:
                r["diagnosis"] = layout_diagnosis(d, libs, [r])
        _record_layout(d, r, o.retry_layout)
        return r
    if st == "post":
        return stage_post(d)
    return stage_fab(d)


def run_build(d: Design, o: BuildOptions) -> dict:
    """Run the stages in order with the skip logic and write build/REPORT.md. Returns
    {"rc", "report", "prev_report", "text"}; `text` is what the CLI prints (the report or its --brief diff)."""
    stages = o.stages if o.stages is not None else STAGES[: STAGES.index(o.until) + 1]
    libs = Libs(design=d)
    rc = 0
    prev_report = (_out(d) / "REPORT.md").read_text() if (_out(d) / "REPORT.md").exists() else ""
    inputs = _inputs_hash(d)
    _save_status(d, "settings", get_settings().snapshot())  # the resolved settings of this run
    ran = False
    for st in stages:
        t0 = time.time()
        # once a stage runs, every later stage runs too: its inputs include the earlier stage's outputs
        if o.build and not o.rebuild and not ran and _can_skip(d, st, _status(d).get(st), inputs, o.mc):
            print(f"[{st}] skipped: passed last time with the same inputs ({inputs}); --rebuild to run it")
            continue
        ran = True
        try:
            r = run_stage(d, libs, st, o)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "exception": f"{type(e).__name__}: {e}"}
        r["inputs_hash"] = inputs
        _save_status(d, st, r)
        print(f"[{st}] {'OK' if r.get('ok') else 'FAIL'} ({time.time() - t0:.1f}s)" +
              (f" {r['exception']}" if "exception" in r else ""))
        if r.get("auto"):
            print(_auto_summary(r))
        if not r.get("ok"):
            rc = 1
            hard = st in ("check", "sch") or "exception" in r or bool(r.get("blocked"))
            if o.build and hard and not o.force:
                break
    report = write_report(d, history=True)
    brief_out = o.brief or (o.layout_search and o.auto)
    return {"rc": rc, "report": report, "prev_report": prev_report,
            "text": _brief(prev_report, report) if brief_out else report}
