"""Stage declarations, inputs, results and cache keys (ADR 0001, sections 2.3 to 2.5).

A stage is a function of what its `StageSpec` declares: design.yaml, the electrical part of requirements.yaml, the
project and repository files it reads, the results and outputs of its upstream stages, project state, a subset of the
settings, a few build options, the source of its code modules and the versions of its tools. `cache_key` hashes
exactly that, so editing a module no stage declares (hwflow.fw) invalidates nothing, and a declared input that
changes always gives a new key. `tests/test_stage_specs.py` checks the declarations against the imports of each stage
function (grimp) and against what a real stage run reads (an audit hook on file opens, stats and subprocesses, and a
recording settings object).

Until #9 splits it into `layout_attempt` and `layout_select`, `layout` is one stage (today's layout stage with
`--auto`); its outputs are the ones ADR 2.3 gives `layout_select`.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

from ..artifacts import ArtifactRef, ref_from_dict, ref_to_dict
from ..core.settings import get_settings

KEY_SCHEMA = "hwflow.stagekey/1"
JSON_MEDIA_TYPE = "application/json"
PROSE_KEYS = ("text", "evidence", "notes", "note", "source", "rationale", "comment")
Verdict = Literal["pass", "fail", "blocked", "error"]
CACHEABLE: tuple[str, ...] = ("pass", "fail", "blocked")  # never error or cancelled (ADR 2.4)


def canonical_json(x: Any) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


# ------------------------------------------------------------------ declarations

@dataclass(frozen=True)
class StageContext:
    """What `StageSpec.run` gets: the design loaded from the stage's scratch copy of the project, its libraries, the
    scratch build dir, the declared params and a progress callback."""
    design: Any
    libs: Any
    workdir: Path
    params: Mapping[str, Any]
    progress: Callable[[str], None] = print


@dataclass(frozen=True)
class StageSpec:
    name: str                       # check | sch | sim | layout | post | fab (layout_attempt/select after #9)
    version: int                    # bump by hand when output semantics change without a code change
    queue: str                      # task queue (ADR 4.2), informational for the engine
    design_sections: tuple[str, ...]  # top-level design.yaml keys read; ("*",) = all
    requirements: bool              # electrical content of requirements.yaml (prose stripped)
    project_files: Callable[[Any], list[str]]   # project relpaths read (a folder ends in "/"), present or not
    shared_files: Callable[[Any], list[Path]]   # repository data read (library/, blocks/), present or not
    upstream: tuple[str, ...]       # stages whose StageResult (result + outputs) is an input
    state_in: tuple[str, ...]       # project state read: "placement" (--reuse), "layout_history", "monte_carlo"
    settings_groups: tuple[str, ...]  # subset of Settings.key_dump(): () or ("layout", "repair", "router")
    params: tuple[str, ...]         # BuildOptions fields that change the result (e.g. "mc")
    code_modules: tuple[str, ...]   # hwflow modules and packages whose source is hashed
    tools: tuple[str, ...]          # "kicad-cli", "kicad-libs", "ngspice", "krt", "freerouting"
    outputs: Mapping[str, str]      # output name -> build-relative path or dir (dir = tree)
    run: Callable[[StageContext], dict]   # wraps today's stage_* function
    summarize: Callable[[dict], dict]     # <= 4 KiB of the result, for events and workflow decisions
    state_out: tuple[str, ...] = ()
    # state read only on a path whose result is never reused from the cache (see `cache_verdicts`), so it is not in
    # the key: the layout history feeds the diagnosis of a failed layout only
    unkeyed_state: tuple[str, ...] = ()
    cache_verdicts: tuple[str, ...] = CACHEABLE  # verdicts stored in the stage cache


# Where project state lives in build/ (ADR 2.5: materialized at its canonical path).
STATE_PATHS = {"placement": "placement.json", "layout_history": "layout_history.json"}


def _rel_inputs(d) -> list[str]:
    """Project files every stage may read besides design.yaml and requirements.yaml: SPICE includes and model
    libraries, the project's own blocks/ and library folders (present or not: a new one changes the result)."""
    from ..core.libs import PROJECT_LIB_DIRS
    out = [str(f) for f in (d.raw.get("sim") or {}).get("includes") or []]
    out += [str(c.get("spice")["lib"]) for c in d.components.values()
            if isinstance(c.get("spice"), dict) and c.get("spice").get("lib")]
    out += [str(x).rstrip("/") + "/" for x in d.raw.get("libraries") or []]
    out += [f"{x}/" for x in (*PROJECT_LIB_DIRS, "blocks")]
    return out


