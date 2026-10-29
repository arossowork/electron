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

from .. import buildplan
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
from .stages import OTHERS_NICE, _auto_summary, first_alone, _layout_blocked, _record_layout, eco_path, layout_auto, layout_diagnosis, stage_check, stage_fab, stage_layout, stage_post, stage_sch, stage_sim
from .status import _design_hash, _out, _status

STAGES = list(buildplan.STAGES)  # the user stages (`--until`); layout is the layout_attempt and layout_select specs
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
            r = stage_layout(d, libs, reuse=o.reuse, eco=None if o.reuse else eco_path(_out(d)), **kw)
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
    on disk, as a StageResult (`layout_select` is status.json's `layout`)."""
    if st not in STAGE_SPECS or st == buildplan.ATTEMPT:
        return None
    prev = _status(d).get("layout" if st == buildplan.SELECT else st)
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
    if name == "placement_good":  # the last clean layout's placement: attempt 1 starts from it (not with --reuse)
        p = out / STATE_PATHS[name]
        ok = p.is_file() and not o.reuse and get_settings().layout.eco
        return store.put_file(p, media_type=JSON_MEDIA_TYPE, name=name) if ok else None
    if name == "layout_history":
        p = out / STATE_PATHS[name]
        return store.put_file(p, media_type=JSON_MEDIA_TYPE, name=name) if p.is_file() else None
    runs = runs or FileRunStore(out / "runs")
    ref = runs.project_state(d.dir.name).get(name)  # monte_carlo: kept only as a ref (build/runs/state.json)
    return ref if ref is not None and store.exists(ref.sha256) else None


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
    """stage -> output (or "state/<name>") -> build-relative path, for checkout. Layout attempts have none: their
    files reach build/ through layout_select."""
    out = {}
    for st, spec in STAGE_SPECS.items():
        if st == buildplan.ATTEMPT:
            out[st] = {}
            continue
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


def new_run(d: Design, o: BuildOptions, store: ArtifactStore, snap: dict, key_dump: dict,
            run_id: str | None = None) -> Run:
    design_ref = store.put_file(d.path, media_type="application/yaml", name="design.yaml")
    rq = d.dir / "requirements.yaml"
    req_ref = store.put_file(rq, media_type="application/yaml", name="requirements.yaml") if rq.is_file() else None
    project_ref = put_project(store, d)
    from dataclasses import asdict
    return Run(run_id=run_id or str(uuid7()), kind="build", tenant_id=None, project_id=d.dir.name,
               design_version=DesignVersionRef(id=project_ref.sha256, content_sha256=project_ref.sha256,
                                               design=design_ref, requirements=req_ref, project=project_ref),
               status=RunStatus.queued, verdict=None, options=asdict(o), options_hash=options_hash(o, key_dump),
               settings=snap, code=code_info(), tools=tool_versions(), stages=[], artifacts={}, created_at=runs_now())


def render_view(run: Run, store: ArtifactStore, workdir: Path, *, history: bool = False) -> tuple[str, dict]:
    """REPORT.md and status.json of a run away from its project's build/: the run's design version in
    `workdir/<project>/`, its stage outputs at their canonical paths, its status view, then `write_report` (which adds
    `trace` and the report extras to status.json). The platform renders every run this way; the CLI renders a run
    that is no longer the newest one (so not in build/) this way."""
    from ..artifacts import get_tree
    from ..runs import place, render_status
    tp = Path(workdir) / (run.design_version.project.name or "project")
    get_tree(store, run.design_version.project, tp)  # the design version the run built
    build = tp / "build"
    build.mkdir(parents=True, exist_ok=True)
    d = Design.load(tp / "design.yaml")
    paths = output_paths(d)
    for rec in run.stages:
        for name, ref in rec.outputs.items():
            if name in (paths.get(rec.stage) or {}):
                place(store, ref, build / paths[rec.stage][name])
    (build / "status.json").write_text(json.dumps(render_status(run, store), indent=2, default=str))
    report = write_report(Design.load(tp / "design.yaml"), history=history)
    return report, json.loads((build / "status.json").read_text())


def render_report(run: Run, d: Design, store: ArtifactStore) -> str:
    """REPORT.md of a run that is not the newest one (so not in build/), rendered in a temporary copy."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        return render_view(run, store, Path(t))[0]


# ------------------------------------------------------------------ the build

class _Child:
    """A layout attempt in a child process (`python -m hwflow.pipeline.exec`, own process group): the inline form of
    the platform's parallel attempt activities. kill() stops it and the tools it started."""

    def __init__(self, key: str, spec: StageSpec, inp: StageInput, job: Path, cas_dir: Path, tool_cache_dir: Path,
                 rebuild: bool, nice: int = 0):
        import os
        import subprocess
        import sys
        from .exec import input_to_json
        self.key, self.spec, self.job = key, spec, job
        job.mkdir(parents=True, exist_ok=True)
        (job / "in.json").write_text(json.dumps({
            "spec": spec.name, "input": input_to_json(inp), "cas_dir": str(cas_dir), "scratch_root": str(job),
            "tool_cache_dir": str(tool_cache_dir), "rebuild": rebuild, "reuse_verdicts": list(REUSE_VERDICTS)}))
        self.proc = subprocess.Popen([sys.executable, "-m", "hwflow.pipeline.exec", str(job / "in.json"),
                                      str(job / "out.json")], start_new_session=True,
                                     **({"preexec_fn": lambda: os.nice(nice)} if nice else {}))

    def poll(self, store: ArtifactStore) -> StageResult | None:
        """The result once the child has exited (an error result when it crashed), else None."""
        rc = self.proc.poll()
        if rc is None:
            return None
        try:
            doc = json.loads((self.job / "out.json").read_text())
        except (OSError, ValueError):
            doc = {"error": {"type": "WorkerLost", "message": f"the attempt process exited with code {rc}"}}
        import shutil
        shutil.rmtree(self.job, ignore_errors=True)
        if "stage" in doc:  # a StageResult; a failed child writes {"error": {...}} only
            return StageResult.from_json(doc)
        msg = f"{doc['error'].get('type')}: {doc['error'].get('message')}"
        return error_result(store, self.spec, msg)

    def kill(self):
        import os
        import shutil
        import signal
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(10)
            except Exception:  # noqa: BLE001 - it did not stop in time
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(10)
        except ProcessLookupError:
            pass
        shutil.rmtree(self.job, ignore_errors=True)


def error_result(store: ArtifactStore, spec: StageSpec, msg: str) -> StageResult:
    """A stage that did not produce a result (its process died): verdict error."""
    r = {"ok": False, "exception": msg}
    ref = store.put_bytes(json.dumps(r, sort_keys=True).encode(), media_type=JSON_MEDIA_TYPE,
                          name=f"{spec.name}.result.json")
    return StageResult(stage=spec.name, verdict="error", cache_key="", cached=False, result=ref,
                       summary=spec.summarize(r), error={"type": "StageException", "message": msg[:2000],
                                                         "retryable": False})


def _workers(o: BuildOptions, first_alone: bool) -> int:
    """How many layout attempts run at once inline (cpu count - 1 by default, at most the attempts; attempts - 1 after
    attempt 1 when it runs alone first)."""
    import os
    n = max(o.attempts, 1) if o.auto else 1
    w = max(1, min(n, o.workers if o.workers else (os.cpu_count() or 2) - 1))
    return max(1, min(w, n - 1 if first_alone else n))


def run_build(d: Design, o: BuildOptions) -> dict:
    """Drive hwflow.buildplan.plan to the end as one run (build/runs/<run_id>/): each stage through execute_stage
    (layout attempts in child processes, at most `_workers(o)` at once), each result recorded and projected into
    build/ (outputs, status.json, REPORT.md) while the run is the newest one. Returns {"rc", "report", "prev_report",
    "text", "run_id"}; `text` is what the CLI prints (the report or its --brief diff). HWFLOW_RUN_ID (set by
    `hwflow build --detach`) names the run."""
    import os
    stages = buildplan.stage_list({"stages": o.stages, "until": o.until})
    rc = 0
    out = _out(d)
    prev_report = (out / "REPORT.md").read_text() if (out / "REPORT.md").exists() else ""
    settings = get_settings()
    snap = settings.snapshot()
    store = LocalCAS(settings.paths.cas_dir)
    cache = LocalStageCache(store.root)
    runs = FileRunStore(out / "runs")
    run = runs.create(new_run(d, o, store, snap, settings.key_dump(), run_id=os.environ.get("HWFLOW_RUN_ID") or None))
    rid = run.run_id
    runs.transition(rid, RunStatus.running)
    paths = output_paths(d)
    results: dict[str, StageResult] = {}
    from dataclasses import asdict
    alone = first_alone(d, o.reuse)
    opts = {**asdict(o), "stages": stages, "layout_blocked": False, "workers": _workers(o, alone),
            "first_alone": alone}
    lay = dict(snap.get("layout") or {})
    rebuild = o.rebuild or not o.build
    seq = 0
    children: dict[str, _Child] = {}
    started_at: dict[str, datetime] = {}
    layout_t0: float | None = None
    blocked_checked = False

    def project(report=None) -> bool:
        return checkout(runs.get(rid), d.dir, store, output_paths=paths, report=report, runs=runs)

    def start(a: buildplan.RunStage) -> StageInput:
        nonlocal seq
        seq += 1
        started_at[a.key] = runs_now()
        runs.upsert_stage(rid, StageRecord(key=a.key, stage=a.stage, seq=seq, status=StageStatus.running,
                                           started_at=started_at[a.key]))
        spec = STAGE_SPECS[a.stage]
        upstream = {}
        for u in a.upstream:
            ur = results.get(u) or result_from_build(d, u, store)
            if ur is not None:
                upstream[u] = ur
        state = {s: state_from_build(d, s, store, o, runs) for s in spec.state_in}
        return StageInput(run_id=rid, stage=a.stage, design=run.design_version.design,
                          project=run.design_version.project, upstream=upstream, state=state, settings=snap,
                          params=dict(a.params))

    def finish(key: str, res: StageResult, t0: float):
        nonlocal rc
        recs = {r.key: r for r in runs.get(rid).stages}
        results[key] = res
        runs.upsert_stage(rid, stage_record(key, recs[key].seq, res, started_at[key]))
        runs.set_project_state(d.dir.name, {k: v for k, v in res.state.items() if k not in STATE_PATHS}, rid)
        project()
        name = "layout" if res.stage == buildplan.SELECT else res.stage
        if res.stage == buildplan.ATTEMPT:
            s = res.summary
            if opts["auto"]:
                print(f"[layout --auto] {s.get('line') or s.get('error') or s.get('exception')} "
                      f"({time.time() - (layout_t0 or t0):.0f} s of {float(lay.get('budget_s', 0)):.0f} s)", flush=True)
            return
        if res.cached:
            print(f"[{name}] skipped: passed last time with the same inputs ({res.cache_key[:12]}); "
                  "--rebuild to run it")
            return
        r = _load_result(store, res)
        print(f"[{name}] {'OK' if r.get('ok') else 'FAIL'} ({time.time() - t0:.1f}s)" +
              (f" {r['exception']}" if "exception" in r else ""))
        if r.get("auto"):
            print(_auto_summary(r))
        if not r.get("ok"):
            rc = 1

    try:
        project()  # the resolved settings of this run (status.json `settings`)
        stopped = None
        while True:
            now = time.time()
            view = {k: {"verdict": r.verdict, "summary": dict(r.summary)} for k, r in results.items()}
            p = buildplan.plan(opts, view, lay, now, layout_t0 if layout_t0 is not None else now)
            if isinstance(p, buildplan.Done):
                stopped = None if p.reason == "complete" else p.reason
                break
            if any(a.stage == buildplan.ATTEMPT for a in p) and not blocked_checked:
                blocked_checked = True
                if "layout" in stages and _layout_blocked(d, o.retry_layout) is not None:
                    opts["layout_blocked"] = True  # the layout diagnosis sent this structure back
                    continue
            wanted = {a.key for a in p}
            att = buildplan.attempts_of(view)
            for key in [k for k in children if k not in wanted]:  # past the cutoff or the budget
                children.pop(key).kill()
                s = buildplan.layout_search(opts, att, lay, now, layout_t0 or now)
                recs = {r.key: r for r in runs.get(rid).stages}
                runs.upsert_stage(rid, StageRecord(key=key, stage=buildplan.ATTEMPT, seq=recs[key].seq,
                                                   status=StageStatus.skipped, started_at=started_at.get(key),
                                                   ended_at=runs_now(),
                                                   summary={"reason": buildplan.drop_reason(
                                                       s, buildplan.attempt_index(key) or 0)}))
            todo = [a for a in p if a.key not in children and a.key not in results]
            # attempt 1 runs here only when it runs alone first (`first_alone`); else every attempt is a child
            inline = next((a for a in todo if a.stage != buildplan.ATTEMPT
                           or (opts["first_alone"] and a.key == buildplan.attempt_key(1))), None)
            if inline is not None:
                t0 = time.time()
                if inline.stage == buildplan.ATTEMPT and layout_t0 is None:
                    layout_t0 = t0
                    inline = buildplan.plan(opts, view, lay, t0, t0)[0]  # its deadline from the search start
                inp = start(inline)
                if inline.params.get("blocked"):
                    blk = _layout_blocked(d, o.retry_layout) or {"ok": False, "blocked": "blocked"}
                    spec = STAGE_SPECS[buildplan.SELECT]
                    ref = store.put_bytes(json.dumps(blk, sort_keys=True, default=str).encode(),
                                          media_type=JSON_MEDIA_TYPE, name="layout_select.result.json")
                    res = StageResult(stage=buildplan.SELECT, verdict="blocked", cache_key="", cached=False,
                                      result=ref, summary={**spec.summarize(blk), "design_hash": _design_hash(d),
                                                           "inputs_hash": "blocked"})
                else:
                    if inline.stage == buildplan.SELECT and layout_t0 is not None:
                        t0 = layout_t0
                    res = execute_stage(STAGE_SPECS[inline.stage], inp, store, cache, runs.root, tool_cache_dir=out,
                                        rebuild=rebuild, reuse_verdicts=REUSE_VERDICTS)
                finish(inline.key, res, t0)
                continue
            if todo and layout_t0 is None:  # the layout search starts: the attempts' deadline counts from now
                layout_t0 = time.time()
                p = buildplan.plan(opts, view, lay, layout_t0, layout_t0)
                todo = [a for a in p if a.key not in children and a.key not in results]
            for a in todo:  # the attempts in parallel (2.. after attempt 1 with `first_alone`)
                if len(children) >= opts["workers"]:
                    break
                inp = start(a)
                # attempts 2.. at a lower priority: on a busy machine attempt 1, the most likely final one (an
                # incremental layout, or a design that routes), keeps its speed; on idle cores they run as fast
                children[a.key] = _Child(a.key, STAGE_SPECS[a.stage], inp, runs.root / rid / f"{a.key}.job",
                                         store.root, out, rebuild,
                                         nice=OTHERS_NICE if a.key != buildplan.attempt_key(1) else 0)
            if not children:
                break  # nothing runs and nothing may start (the planner would have said Done)
            deadline = (layout_t0 or now) + float(lay.get("budget_s", 3000)) if opts["auto"] else None
            while True:
                done = [(k, c) for k, c in children.items() if c.proc.poll() is not None]
                if done or (deadline is not None and time.time() >= deadline):
                    break
                time.sleep(0.2)
            for key, c in done:
                res = c.poll(store)
                children.pop(key)
                finish(key, res, layout_t0 or now)
        for key in buildplan.remaining(opts, {k: {} for k in results}) if stopped else ():
            seq += 1
            runs.upsert_stage(rid, StageRecord(key=key, stage=key, seq=seq, status=StageStatus.skipped,
                                               summary={"reason": stopped}))
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
        for c in children.values():
            c.kill()
        try:
            runs.transition(rid, RunStatus.cancelled if isinstance(e, KeyboardInterrupt) else RunStatus.failed,
                            error={"type": type(e).__name__, "message": str(e)[:2000], "retryable": False})
        except Exception:  # noqa: BLE001 - the original error is the one to report
            pass
        raise
    brief_out = o.brief or (o.layout_search and o.auto)
    return {"rc": rc, "report": report, "prev_report": prev_report, "run_id": rid,
            "text": _brief(prev_report, report) if brief_out else report}

