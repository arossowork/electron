"""The ordered stage list and the build loop.

Every build is a run (hwflow.runs: build/runs/<run_id>/manifest.json, events.jsonl). Every stage runs through
`execute_stage` (spec.py, exec.py): in a scratch copy of the project, from content-addressed inputs to
content-addressed outputs, with a cache keyed by the stage's declared inputs only. `hwflow.runs.checkout` then projects
the run into build/ (outputs at their canonical paths, status.json, REPORT.md) while it is the newest run, so skills,
the UI and the evals read build/ as before and two builds of one project do not mix.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..artifacts import ArtifactRef, ArtifactStore, LocalCAS, put_tree
from ..ids import uuid7
from ..runs import DesignVersionRef, FileRunStore, Run, RunStatus, StageRecord, StageStatus, checkout, run_to_manifest
from ..runs import now as runs_now
from ..core.design import Design
from ..core.libs import Libs
from ..core.settings import get_settings
from .exec import execute_stage, verdict_of
from .report import _brief, write_report
from .spec import (JSON_MEDIA_TYPE, STAGE_SPECS, STATE_PATHS, LocalStageCache, StageInput, StageResult, StageSpec,
                   canonical_json, tool_versions)
from .stages import _auto_summary, _layout_blocked, _record_layout, layout_auto, layout_diagnosis, stage_check, stage_fab, stage_layout, stage_post, stage_sch, stage_sim
from .status import _design_hash, _out, _status

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


def _load_result(store: ArtifactStore, res: StageResult) -> dict:
    with store.open(res.result.sha256) as fh:
        return json.loads(fh.read())


def state_from_build(d: Design, name: str, store: ArtifactStore, o: BuildOptions,
                     runs: FileRunStore | None = None) -> ArtifactRef | None:
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
    runs = runs or FileRunStore(out / "runs")
    ref = runs.project_state(d.dir.name).get(name)  # monte_carlo: kept only as a ref (build/runs/state.json)
    return ref if ref is not None and store.exists(ref.sha256) else None


def stage_params(spec: StageSpec, o: BuildOptions) -> dict:
    p = {k: getattr(o, k) for k in spec.params}
    if spec.name == "layout":
        p["workers"] = o.workers  # how many attempts run at once: not a result input
    return p


# ------------------------------------------------------------------ the run record

RESULT_OPTIONS = ("until", "stages", "mc", "reuse", "force", "auto", "attempts", "retry_layout", "layout_search",
                  "seeds", "iters", "passes")


def options_hash(o: BuildOptions, key_dump: dict) -> str:
    """12 hex of the options that change results, and the key settings groups (ADR 3.1)."""
    doc = {"options": {k: getattr(o, k) for k in RESULT_OPTIONS}, "settings": key_dump}
    return hashlib.sha256(canonical_json(doc).encode()).hexdigest()[:12]


def code_info() -> dict:
    import subprocess
    from importlib.metadata import PackageNotFoundError, version
    try:
        ver = version("hwflow")
    except PackageNotFoundError:
        ver = None
    try:
        sha = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        sha = None
    return {"hwflow": ver, "git_sha": sha, "stage_versions": {k: s.version for k, s in STAGE_SPECS.items()}}


def output_paths(d: Design) -> dict[str, dict[str, str]]:
    """stage -> output (or "state/<name>") -> build-relative path, for checkout."""
    out = {}
    for st, spec in STAGE_SPECS.items():
        p = {k: v.format(name=d.name) for k, v in spec.outputs.items()}
        p.update({f"state/{s}": STATE_PATHS[s] for s in spec.state_out if s in STATE_PATHS})
        out[st] = p
    return out


def stage_record(key: str, seq: int, res: StageResult, started: datetime) -> StageRecord:
    """A finished stage: `succeeded` when it ran (pass, fail or blocked is the verdict), `failed` when it raised."""
    outs = dict(res.outputs)
    outs.update({f"state/{k}": v for k, v in res.state.items()})
    return StageRecord(key=key, stage=res.stage, seq=seq,
                       status=StageStatus.failed if res.verdict == "error" else StageStatus.succeeded,
                       verdict=res.verdict, cached=res.cached, cache_key=res.cache_key or None, started_at=started,
                       ended_at=runs_now(), seconds=res.seconds, summary=dict(res.summary), result=res.result,
                       outputs=outs, error=dict(res.error) if res.error else None)


def _verdict(report: str) -> str:
    return "PASS" if "\nVerdict: PASS" in "\n" + report else "FAIL"


def new_run(d: Design, o: BuildOptions, store: ArtifactStore, snap: dict, key_dump: dict) -> Run:
    design_ref = store.put_file(d.path, media_type="application/yaml", name="design.yaml")
    rq = d.dir / "requirements.yaml"
    req_ref = store.put_file(rq, media_type="application/yaml", name="requirements.yaml") if rq.is_file() else None
    project_ref = put_project(store, d)
    from dataclasses import asdict
    return Run(run_id=str(uuid7()), kind="build", tenant_id=None, project_id=d.dir.name,
               design_version=DesignVersionRef(id=project_ref.sha256, content_sha256=project_ref.sha256,
                                               design=design_ref, requirements=req_ref, project=project_ref),
               status=RunStatus.queued, verdict=None, options=asdict(o), options_hash=options_hash(o, key_dump),
               settings=snap, code=code_info(), tools=tool_versions(), stages=[], artifacts={}, created_at=runs_now())


def render_report(run: Run, d: Design, store: ArtifactStore) -> str:
    """REPORT.md of a run that is not the newest one (so not in build/): written in a temporary copy of the design
    with the run's status view."""
    import tempfile

    from ..artifacts import get_tree
    from ..runs import render_status
    with tempfile.TemporaryDirectory() as t:
        tp = Path(t) / d.dir.name
        get_tree(store, run.design_version.project, tp)  # the design version the run built
        (tp / "build").mkdir(parents=True, exist_ok=True)
        (tp / "build" / "status.json").write_text(json.dumps(render_status(run, store), indent=2, default=str))
        return write_report(Design.load(tp / "design.yaml"))


