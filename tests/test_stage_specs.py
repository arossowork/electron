"""Stage declarations (ADR 0001, 2.3): the registry, the code each stage runs, and the guard against under-declared
inputs.

The guard runs each stage for real on examples/sensor_preamp through execute_stage, with an audit hook on file
opens, stats, folder listings and started programs, a profile hook on the executed code and a settings object that
records every read (tests/stage_probe.py). Every input it sees must be declared in the stage's StageSpec, and changing
it must change the stage's cache key. A read that is not declared, or that does not move the key, fails the test.
"""
from __future__ import annotations

import dataclasses
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from stage_imports import stage_modules  # noqa: E402
from stage_probe import Probe, RecordingSettings, referenced_paths  # noqa: E402

from hwflow.artifacts import LocalCAS  # noqa: E402
from hwflow.core import settings as settings_mod  # noqa: E402
from hwflow.core.design import Design  # noqa: E402
from hwflow.layout.route import krt  # noqa: E402
from hwflow.pipeline import spec as specmod  # noqa: E402
from hwflow.pipeline.exec import execute_stage  # noqa: E402
from hwflow.pipeline.runner import STAGES, put_project  # noqa: E402
from hwflow.pipeline.spec import STAGE_SPECS, LocalStageCache, StageInput, cache_key, module_files  # noqa: E402

SRC = Path(specmod.__file__).resolve().parents[1]
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sensor_preamp"
HAVE_KICAD = bool(settings_mod.get_settings().tools.kicad_cli) and bool(settings_mod.get_settings().paths.kicad_share)
HAVE_NGSPICE = shutil.which(settings_mod.get_settings().tools.ngspice) is not None
HAVE_ROUTER = (krt.krt_dir() / "py_router" / "route.py").exists() or Path(krt.freerouting_jar()).exists()


SPECS = ["check", "sch", "sim", "layout_attempt", "layout_select", "post", "fab"]


def test_registry_covers_the_stages():
    """The user stages, with layout as its two steps (ADR 2.3: layout_attempt, layout_select)."""
    assert list(STAGE_SPECS) == SPECS
    assert [s if s != "layout" else "layout_attempt" for s in STAGES] == [s for s in SPECS if s != "layout_select"]
    for s in STAGE_SPECS.values():
        assert set(s.state_out) <= set(s.state_in) | {"monte_carlo", "placement", "placement_good"}
        assert set(s.unkeyed_state) <= set(s.state_in)
        assert set(s.upstream) <= set(SPECS[:SPECS.index(s.name)])


@pytest.mark.parametrize("stage", SPECS)
def test_code_modules_cover_the_stage_imports(stage):
    """grimp: the hwflow modules the stage function reaches (module-level and lazy imports) are all hashed."""
    spec = STAGE_SPECS[stage]
    missing = sorted(m for m in stage_modules(spec.run)
                     if not any(m == c or m.startswith(c + ".") for c in spec.code_modules))
    assert not missing, f"{stage}: code_modules misses {missing}"


def test_no_stage_hashes_the_firmware_code():
    fw = {f for f in (SRC / "fw").rglob("*.py")}
    for s in STAGE_SPECS.values():
        assert not fw & {f for m in s.code_modules for f in module_files(m)}, s.name


def _copy_src(tmp_path, monkeypatch) -> Path:
    import hwflow
    dst = tmp_path / "src" / "hwflow"
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(hwflow, "__file__", str(dst / "__init__.py"))
    return dst


def test_editing_fw_keeps_the_layout_key_editing_layout_changes_it(tmp_path, monkeypatch):
    """#6 done-when: an edit to a firmware module no longer invalidates layout; an edit to a layout module does."""
    src = _copy_src(tmp_path, monkeypatch)
    lay = STAGE_SPECS["layout_attempt"]
    h0 = specmod.code_hash(lay.code_modules)
    with open(src / "fw" / "__init__.py", "a") as fh:
        fh.write("\n# edited\n")
    assert specmod.code_hash(lay.code_modules) == h0
    with open(src / "layout" / "place" / "placer.py", "a") as fh:
        fh.write("\n# edited\n")
    assert specmod.code_hash(lay.code_modules) != h0
    assert specmod.code_hash(STAGE_SPECS["sim"].code_modules) == specmod.code_hash(STAGE_SPECS["sim"].code_modules)


