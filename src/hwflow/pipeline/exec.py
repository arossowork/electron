"""execute_stage: one stage in an ephemeral scratch dir, from artifacts to artifacts (ADR 0001, section 2.5).

Steps: materialize the project tree into `<scratch>/project/`, the declared upstream outputs and project state at their
canonical build paths, and a synthesized `build/status.json` holding the upstream results (today's stage functions read
each other through it); compute the cache key from the declared inputs and return a cached result on a hit; else run
the stage under the run's settings, store the declared outputs and the result JSON, write the cache, and delete the
scratch dir (an `error` keeps a `logs` output first).

`python -m hwflow.pipeline.exec in.json out.json` runs one stage in a child process (the platform worker's entry
point): `in.json` = {"spec", "input", "cas_dir", "scratch_root", "tool_cache_dir", "rebuild"}, `out.json` = the
StageResult as JSON.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import time
import traceback
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

from ..artifacts import TREE_MEDIA_TYPE, ArtifactRef, ArtifactStore, LocalCAS, get_tree, put_tree, ref_from_dict
from ..core import settings as settings_mod
from .spec import (CACHEABLE, JSON_MEDIA_TYPE, STATE_PATHS, StageCache, StageContext, StageInput, StageResult, StageSpec, cache_key,
                   canonical_json, inputs_id)
from .spec import tool_versions as _tool_versions

JSON = JSON_MEDIA_TYPE
TOOL_CACHE_DIRS = ("routecache", "simcache")
STATUS_KEY = {"layout_select": "layout"}  # upstream stage -> its key in the synthesized status.json


def verdict_of(r: dict) -> str:
    if "exception" in r:
        return "error"
    if r.get("blocked"):
        return "blocked"
    return "pass" if r.get("ok") else "fail"


def output_path(spec: StageSpec, name: str, design_name: str) -> str:
    return spec.outputs[name].format(name=design_name)


def read_json(store: ArtifactStore, ref: ArtifactRef) -> Any:
    with store.open(ref.sha256) as fh:
        return json.loads(fh.read())


def _get(store: ArtifactStore, ref: ArtifactRef, dest: Path):
    """A file or a tree at `dest`."""
    if ref.media_type == TREE_MEDIA_TYPE:
        if dest.exists():
            shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
        get_tree(store, ref, dest)
    else:
        store.get_file(ref.sha256, dest)


def materialize(spec: StageSpec, inp: StageInput, store: ArtifactStore, work: Path, design_name: str,
                tool_cache_dir: Path | None) -> Path:
    """The project tree, upstream outputs, state files and the synthesized status.json under `work`. Returns the
    project dir, named like the project's own folder (the tree ref's name): a design without `project:` takes its
    name from it."""
    from .spec import STAGE_SPECS
    proj = work / (inp.project.name or "project")
    get_tree(store, inp.project, proj)
    build = proj / "build"
    build.mkdir(exist_ok=True)
    status: dict[str, Any] = {}
    for st in spec.upstream:
        r = inp.upstream.get(st)
        if r is None:
            continue
        up = STAGE_SPECS.get(st)
        for name, ref in r.outputs.items():
            rel = up.outputs[name].format(name=design_name) if up and name in up.outputs else name
            _get(store, ref, build / rel)
        res = read_json(store, r.result)
        res["inputs_hash"] = r.cache_key[:12]
        status[STATUS_KEY.get(st, st)] = res
    for name, ref in inp.state.items():
        if ref is None or name not in spec.state_in:
            continue
        if name == "monte_carlo":  # the sim stage reads its last Monte Carlo result from status.json
            mc = read_json(store, ref)
            status["sim"] = {"monte_carlo": mc.get("monte_carlo"), "mc_runs": mc.get("mc_runs"),
                             "design_hash": mc.get("design_hash"), "inputs_hash": mc.get("inputs")}
        elif name in STATE_PATHS:
            _get(store, ref, build / STATE_PATHS[name])
    if inp.settings:
        status["settings"] = dict(inp.settings)
    (build / "status.json").write_text(json.dumps(status, indent=2, default=str))
    if tool_cache_dir is not None:  # accelerators with their own content keys, never inputs or outputs
        for name in TOOL_CACHE_DIRS:
            (Path(tool_cache_dir) / name).mkdir(parents=True, exist_ok=True)
            (build / name).symlink_to(Path(tool_cache_dir).resolve() / name, target_is_directory=True)
    return proj


def collect(spec: StageSpec, store: ArtifactStore, build: Path, design_name: str) -> dict[str, ArtifactRef]:
    out = {}
    for name in spec.outputs:
        p = build / output_path(spec, name, design_name)
        if p.is_dir():
            out[name] = put_tree(store, p, name=name)
        elif p.is_file():
            out[name] = store.put_file(p, name=name)
    return out


def collect_state(spec: StageSpec, store: ArtifactStore, build: Path, result: dict,
                  inputs: str) -> dict[str, ArtifactRef]:
    out = {}
    for name in spec.state_out:
        if name == "monte_carlo":
            if result.get("monte_carlo"):
                doc = {"monte_carlo": result["monte_carlo"], "mc_runs": result.get("mc_runs"),
                       "design_hash": result.get("design_hash"), "inputs": inputs}
                out[name] = store.put_bytes(canonical_json(doc).encode(), media_type=JSON, name=name)
        elif name in STATE_PATHS and (build / STATE_PATHS[name]).is_file():
            out[name] = store.put_file(build / STATE_PATHS[name], media_type=JSON, name=name)
    return out


def _logs(store: ArtifactStore, build: Path, lines: list[str]) -> ArtifactRef | None:
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        for f in build.rglob("*.log"):
            if f.is_file() and not f.is_symlink() and "routecache" not in f.parts and "simcache" not in f.parts:
                dst = Path(t) / f.relative_to(build)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(f, dst)
        (Path(t) / "stdout.txt").write_text("\n".join(lines[-200:]) + "\n")
        return put_tree(store, Path(t), name="logs")


class _Tee:
    """Keeps the last lines written to stdout (the `logs` output of a stage that raised)."""

    def __init__(self, inner):
        self.inner, self.lines, self._buf = inner, [], ""

    def write(self, s):
        self._buf += s
        *done, self._buf = self._buf.split("\n")
        self.lines = (self.lines + done)[-200:]
        return self.inner.write(s)

    def flush(self):
        return self.inner.flush()

    def __getattr__(self, k):
        return getattr(self.inner, k)


def execute_stage(spec: StageSpec, inp: StageInput, store: ArtifactStore, cache: StageCache, scratch_root: Path, *,
                  tool_cache_dir: Path | None = None, progress: Callable[[str], None] = print,
                  rebuild: bool = False, reuse_verdicts: Collection[str] = CACHEABLE,
                  keep_scratch: bool = False) -> StageResult:
    """Run one stage from artifacts to artifacts. `rebuild` skips the cache lookup, not the write; `reuse_verdicts`
    limits which cached verdicts a hit may return (the CLI reuses passes only, as its skip logic always did)."""
    from ..core.design import Design
    from ..core.libs import Libs
    from .status import CURRENT_INPUTS
    t0 = time.time()
    work = Path(scratch_root) / inp.run_id / f"{inp.stage}-{secrets.token_hex(4)}"
    work.mkdir(parents=True)
    tools = _tool_versions(spec.tools)
    try:
        with store.open(inp.design.sha256):  # the design ref must exist (it is the tree's design.yaml)
            pass
        design_name = _design_name(store, inp)
        proj = materialize(spec, inp, store, work, design_name, tool_cache_dir)
        d = Design.load(proj / "design.yaml")
        key = cache_key(spec, inp, tool_versions=tools, design=d)
        iid = inputs_id(spec, inp, tool_versions=tools, design=d)
        if not rebuild:
            hit = cache.get(spec.name, key)
            if hit is not None and hit.verdict in reuse_verdicts and _present(store, hit):
                return _replace(hit, cached=True, seconds=round(time.time() - t0, 3))
        snap = settings_mod.Settings.from_snapshot(inp.settings) if inp.settings else settings_mod.get_settings()
        tee = _Tee(sys.stdout)
        tok = CURRENT_INPUTS.set(iid)
        old_stdout, sys.stdout = sys.stdout, tee
        try:
            with settings_mod.use(snap):
                libs = Libs(design=d)
                r = spec.run(StageContext(d, libs, proj / "build", dict(inp.params), progress))
        except Exception as e:  # noqa: BLE001 - a stage that raises gives verdict error (StageException)
            r = {"ok": False, "exception": f"{type(e).__name__}: {e}"}
            err_tb = traceback.format_exc()
        else:
            err_tb = None
        finally:
            sys.stdout = old_stdout
            CURRENT_INPUTS.reset(tok)
        verdict = verdict_of(r)
        build = proj / "build"
        result_ref = store.put_bytes(json.dumps(r, sort_keys=True, default=str).encode(), media_type=JSON,
                                     name=f"{spec.name}.result.json")
        outputs = collect(spec, store, build, design_name) if verdict != "error" else {}
        error = None
        if verdict == "error":
            logs = _logs(store, build, tee.lines + (err_tb or "").splitlines())
            if logs:
                outputs["logs"] = logs
            error = {"type": "StageException", "message": r["exception"][:2000], "retryable": False}
        state = collect_state(spec, store, build, r, iid) if verdict != "error" else {}
        res = StageResult(stage=spec.name, verdict=verdict, cache_key=key, cached=False, result=result_ref,
                          outputs=outputs, state=state, summary=spec.summarize(r), seconds=round(time.time() - t0, 3),
                          tool_versions=tools, error=error)
        if verdict in spec.cache_verdicts and verdict in CACHEABLE:
            cache.put(spec.name, key, res)
        return res
    finally:
        if not keep_scratch:
            shutil.rmtree(work, ignore_errors=True)
            try:
                work.parent.rmdir()  # the run's scratch folder, once its last stage is done
            except OSError:
                pass


def _design_name(store: ArtifactStore, inp: StageInput) -> str:
    """Design.name: `project:` of design.yaml, else the project folder's name."""
    import yaml
    with store.open(inp.design.sha256) as fh:
        raw = yaml.safe_load(fh.read()) or {}
    return raw.get("project") or inp.project.name or "project"