def _check_inputs(d) -> list[str]:
    from .hooks import registry
    out = _rel_inputs(d)
    for fn in registry().check_inputs:
        out += list(fn(d))
    return out


def _shared(d) -> list[Path]:
    """Repository data the design uses: the shared symbol and footprint libraries of its library nicknames and the
    folders of its layout blocks in the block library."""
    from ..core import blocks
    from ..core.libs import shared_lib
    nicks = {"power"}  # the schematic's library table always lists KiCad's power symbols
    for c in d.components.values():
        for k in ("symbol", "footprint"):
            v = c.get(k)
            if isinstance(v, str) and ":" in v:
                nicks.add(v.split(":", 1)[0])
    lib = shared_lib()  # the library search probes every name in both folders
    out = [lib / sub / f"{n}{ext}" for n in sorted(nicks) for sub in ("symbols", "footprints")
           for ext in (".kicad_sym", ".kicad_symdir", ".pretty")]
    out += [blocks.library_root() / str((e or {}).get("use")) for e in (d.raw.get("blocks") or {}).values()]
    return out


def _no_shared(d) -> list[Path]:
    return []


def _summary(keys: tuple[str, ...]) -> Callable[[dict], dict]:
    def summarize(r: dict) -> dict:
        s = {k: r[k] for k in ("ok", *keys) if k in r}
        if "exception" in r:
            s["exception"] = str(r["exception"])[:300]
        if r.get("blocked"):
            s["blocked"] = str(r["blocked"])[:300]
        return s
    return summarize


def _run_check(c: StageContext) -> dict:
    from .stages import stage_check
    return stage_check(c.design, c.libs)


def _run_sch(c: StageContext) -> dict:
    from .stages import stage_sch
    return stage_sch(c.design, c.libs)


def _run_sim(c: StageContext) -> dict:
    from .stages import stage_sim
    return stage_sim(c.design, int(c.params.get("mc") or 0))


def _run_layout(c: StageContext) -> dict:
    """Today's layout stage: `--auto` search or one attempt, the diagnosis of a failed layout, and the history entry.
    The `blocked` pre-check runs in the runner, on the project's own history, before the stage is scheduled."""
    from .stages import _record_layout, layout_auto, layout_diagnosis, stage_layout
    p, d, libs = c.params, c.design, c.libs
    kw = {"seeds": p["seeds"], "iters": p["iters"], "passes": p["passes"]} if p.get("layout_search") else {}
    if p.get("auto"):
        r = layout_auto(d, libs, reuse=p.get("reuse", False), attempts=p.get("attempts", 4), workers=p.get("workers"),
                        **kw)
    else:
        r = stage_layout(d, libs, reuse=p.get("reuse", False), **kw)
        if not r["ok"]:
            r["diagnosis"] = layout_diagnosis(d, libs, [r])
    _record_layout(d, r, bool(p.get("retry_layout")))
    return r


def _run_post(c: StageContext) -> dict:
    from .stages import stage_post
    return stage_post(c.design)


def _run_fab(c: StageContext) -> dict:
    from .stages import stage_fab
    return stage_fab(c.design)


# The code each stage runs (tests/test_stage_specs.py: the transitive hwflow imports of the stage function).
# Packages count whole (every .py and .lib file); hwflow.models is the SPICE model data. hwflow.fw is in no list.
_BASE = ("hwflow.pipeline.spec", "hwflow.pipeline.stages", "hwflow.pipeline.status", "hwflow.pipeline.hooks",
         "hwflow.core")
CODE = {
    "check": _BASE + ("hwflow.sim", "hwflow.layout", "hwflow.verify", "hwflow.models"),
    "sch": _BASE + ("hwflow.layout", "hwflow.schematic"),
    "sim": _BASE + ("hwflow.sim", "hwflow.models"),
    "layout": _BASE + ("hwflow.sim", "hwflow.layout", "hwflow.verify", "hwflow.models"),
    "post": _BASE + ("hwflow.sim", "hwflow.layout", "hwflow.verify", "hwflow.models"),
    "fab": _BASE + ("hwflow.layout",),
}