# ------------------------------------------------------------------ the guard

PY_ROOTS = tuple({os.path.realpath(p) for p in (sys.prefix, sys.base_prefix, sys.exec_prefix, os.path.dirname(os.__file__))})
IGNORED_ROOTS = ("/proc", "/dev", "/sys", "/etc", "/tmp", "/var/tmp", "/usr/lib/locale", "/usr/share/zoneinfo",
                 "/usr/lib/x86_64-linux-gnu", "/lib", "/lib64", "/usr/lib64")


TOOL_BINARIES = ("kicad-cli", "ngspice", "java")
EXECUTOR = os.path.abspath(SRC / "pipeline" / "exec.py")


def _under(p: str, root: str | Path) -> bool:
    root = os.path.abspath(str(root))
    return p == root or p.startswith(root.rstrip("/") + "/")


class Guard:
    """Classifies the reads of one stage run against its declaration."""

    def __init__(self, spec, work: Path, proj: Path, pre: set[str], probe: Probe, upstream_paths: set[str]):
        s = settings_mod.get_settings()
        self.spec, self.work, self.proj, self.build = spec, work, proj, proj / "build"
        self.pre, self.probe, self.upstream_paths = pre, probe, upstream_paths
        self.d = Design.load(proj / "design.yaml")
        self.declared = sorted(set(spec.project_files(self.d)))
        self.shared = [os.path.abspath(p) for p in spec.shared_files(self.d)]
        self.code = {os.path.abspath(f) for m in spec.code_modules for f in module_files(m)}
        self.paths = s.paths
        self.tools = s.tools
        self.problems: list[str] = []
        self.project_reads: set[str] = set()   # project paths read or probed (mutated below)
        self.shared_reads: set[str] = set()
        self.env_reads: set[str] = set()       # the Python environment: interpreter and packages

    def _declared_project(self, rel: str) -> bool:
        if rel in ("", ".", "design.yaml", "requirements.yaml"):
            return True
        for x in self.declared:
            x0 = x.rstrip("/")
            if rel == x0 or (x.endswith("/") and rel.startswith(x)) or x0.startswith(rel + "/"):
                return True  # the entry, a file in a declared folder, or a parent folder of an entry
        return False

    def _declared_shared(self, p: str) -> bool:
        return any(p == x or p.startswith(x + "/") or x.startswith(p + "/") for x in self.shared)

    def _tool(self, name: str, p: str):
        if name not in self.spec.tools:
            self.problems.append(f"reads {p}: tool {name!r} not declared")

    def classify(self, p: str, how: str, from_program: bool = False):
        if not p or p.startswith(("<", "frozen")):
            return
        p = os.path.abspath(p)
        if _under(p, self.build):
            rel = os.path.relpath(p, self.build)
            if (p in self.pre or p in self.probe.written or rel == "status.json" or p in self.upstream_paths
                    or rel.split("/")[0] in ("routecache", "simcache")
                    or any(_under(p, w) for w in self.probe.written if os.path.isdir(w))):
                return
            if from_program:
                return  # a program's output path
            if not self._declared_project(f"build/{rel}"):
                self.problems.append(f"{how} build/{rel}: not an upstream output, state or declared project file")
            else:
                self.project_reads.add(p)
            return
        if _under(p, self.proj):
            rel = os.path.relpath(p, self.proj)
            if not self._declared_project(rel):
                self.problems.append(f"{how} {rel}: project file not declared in project_files")
            elif rel not in ("", "."):
                self.project_reads.add(p)
            return
        if _under(p, SRC):
            if p.endswith((".py", ".pyc", ".lib")) and p.endswith(".py") and p not in self.code:
                self.problems.append(f"{how} {os.path.relpath(p, SRC)}: hwflow code not in code_modules")
            elif p.endswith(".lib") and p not in self.code:
                self.problems.append(f"{how} {os.path.relpath(p, SRC)}: hwflow data not in code_modules")
            return
        for root in (self.paths.library_dir, self.paths.blocks_dir):
            if _under(p, root):
                if p != os.path.abspath(root) and not self._declared_shared(p):
                    self.problems.append(f"{how} {p}: repository data not in shared_files")
                else:
                    self.shared_reads.add(p)
                return
        if self.paths.kicad_share and _under(p, self.paths.kicad_share):
            return self._tool("kicad-libs", p)
        if _under(p, self.paths.krt_dir):
            return self._tool("krt", p)
        if p == os.path.abspath(self.paths.freerouting):
            return self._tool("freerouting", p)
        if _under(p, self.work) or _under(p, self.paths.cas_dir):
            return  # materialization
        real = os.path.realpath(p)
        if any(_under(real, r) or _under(p, r) for r in PY_ROOTS) or "site-packages" in p:
            self.env_reads.add(p)
            return
        if any(_under(p, r) for r in IGNORED_ROOTS) or p in ("/", "/usr", "/usr/lib", "/usr/share", "/home"):
            return
        if os.path.basename(p) in TOOL_BINARIES:
            return  # a program looked up on PATH (the tool's version is the input)
        if _under(p, os.path.expanduser("~")) and not _under(p, os.path.expanduser("~/.cache/hwflow")):
            home_parents = [os.path.expanduser("~")]
            if p in home_parents or os.path.isdir(p) and any(_under(x, p) for x in (str(self.work), str(SRC))):
                return
        self.problems.append(f"{how} {p}: outside every declared input")

    def check(self, reads_programs: set[str], referenced: set[str], settings_reads: set[str]):
        for p in sorted(self.probe.opened):
            self.classify(p, "opens")
        for p in sorted(self.probe.stated - self.probe.opened):
            self.classify(p, "probes")
        for p in sorted(self.probe.listed):
            if _under(p, self.proj) and not _under(p, self.build):
                rel = os.path.relpath(p, self.proj)
                if rel in ("", ".") or not self._declared_project(rel + "/"):
                    self.problems.append(f"lists {rel}/: project folder not declared")
                continue
            self.classify(p, "lists")
        for p in sorted(reads_programs | referenced):
            self.classify(p, "a program reads", from_program=True)
        for f in sorted(self.probe.code):
            if (_under(os.path.abspath(f), SRC) and f.endswith(".py") and os.path.abspath(f) not in self.code
                    and os.path.abspath(f) != EXECUTOR):  # the executor's stdout tee runs on every print
                self.problems.append(f"runs {os.path.relpath(f, SRC)}: not in code_modules")
        for argv in self.probe.commands:
            exe = os.path.basename(argv[0]) if argv else ""
            tool = {"kicad-cli": "kicad-cli", "ngspice": "ngspice", "java": "freerouting"}.get(exe)
            if tool and tool not in self.spec.tools:
                self.problems.append(f"starts {exe}: tool {tool!r} not declared")
            if exe.startswith("python") and any("route" in a for a in argv) and "krt" not in self.spec.tools:
                self.problems.append("starts the KRT router: tool 'krt' not declared")
        groups = {r.split(".")[0] for r in settings_reads} - {"tools", "paths"}
        for g in sorted(groups - set(self.spec.settings_groups)):
            self.problems.append(f"reads settings group {g!r}: not in settings_groups")
        return groups