# ------------------------------------------------------------------ the build

def run_build(d: Design, o: BuildOptions) -> dict:
    """Run the stages in order through execute_stage as one run (build/runs/<run_id>/), project the run into build/
    (outputs, status.json, REPORT.md) while it is the newest one, and return {"rc", "report", "prev_report", "text",
    "run_id"}; `text` is what the CLI prints (the report or its --brief diff)."""
    stages = o.stages if o.stages is not None else STAGES[: STAGES.index(o.until) + 1]
    rc = 0
    out = _out(d)
    prev_report = (out / "REPORT.md").read_text() if (out / "REPORT.md").exists() else ""
    settings = get_settings()
    snap = settings.snapshot()
    store = LocalCAS(settings.paths.cas_dir)
    cache = LocalStageCache(store.root)
    runs = FileRunStore(out / "runs")
    run = runs.create(new_run(d, o, store, snap, settings.key_dump()))
    rid = run.run_id
    runs.transition(rid, RunStatus.running)
    paths = output_paths(d)
    results: dict[str, StageResult] = {}

    def project(report=None) -> bool:
        return checkout(runs.get(rid), d.dir, store, output_paths=paths, report=report, runs=runs)
    try:
        project()  # the resolved settings of this run (status.json `settings`)
        stopped = None
        for seq, st in enumerate(stages, 1):
            if stopped:
                runs.upsert_stage(rid, StageRecord(key=st, stage=st, seq=seq, status=StageStatus.skipped,
                                                   summary={"reason": stopped}))
                continue
            t0, started = time.time(), runs_now()
            spec = STAGE_SPECS[st]
            runs.upsert_stage(rid, StageRecord(key=st, stage=st, seq=seq, status=StageStatus.running,
                                               started_at=started))
            if st == "layout" and (blk := _layout_blocked(d, o.retry_layout)):
                # the layout diagnosis sent this structure back: the stage is not scheduled
                ref = store.put_bytes(json.dumps(blk, sort_keys=True, default=str).encode(),
                                      media_type=JSON_MEDIA_TYPE, name="layout.result.json")
                res = StageResult(stage=st, verdict="blocked", cache_key="", cached=False, result=ref,
                                  summary={**spec.summarize(blk), "design_hash": _design_hash(d),
                                           "inputs_hash": "blocked"})
            else:
                upstream = {}
                for u in spec.upstream:
                    ur = results.get(u) or result_from_build(d, u, store)
                    if ur is not None:
                        upstream[u] = ur
                state = {s: state_from_build(d, s, store, o, runs) for s in spec.state_in}
                inp = StageInput(run_id=rid, stage=st, design=run.design_version.design,
                                 project=run.design_version.project, upstream=upstream, state=state, settings=snap,
                                 params=stage_params(spec, o))
                res = execute_stage(spec, inp, store, cache, runs.root, tool_cache_dir=out,
                                    rebuild=o.rebuild or not o.build, reuse_verdicts=REUSE_VERDICTS)
            results[st] = res
            runs.upsert_stage(rid, stage_record(st, seq, res, started))
            runs.set_project_state(d.dir.name, {k: v for k, v in res.state.items() if k not in STATE_PATHS}, rid)
            project()
            if res.cached:
                print(f"[{st}] skipped: passed last time with the same inputs ({res.cache_key[:12]}); "
                      "--rebuild to run it")
                continue
            r = _load_result(store, res)
            print(f"[{st}] {'OK' if r.get('ok') else 'FAIL'} ({time.time() - t0:.1f}s)" +
                  (f" {r['exception']}" if "exception" in r else ""))
            if r.get("auto"):
                print(_auto_summary(r))
            if not r.get("ok"):
                rc = 1
                hard = st in ("check", "sch") or "exception" in r or bool(r.get("blocked"))
                if o.build and hard and not o.force:
                    stopped = f"hard_failure:{st}"
        texts: list[str] = []
        latest = project(report=lambda: texts.append(write_report(d, history=True)))
        if not latest:
            print(f"[run {rid}] a newer build of this project started: build/ shows that one")
            texts.append(render_report(runs.get(rid), d, store))
        report = texts[0]
        arts = {"REPORT.md": store.put_bytes(report.encode(), media_type="text/markdown", name="REPORT.md")}
        if latest and (out / "status.json").is_file():
            arts["status.json"] = store.put_file(out / "status.json", media_type=JSON_MEDIA_TYPE, name="status.json")
        runs.set_artifacts(rid, arts)
        runs.transition(rid, RunStatus.succeeded, verdict=_verdict(report))
        man = json.dumps(run_to_manifest(runs.get(rid)), indent=1, sort_keys=True).encode()
        runs.set_artifacts(rid, {"manifest.json": store.put_bytes(man, media_type=JSON_MEDIA_TYPE,
                                                                   name="manifest.json")})
    except BaseException as e:
        try:
            runs.transition(rid, RunStatus.failed, error={"type": type(e).__name__, "message": str(e)[:2000],
                                                          "retryable": False})
        except Exception:  # noqa: BLE001 - the original error is the one to report
            pass
        raise
    brief_out = o.brief or (o.layout_search and o.auto)
    return {"rc": rc, "report": report, "prev_report": prev_report, "run_id": rid,
            "text": _brief(prev_report, report) if brief_out else report}