def _present(store: ArtifactStore, r: StageResult) -> bool:
    """A cached result is usable only while its artifacts are still in the store."""
    return store.exists(r.result.sha256) and all(store.exists(v.sha256) for v in r.outputs.values())


def _replace(r: StageResult, **kw) -> StageResult:
    from dataclasses import replace
    return replace(r, **kw)


# ------------------------------------------------------------------ child process entry point

def _main(argv: list[str]) -> int:
    from .spec import STAGE_SPECS, LocalStageCache
    src, dst = Path(argv[0]), Path(argv[1])
    j = json.loads(src.read_text())
    spec = STAGE_SPECS[j["spec"]]
    i = j["input"]
    inp = StageInput(run_id=i["run_id"], stage=i["stage"], design=ref_from_dict(i["design"]),
                     project=ref_from_dict(i["project"]),
                     upstream={k: StageResult.from_json(v) for k, v in (i.get("upstream") or {}).items()},
                     state={k: (ref_from_dict(v) if v else None) for k, v in (i.get("state") or {}).items()},
                     settings=i.get("settings") or {}, params=i.get("params") or {})
    cas = LocalCAS(j.get("cas_dir") or settings_mod.get_settings().paths.cas_dir)
    res = execute_stage(spec, inp, cas, LocalStageCache(cas.root), Path(j["scratch_root"]),
                        tool_cache_dir=Path(j["tool_cache_dir"]) if j.get("tool_cache_dir") else None,
                        rebuild=bool(j.get("rebuild")))
    tmp = dst.with_name(dst.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(res.to_json(), indent=1))
    os.replace(tmp, dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