def _upstream_paths(spec, inp, build: Path, name: str) -> set[str]:
    out = set()
    for key, r in inp.upstream.items():
        st, _, k = key.partition(".")
        up = STAGE_SPECS[st]
        uparams = {"k": int(k), "auto": bool(inp.params.get("auto"))} if k else {}
        for o in r.outputs:
            rel = specmod.output_rel(up, o, name, uparams) if o in up.outputs else o
            out.add(os.path.abspath(build / rel))
    for s in inp.state:
        if s in specmod.STATE_PATHS:
            out.add(os.path.abspath(build / specmod.STATE_PATHS[s]))
    return out


@pytest.fixture
def guard_env(tmp_path, monkeypatch):
    """A copy of the example, an empty shared library and block library (probed paths can be created there), and a
    private CAS."""
    lib, blk = tmp_path / "library", tmp_path / "blocks"
    (lib / "symbols").mkdir(parents=True)
    (lib / "footprints").mkdir(parents=True)
    blk.mkdir()
    monkeypatch.setenv("HWFLOW_LIBRARY_DIR", str(lib))
    monkeypatch.setenv("HWFLOW_BLOCKS_DIR", str(blk))
    monkeypatch.setenv("HWFLOW_CAS_DIR", str(tmp_path / "cas"))
    proj = tmp_path / "sensor_preamp"
    shutil.copytree(EXAMPLE, proj, ignore=shutil.ignore_patterns("build"))
    return proj