def _specs() -> dict[str, StageSpec]:
    lay_out = {"board": "{name}.kicad_pcb", "drc": "{name}.drc.json", "placement": "placement.json",
               "dru": "{name}.kicad_dru", "project": "{name}.kicad_pro", "krt_log": "krt.log", "auto": "auto"}
    common = dict(version=1, design_sections=("*",), requirements=True, shared_files=_shared)
    return {s.name: s for s in [
        StageSpec(name="check", queue="hwflow-engine", project_files=_check_inputs, upstream=(), state_in=(),
                  settings_groups=("layout", "repair", "router"), params=(), code_modules=CODE["check"],
                  tools=("kicad-libs",), outputs={}, run=_run_check, summarize=_summary(("errors", "warnings")),
                  **common),
        StageSpec(name="sch", queue="hwflow-verify", project_files=_rel_inputs, upstream=("check",), state_in=(),
                  settings_groups=(), params=(), code_modules=CODE["sch"], tools=("kicad-cli", "kicad-libs"),
                  outputs={"schematic": "{name}.kicad_sch", "project": "{name}.kicad_pro", "dru": "{name}.kicad_dru",
                           "fp_lib_table": "fp-lib-table", "sym_lib_table": "sym-lib-table"},
                  run=_run_sch, summarize=_summary(("errors", "warnings")), **common),
        StageSpec(name="sim", queue="hwflow-sim", project_files=_rel_inputs, upstream=("sch",),
                  state_in=("monte_carlo",), state_out=("monte_carlo",), settings_groups=(), params=("mc",),
                  code_modules=CODE["sim"], tools=("ngspice", "kicad-libs"), outputs={"sim": "sim"}, run=_run_sim,
                  summarize=_summary(("passed", "total", "mc_runs")), **common),
        StageSpec(name="layout", queue="hwflow-layout", project_files=_rel_inputs, upstream=("sch", "sim"),
                  state_in=("placement", "layout_history"), state_out=("placement", "layout_history"),
                  unkeyed_state=("layout_history",), cache_verdicts=("pass",),
                  settings_groups=("layout", "repair", "router"),
                  params=("reuse", "auto", "attempts", "layout_search", "seeds", "iters", "passes", "retry_layout"),
                  code_modules=CODE["layout"], tools=("krt", "freerouting", "kicad-cli", "kicad-libs"), outputs=lay_out,
                  run=_run_layout, summarize=_summary(("drc_errors", "unrouted", "vias", "segments", "board_mm")),
                  **common),
        StageSpec(name="post", queue="hwflow-sim", project_files=_rel_inputs, upstream=("layout", "sim"), state_in=(),
                  settings_groups=(), params=(), code_modules=CODE["post"], tools=("ngspice", "kicad-libs"),
                  outputs={"post": "post"}, run=_run_post, summarize=_summary(()), **common),
        StageSpec(name="fab", queue="hwflow-verify", project_files=_rel_inputs, upstream=("layout", "sch"),
                  state_in=(), settings_groups=(), params=(), code_modules=CODE["fab"], tools=("kicad-cli",),
                  outputs={"fab": "fab"}, run=_run_fab, summarize=_summary(("steps",)), **common),
    ]}


STAGE_SPECS: dict[str, StageSpec] = _specs()


# ------------------------------------------------------------------ inputs and results

