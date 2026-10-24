"""The ordered stage list, the build loop and the projection of stage results into build/.

Every stage runs through `execute_stage` (spec.py, exec.py): in a scratch copy of the project, from content-addressed
inputs to content-addressed outputs, with a cache keyed by the stage's declared inputs only. The build then copies the
outputs to their canonical paths in build/ and writes the stage result to build/status.json, so skills, the UI and
the evals read build/ as before.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..artifacts import TREE_MEDIA_TYPE, ArtifactRef, ArtifactStore, LocalCAS, get_tree, put_tree, read_tree
from ..core.design import Design
from ..core.libs import Libs
from ..core.settings import get_settings
from .exec import execute_stage, verdict_of
from .report import _brief, write_report
from .spec import JSON_MEDIA_TYPE, STAGE_SPECS, STATE_PATHS, LocalStageCache, StageInput, StageResult, StageSpec
from .stages import _auto_summary, _layout_blocked, _record_layout, layout_auto, layout_diagnosis, stage_check, stage_fab, stage_layout, stage_post, stage_sch, stage_sim
from .status import _out, _save_status, _status

STAGES = ["check", "sch", "sim", "layout", "post", "fab"]
REUSE_VERDICTS = ("pass",)  # the CLI skips a stage only when it passed with the same inputs (as it always did)
PROJECT_EXCLUDE = ("build", ".git", "__pycache__", "scratch", ".venv", "node_modules")


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


def run_stage(d: Design, libs: Libs, st: str, o: BuildOptions) -> dict:
    """One stage in place, in the project's own build/ (no scratch dir, no cache): a library call for scripts and
    tests. `run_build` runs stages through `execute_stage`."""
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


# ------------------------------------------------------------------ inputs from the project

def project_inputs_under_build(d: Design) -> list[str]:
    """Declared project inputs that live in build/ (e.g. build/approved.json of the approval hook): they go into the
    project tree although build/ does not."""
    out = set()
    for spec in STAGE_SPECS.values():
        out |= {r for r in spec.project_files(d) if r.startswith("build/")}
    return sorted(out)


def put_project(store: ArtifactStore, d: Design) -> ArtifactRef:
    """The project as a tree (its folder minus build/ and tool folders), named like the folder."""
    keep = set(project_inputs_under_build(d))

    def include(rel: Path) -> bool:
        if rel.parts and rel.parts[0] == "build":
            return rel.as_posix() in keep or any(k.endswith("/") and rel.as_posix().startswith(k) for k in keep)
        return not any(p in PROJECT_EXCLUDE or p.endswith(".tmp") for p in rel.parts)
    return put_tree(store, d.dir, name=d.dir.name, include=include)


def _clean(res: dict) -> dict:
    """A status.json entry without what the build adds to it (input hash, time)."""
    return {k: v for k, v in res.items() if k not in ("inputs_hash", "at")}


def result_from_build(d: Design, st: str, store: ArtifactStore) -> StageResult | None:
    """A stage that is not in this run (single-stage commands): its last result in build/status.json and its outputs
    on disk, as a StageResult."""
    prev = _status(d).get(st)
    if not isinstance(prev, dict):
        return None
    spec = STAGE_SPECS[st]
    res = _clean(prev)
    ref = store.put_bytes(json.dumps(res, sort_keys=True, default=str).encode(), media_type=JSON_MEDIA_TYPE,
                          name=f"{st}.result.json")
    outs = {}
    for name, rel in spec.outputs.items():
        p = _out(d) / rel.format(name=d.name)
        if p.is_dir():
            outs[name] = put_tree(store, p, name=name)
        elif p.is_file():
            outs[name] = store.put_file(p, name=name)
    return StageResult(stage=st, verdict=verdict_of(res), cache_key=str(prev.get("inputs_hash") or ""), cached=True,
                       result=ref, outputs=outs)


def state_from_build(d: Design, name: str, store: ArtifactStore, o: BuildOptions) -> ArtifactRef | None:
    out = _out(d)
    if name == "placement":
        p = out / "placement.json"
        if not o.reuse or not p.is_file():
            return None
        doc = json.loads(p.read_text() or "{}")
        size = (_status(d).get("layout") or {}).get("board_mm")
        if "__board_mm__" not in doc and size:  # a file written before placement.json kept its board size
            doc["__board_mm__"] = size
            return store.put_bytes(json.dumps(doc, indent=1).encode(), media_type=JSON_MEDIA_TYPE, name=name)
        return store.put_file(p, media_type=JSON_MEDIA_TYPE, name=name)
    if name == "layout_history":
        p = out / STATE_PATHS[name]
        return store.put_file(p, media_type=JSON_MEDIA_TYPE, name=name) if p.is_file() else None
    ref = load_project_state(d).get(name)  # monte_carlo: kept only as a ref (build/runs/state.json)
    return ref if ref is not None and store.exists(ref.sha256) else None


def _state_path(d: Design) -> Path:
    return _out(d) / "runs" / "state.json"


def load_project_state(d: Design) -> dict[str, ArtifactRef]:
    """Project state refs of the last runs (ADR 3.3 build/runs/state.json): {name: ArtifactRef}."""
    from ..artifacts import ref_from_dict
    try:
        doc = json.loads(_state_path(d).read_text())
    except (OSError, ValueError):
        return {}
    return {k: ref_from_dict(v) for k, v in doc.items() if isinstance(v, dict)}


def save_project_state(d: Design, state: dict[str, ArtifactRef], run_id: str):
    from ..artifacts import ref_to_dict
    from .status import _status_lock, _write_atomic
    if not state:
        return
    p = _state_path(d)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _status_lock(d):
        cur = {k: ref_to_dict(v) for k, v in load_project_state(d).items()}
        cur.update({k: ref_to_dict(v) for k, v in state.items()})
        cur["run_id"] = run_id
        _write_atomic(p, json.dumps(cur, indent=1, sort_keys=True))


def stage_params(spec: StageSpec, o: BuildOptions) -> dict:
    p = {k: getattr(o, k) for k in spec.params}
    if spec.name == "layout":
        p["workers"] = o.workers  # how many attempts run at once: not a result input
    return p


# ------------------------------------------------------------------ projection into build/

def _same_file(p: Path, ref: ArtifactRef) -> bool:
    from .spec import file_sha
    return p.is_file() and p.stat().st_size == ref.size and file_sha(p) == ref.sha256


def place_output(store: ArtifactStore, ref: ArtifactRef, dest: Path):
    """Copy an output to its canonical path (a tree replaces the folder), skipping what is already there."""
    if ref.media_type == TREE_MEDIA_TYPE:
        files = read_tree(store, ref)
        if dest.is_dir() and all(_same_file(dest / rel, ArtifactRef(e["sha256"], e["size"])) for rel, e in files.items()) \
                and len([f for f in dest.rglob("*") if f.is_file()]) == len(files):
            return
        if dest.exists():
            shutil.rmtree(dest) if dest.is_dir() and not dest.is_symlink() else dest.unlink()
        get_tree(store, ref, dest)
    elif not _same_file(dest, ref):
        if dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest)
        store.get_file(ref.sha256, dest)


def checkout_stage(d: Design, res: StageResult, store: ArtifactStore):
    """The outputs and state files of a stage result at their paths in build/."""
    spec = STAGE_SPECS[res.stage]
    out = _out(d)
    for name, ref in res.outputs.items():
        if name in spec.outputs:
            place_output(store, ref, out / spec.outputs[name].format(name=d.name))
    for name, ref in res.state.items():
        if name in STATE_PATHS:
            place_output(store, ref, out / STATE_PATHS[name])


def _load_result(store: ArtifactStore, res: StageResult) -> dict:
    with store.open(res.result.sha256) as fh:
        return json.loads(fh.read())


def _record(d: Design, st: str, res: StageResult, store: ArtifactStore, run_id: str) -> dict:
    r = _load_result(store, res)
    r["inputs_hash"] = res.cache_key[:12]
    _save_status(d, st, r)
    save_project_state(d, dict(res.state), run_id)
    return r


# ------------------------------------------------------------------ the build

def run_build(d: Design, o: BuildOptions) -> dict:
    """Run the stages in order through execute_stage and write build/REPORT.md. Returns
    {"rc", "report", "prev_report", "text"}; `text` is what the CLI prints (the report or its --brief diff)."""
    stages = o.stages if o.stages is not None else STAGES[: STAGES.index(o.until) + 1]
    rc = 0
    out = _out(d)
    prev_report = (out / "REPORT.md").read_text() if (out / "REPORT.md").exists() else ""
    settings = get_settings()
    snap = settings.snapshot()
    _save_status(d, "settings", snap)  # the resolved settings of this run
    store = LocalCAS(settings.paths.cas_dir)
    cache = LocalStageCache(store.root)
    run_id = uuid.uuid4().hex
    design_ref = store.put_file(d.path, media_type="application/yaml", name="design.yaml")
    project_ref = put_project(store, d)
    results: dict[str, StageResult] = {}
    for st in stages:
        t0 = time.time()
        spec = STAGE_SPECS[st]
        if st == "layout" and (blk := _layout_blocked(d, o.retry_layout)):
            r = dict(blk, inputs_hash="blocked")  # the layout diagnosis sent this structure back: not scheduled
            _save_status(d, st, r)
            print(f"[{st}] FAIL ({time.time() - t0:.1f}s)")
            rc = 1
            if o.build and not o.force:
                break
            continue
        upstream = {}
        for u in spec.upstream:
            ur = results.get(u) or result_from_build(d, u, store)
            if ur is not None:
                upstream[u] = ur
        state = {s: state_from_build(d, s, store, o) for s in spec.state_in}
        inp = StageInput(run_id=run_id, stage=st, design=design_ref, project=project_ref, upstream=upstream,
                         state=state, settings=snap, params=stage_params(spec, o))
        res = execute_stage(spec, inp, store, cache, out / "work", tool_cache_dir=out,
                            rebuild=o.rebuild or not o.build, reuse_verdicts=REUSE_VERDICTS)
        results[st] = res
        prev = _status(d).get(st)
        if res.cached:
            print(f"[{st}] skipped: passed last time with the same inputs ({res.cache_key[:12]}); --rebuild to run it")
            checkout_stage(d, res, store)
            if not (isinstance(prev, dict) and prev.get("inputs_hash") == res.cache_key[:12]):
                _record(d, st, res, store, run_id)  # a result of earlier inputs again (e.g. a reverted edit)
            continue
        checkout_stage(d, res, store)
        r = _record(d, st, res, store, run_id)
        print(f"[{st}] {'OK' if r.get('ok') else 'FAIL'} ({time.time() - t0:.1f}s)" +
              (f" {r['exception']}" if "exception" in r else ""))
        if r.get("auto"):
            print(_auto_summary(r))
        if not r.get("ok"):
            rc = 1
            hard = st in ("check", "sch") or "exception" in r or bool(r.get("blocked"))
            if o.build and hard and not o.force:
                break
    work = out / "work"
    if work.is_dir() and not any(work.iterdir()):
        work.rmdir()
    report = write_report(d, history=True)
    brief_out = o.brief or (o.layout_search and o.auto)
    return {"rc": rc, "report": report, "prev_report": prev_report,
            "text": _brief(prev_report, report) if brief_out else report}