def _mutate(p: Path, rel: str):
    """Change a file (a new top-level key in YAML, bytes elsewhere), create a probed one, or add a file to a folder."""
    if p.is_dir():
        (p / "guard_probe.txt").write_text("x")
    elif p.exists():
        with open(p, "a") as fh:
            fh.write("\nguard_probe_key: 1\n" if p.suffix in (".yaml", ".yml") else "\n* guard probe\n")
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("guard_probe: 1\n" if p.suffix in (".yaml", ".yml", ".json") else "x\n") if not rel.endswith("/") \
            else p.mkdir(parents=True, exist_ok=True)
        if rel.endswith("/"):
            (p / "guard_probe.txt").write_text("x")


def _restore(p: Path, backup: dict):
    if p.is_dir() and (p / "guard_probe.txt").exists():
        (p / "guard_probe.txt").unlink()
    if backup.get("bytes") is not None:
        p.write_bytes(backup["bytes"])
    elif not backup.get("existed"):
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()


def _guard_stages(guard_env, monkeypatch, stages: list[str], params: dict | None = None, wrap=None):
    proj = guard_env
    d0 = Design.load(proj / "design.yaml")
    store = LocalCAS(os.environ["HWFLOW_CAS_DIR"])
    cache = LocalStageCache(store.root)
    snap = settings_mod.get_settings().snapshot()
    design_ref = store.put_file(d0.path, name="design.yaml")
    project_ref = put_project(store, d0)
    tools = specmod.tool_versions()
    results, report = {}, {}
    orig = settings_mod.Settings.from_snapshot.__func__
    for st in stages:
        stage, _, k = st.partition(".")
        spec = STAGE_SPECS[stage] if wrap is None else wrap(STAGE_SPECS[stage])
        probe = Probe()
        monkeypatch.setattr(settings_mod.Settings, "from_snapshot",
                            classmethod(lambda cls, s: RecordingSettings(orig(cls, s), probe.settings)))
        pre: set[str] = set()

        def run(ctx, _spec=spec, _probe=probe, _pre=pre):
            _pre.update(os.path.abspath(f) for f in ctx.workdir.rglob("*"))
            with _probe:
                return _spec.run(ctx)
        p = {"mc": 0, "reuse": False, "auto": False, "attempts": 1, "layout_search": False, "seeds": 4,
             "iters": 30000, "passes": 30, "retry_layout": False, "k": int(k or 1), "used": [1], "n": 1,
             "cutoff": None, "hopeless": False, "fixed_all": False, "over": False, "budget": 0.0, **(params or {})}
        inp = StageInput(run_id=f"guard-{st}", stage=stage, design=design_ref, project=project_ref,
                         upstream={u: results[u] for u in specmod.upstream_keys(spec, results)},
                         state={s: None for s in spec.state_in}, settings=snap,
                         params={k: p[k] for k in spec.params})
        scratch = proj.parent / "scratch"
        res = execute_stage(dataclasses.replace(spec, run=run), inp, store, cache, scratch, rebuild=True,
                            keep_scratch=True)
        monkeypatch.setattr(settings_mod.Settings, "from_snapshot", classmethod(orig))
        assert res.verdict in ("pass", "fail"), (st, res.error)
        results[st] = res
        work = next((scratch / inp.run_id).iterdir())
        sproj = work / proj.name
        g = Guard(spec, work, sproj, pre, probe, _upstream_paths(spec, inp, sproj / "build", d0.name))
        groups = g.check(probe.subprocess_paths(), referenced_paths(sproj / "build", pre), probe.settings)
        assert not g.problems, f"{st}: under-declared inputs:\n  " + "\n  ".join(sorted(set(g.problems)))
        # every input read must move the key
        d = Design.load(sproj / "design.yaml")
        k0 = cache_key(spec, inp, tool_versions=tools, design=d)
        assert k0 == res.cache_key
        targets = sorted(g.project_reads | g.shared_reads)
        stuck = []
        for t in targets:
            tp = Path(t)
            if tp.is_dir() and t in g.shared_reads and not any(x.startswith(t + "/") is False for x in g.shared):
                continue
            if any(t == os.path.abspath(r) for r in (g.paths.library_dir, g.paths.blocks_dir)):
                continue
            if _under(t, sproj):
                rel = os.path.relpath(t, sproj)
                if not any(rel == x.rstrip("/") or (x.endswith("/") and rel.startswith(x)) or rel in
                           ("design.yaml", "requirements.yaml") for x in g.declared):
                    continue  # a parent folder of a declared entry: its entries are tested themselves
            elif not any(t == x or t.startswith(x + "/") for x in g.shared):
                continue
            backup = {"existed": tp.exists(), "bytes": tp.read_bytes() if tp.is_file() else None}
            _mutate(tp, os.path.relpath(t, sproj) + ("/" if tp.is_dir() else ""))
            try:
                d = Design.load(sproj / "design.yaml")
                if cache_key(spec, inp, tool_versions=tools, design=d) == k0:
                    stuck.append(t)
            finally:
                _restore(tp, backup)
        assert not stuck, f"{st}: changing these inputs does not change the cache key: {stuck}"
        for gname in groups:
            snap2 = {**snap, gname: {**snap[gname], "guard_probe": 1}}
            assert cache_key(spec, dataclasses.replace(inp, settings=snap2), tool_versions=tools, design=d) != k0
        report[st] = {"project": sorted(os.path.relpath(t, sproj) for t in g.project_reads if _under(t, sproj)),
                      "shared_probes": len(g.shared_reads), "mutated": len(targets), "settings": sorted(groups),
                      "python_env": len(g.env_reads)}
        shutil.rmtree(scratch, ignore_errors=True)
    return report