@dataclass(frozen=True)
class StageResult:
    stage: str
    verdict: Verdict   # ok -> pass, not ok -> fail, layout blocked, raised -> error
    cache_key: str
    cached: bool
    result: ArtifactRef   # the full stage dict (what status.json holds), JSON
    outputs: Mapping[str, ArtifactRef] = field(default_factory=dict)
    state: Mapping[str, ArtifactRef] = field(default_factory=dict)
    summary: Mapping[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    tool_versions: Mapping[str, str] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None   # {"type", "message", "retryable"} when verdict == "error"

    def to_json(self) -> dict:
        return {"stage": self.stage, "verdict": self.verdict, "cache_key": self.cache_key, "cached": self.cached,
                "result": ref_to_dict(self.result), "outputs": {k: ref_to_dict(v) for k, v in self.outputs.items()},
                "state": {k: ref_to_dict(v) for k, v in self.state.items()}, "summary": dict(self.summary),
                "seconds": self.seconds, "tool_versions": dict(self.tool_versions),
                "error": dict(self.error) if self.error else None}

    @classmethod
    def from_json(cls, j: Mapping[str, Any]) -> StageResult:
        return cls(stage=j["stage"], verdict=j["verdict"], cache_key=j["cache_key"], cached=bool(j.get("cached")),
                   result=ref_from_dict(j["result"]),
                   outputs={k: ref_from_dict(v) for k, v in (j.get("outputs") or {}).items()},
                   state={k: ref_from_dict(v) for k, v in (j.get("state") or {}).items()},
                   summary=dict(j.get("summary") or {}), seconds=float(j.get("seconds") or 0.0),
                   tool_versions=dict(j.get("tool_versions") or {}), error=j.get("error"))


@dataclass(frozen=True)
class StageInput:
    run_id: str
    stage: str
    design: ArtifactRef                       # design.yaml
    project: ArtifactRef                      # tree of the design version (design.yaml, requirements.yaml, notes/, libs)
    upstream: Mapping[str, StageResult] = field(default_factory=dict)   # declared upstream stages only
    state: Mapping[str, ArtifactRef | None] = field(default_factory=dict)
    settings: Mapping[str, Any] = field(default_factory=dict)   # Settings.snapshot() of the run
    params: Mapping[str, Any] = field(default_factory=dict)


class StageCache(Protocol):   # (stage, cache_key) -> StageResult
    def get(self, stage: str, key: str) -> StageResult | None: ...
    def put(self, stage: str, key: str, result: StageResult) -> None: ...


class LocalStageCache:
    """`<cas_dir>/keys/<stage>/<key>.json` (ADR 2.4). Writes are atomic; a corrupt entry reads as a miss."""

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser()

    def _path(self, stage: str, key: str) -> Path:
        return self.root / "keys" / stage / f"{key}.json"

    def get(self, stage: str, key: str) -> StageResult | None:
        try:
            return StageResult.from_json(json.loads(self._path(stage, key).read_text()))
        except (OSError, ValueError, KeyError):
            return None

    def put(self, stage: str, key: str, result: StageResult) -> None:
        import os
        import tempfile
        p = self._path(stage, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".", suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(result.to_json(), sort_keys=True, indent=1))
        os.replace(tmp, p)


# ------------------------------------------------------------------ the key

def module_files(module: str) -> list[Path]:
    """Source files of an hwflow module, or of every module and data file (.lib) of a package."""
    import hwflow
    root = Path(hwflow.__file__).parent
    rel = Path(*module.split(".")[1:])
    if (root / rel).is_dir():
        return sorted(p for p in (root / rel).rglob("*") if p.suffix in (".py", ".lib") and p.is_file()
                      and "__pycache__" not in p.parts)
    f = (root / rel).with_suffix(".py")
    return [f] if f.is_file() else []


@lru_cache(maxsize=64)
def _code_hash(modules: tuple[str, ...], stamp: tuple) -> str:
    import hwflow
    root = Path(hwflow.__file__).parent
    h = hashlib.sha256()
    for f in sorted({f for m in modules for f in module_files(m)}):
        h.update(f.relative_to(root).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def code_hash(modules: tuple[str, ...]) -> str:
    """sha256 over the sorted (relative path, source bytes) of the modules' files. Cached per process by file stamps."""
    files = sorted({f for m in modules for f in module_files(m)})
    stamp = tuple((str(f), f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    return _code_hash(tuple(sorted(modules)), stamp)


def file_sha(p: Path) -> str | None:
    if not p.is_file():
        return None
    from ..artifacts import sha256_file
    return sha256_file(p)[0]


def _hash_entry(base: Path, rel: str) -> Any:
    """sha256 of a project file, {relpath: sha256} of a folder ("x/"), None when absent."""
    p = base / rel
    if rel.endswith("/"):
        if not p.is_dir():
            return None
        return {f.relative_to(p).as_posix(): file_sha(f) for f in sorted(p.rglob("*")) if f.is_file()}
    return file_sha(p)


def shared_key(p: Path) -> str:
    """A repository data path without the machine-specific part: relative to the data root, else to its data dir."""
    s = get_settings().paths
    for base, label in ((Path(s.library_dir), "library"), (Path(s.blocks_dir), "blocks"), (Path(s.root), "")):
        try:
            r = Path(p).resolve().relative_to(Path(base).resolve()).as_posix()
            return f"{label}/{r}" if label else r
        except ValueError:
            continue
    return Path(p).name


def stripped_requirements(project_dir: Path) -> Any:
    """The electrical content of requirements.yaml: prose fields (text, evidence, notes, ...) dropped."""
    import yaml
    rq = Path(project_dir) / "requirements.yaml"
    if not rq.exists():
        return None

    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in PROSE_KEYS}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    return strip(yaml.safe_load(rq.read_text()) or {})


def key_doc(spec: StageSpec, inp: StageInput, *, tool_versions: Mapping[str, str], design: Any,
            include_params: bool = True, include_state: bool = True) -> dict:
    """The document `cache_key` hashes. `design` is the Design loaded from the stage's copy of the project (its
    folder is where the declared project files are read)."""
    import yaml
    raw = yaml.safe_load(Path(design.path).read_text()) or {}  # parsed as written: comments ignored, d.raw may be edited
    secs = sorted(raw) if "*" in spec.design_sections else [k for k in spec.design_sections if k in raw]
    files = {rel: _hash_entry(Path(design.dir), rel) for rel in sorted(set(spec.project_files(design)))}
    for p in spec.shared_files(design):
        files[shared_key(p)] = _hash_entry(p.parent, p.name + "/") if p.is_dir() else file_sha(p)
    up = {}
    for st in spec.upstream:
        r = inp.upstream.get(st)
        up[st] = None if r is None else {**{k: v.sha256 for k, v in sorted(r.outputs.items())},
                                         "result": r.result.sha256}
    snap = inp.settings or {}
    return {
        "schema": KEY_SCHEMA, "stage": spec.name, "version": spec.version,
        "code": code_hash(spec.code_modules),
        "design": {k: raw[k] for k in secs},
        "requirements": stripped_requirements(Path(design.dir)) if spec.requirements else None,
        "files": files,
        "upstream": up,
        "state": {s: (r.sha256 if (r := inp.state.get(s)) is not None else None)
                  for s in spec.state_in if s not in spec.unkeyed_state} if include_state else {},
        "settings": {g: snap.get(g) for g in spec.settings_groups},
        "params": {p: inp.params.get(p) for p in spec.params} if include_params else {},
        "tools": {t: tool_versions.get(t, "missing") for t in spec.tools},
    }


def cache_key(spec: StageSpec, inp: StageInput, *, tool_versions: Mapping[str, str], design: Any) -> str:  # 64 hex
    return hashlib.sha256(canonical_json(key_doc(spec, inp, tool_versions=tool_versions, design=design)).encode()
                          ).hexdigest()


def inputs_id(spec: StageSpec, inp: StageInput, *, tool_versions: Mapping[str, str], design: Any) -> str:
    """12 hex of the key without params and state: what a Monte Carlo result was computed for (stage_sim reuses one
    of the same inputs and run count)."""
    doc = key_doc(spec, inp, tool_versions=tool_versions, design=design, include_params=False, include_state=False)
    return hashlib.sha256(canonical_json(doc).encode()).hexdigest()[:12]


# ------------------------------------------------------------------ tool versions

def _run(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return "missing"


@lru_cache(maxsize=16)
def _tool_version(tool: str, where: str) -> str:
    if tool == "kicad-cli":
        return _run([where, "version"]) if where else "missing"
    if tool == "ngspice":
        out = _run([where, "-v"])
        return next((ln.strip() for ln in out.splitlines() if "ngspice" in ln.lower() and any(c.isdigit() for c in ln)),
                    out[:200])
    if tool == "kicad-libs":  # KiCad's symbol and footprint libraries: file names and sizes
        base = Path(where)
        if not where or not (base / "symbols").is_dir():
            return "missing"
        h = hashlib.sha256()
        for sub in ("symbols", "footprints"):
            for p in sorted((base / sub).iterdir()) if (base / sub).is_dir() else []:
                if p.is_dir():
                    for f in sorted(p.iterdir()):
                        h.update(f"{sub}/{p.name}/{f.name}:{f.stat().st_size}".encode())
                else:
                    h.update(f"{sub}/{p.name}:{p.stat().st_size}".encode())
        return h.hexdigest()[:16]
    if tool == "krt":  # the router sources and its compiled extension
        base = Path(where)
        if not (base / "py_router" / "route.py").exists():
            return "missing"
        h = hashlib.sha256()
        for f in sorted(list((base / "py_router").rglob("*.py")) + list((base / "rust_router").glob("*.so"))):
            h.update(f.relative_to(base).as_posix().encode() + f.read_bytes())
        return h.hexdigest()[:16]
    if tool == "freerouting":
        return (file_sha(Path(where)) or "missing")[:16]
    return "missing"


def tool_versions(tools: tuple[str, ...] | None = None) -> dict[str, str]:
    """`kicad-cli version`, `ngspice -v`, a hash of KiCad's library listing, the KRT sources, the Freerouting jar."""
    s = get_settings()
    where = {"kicad-cli": s.tools.kicad_cli, "ngspice": s.tools.ngspice, "kicad-libs": s.paths.kicad_share,
             "krt": s.paths.krt_dir, "freerouting": s.paths.freerouting}
    names = tools if tools is not None else tuple(where)
    return {t: _tool_version(t, where.get(t, "")) for t in names}