@pytest.mark.skipif(not (HAVE_KICAD and HAVE_NGSPICE), reason="needs kicad-cli, the KiCad libraries and ngspice")
def test_declared_inputs_cover_what_check_sch_sim_read(guard_env, monkeypatch, capsys):
    rep = _guard_stages(guard_env, monkeypatch, ["check", "sch", "sim"])
    with capsys.disabled():
        print("\nguard:", rep)


@pytest.mark.slow
@pytest.mark.skipif(not (HAVE_KICAD and HAVE_NGSPICE and HAVE_ROUTER), reason="needs KiCad, ngspice and a router")
def test_declared_inputs_cover_what_layout_post_fab_read(guard_env, monkeypatch, capsys):
    rep = _guard_stages(guard_env, monkeypatch, ["check", "sch", "sim", "layout_attempt.1", "layout_select", "post",
                                                  "fab"])
    with capsys.disabled():
        print("\nguard:", rep)


@pytest.mark.skipif(not HAVE_KICAD, reason="needs the KiCad libraries")
def test_the_guard_catches_an_undeclared_read(guard_env, monkeypatch):
    """A stage that reads a project file and a settings group it does not declare fails the guard."""
    (guard_env / "notes").mkdir(exist_ok=True)
    (guard_env / "notes" / "secret.txt").write_text("x")

    def wrap(spec):
        def run(ctx):
            (ctx.design.dir / "notes" / "secret.txt").read_text()
            settings_mod.get_settings().repair.budget_s  # noqa: B018
            return spec.run(ctx)
        return dataclasses.replace(spec, run=run, settings_groups=())
    with pytest.raises(AssertionError) as e:
        _guard_stages(guard_env, monkeypatch, ["check"], wrap=wrap)
    assert "notes/secret.txt" in str(e.value) and "'repair'" in str(e.value)
