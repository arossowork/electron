"""ADR 0001, 2.5: a cold build and a fully cached build of examples/sensor_preamp give the same StageResults except
`cached` and `seconds`; `--rebuild` runs every stage again; a stage that raises gives verdict `error`."""
import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from hwflow.artifacts import LocalCAS
from hwflow.core import settings as settings_mod
from hwflow.core.design import Design
from hwflow.layout.route import krt
from hwflow.pipeline import runner
from hwflow.pipeline.exec import STATUS_KEY, execute_stage
from hwflow.pipeline.runner import BuildOptions, put_project, run_build
from hwflow.pipeline.spec import STAGE_SPECS, LocalStageCache, StageInput

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sensor_preamp"
S = settings_mod.get_settings()
HAVE_TOOLS = bool(S.tools.kicad_cli) and bool(S.paths.kicad_share) and shutil.which(S.tools.ngspice) is not None
HAVE_ROUTER = (krt.krt_dir() / "py_router" / "route.py").exists() or Path(krt.freerouting_jar()).exists()


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("HWFLOW_CAS_DIR", str(tmp_path / "cas"))
    p = tmp_path / "sensor_preamp"
    shutil.copytree(EXAMPLE, p, ignore=shutil.ignore_patterns("build"))
    return p


def _results(monkeypatch) -> list:
    got = []
    real = runner.execute_stage

    def spy(*a, **kw):
        r = real(*a, **kw)
        got.append(r)
        return r
    monkeypatch.setattr(runner, "execute_stage", spy)
    return got


def _norm(rs):
    return [dataclasses.replace(r, cached=False, seconds=0.0) for r in rs]


def _cold_then_cached(proj, monkeypatch, until):
    got = _results(monkeypatch)
    run_build(Design.load(proj / "design.yaml"), BuildOptions(until=until))
    cold = list(got)
    status1 = json.loads((proj / "build" / "status.json").read_text())
    got.clear()
    shutil.rmtree(proj / "build" / "sim", ignore_errors=True)  # an output deleted by hand comes back from the store
    run_build(Design.load(proj / "design.yaml"), BuildOptions(until=until))
    warm = list(got)
    assert [r.cached for r in cold] == [False] * len(cold) and [r.cached for r in warm] == [True] * len(warm)
    assert _norm(cold) == _norm(warm)
    status2 = json.loads((proj / "build" / "status.json").read_text())
    for st in [r.stage for r in cold]:
        if st == "layout_attempt":
            continue  # a candidate board: only layout_select (status.json key `layout`) has an entry (#46)
        key = STATUS_KEY.get(st, st)
        assert key in status1, f"stage {st} has no status.json entry {key}"
        assert status1[key] == status2[key]  # skipped stages keep their entry
    assert (proj / "build" / "sim").is_dir()
    return got


@pytest.mark.skipif(not HAVE_TOOLS, reason="needs kicad-cli, the KiCad libraries and ngspice")
def test_cold_equals_cached_until_sim(proj, monkeypatch, capsys):
    got = _cold_then_cached(proj, monkeypatch, "sim")
    assert "[sim] skipped: passed last time with the same inputs" in capsys.readouterr().out
    got.clear()
    run_build(Design.load(proj / "design.yaml"), BuildOptions(until="sim", rebuild=True))  # --rebuild runs them all
    assert [r.cached for r in got] == [False, False, False]
    assert "skipped" not in capsys.readouterr().out
    # an edit of requirements prose keeps every key; an edit of a value changes them
    rq = proj / "requirements.yaml"
    rq.write_text(rq.read_text().replace("text:", "text: (edited)", 1))
    got.clear()
    run_build(Design.load(proj / "design.yaml"), BuildOptions(until="sim"))
    assert [r.cached for r in got] == [True, True, True]
    assert not (proj / "build" / "work").exists()  # scratch dirs are removed


@pytest.mark.slow
@pytest.mark.skipif(not (HAVE_TOOLS and HAVE_ROUTER), reason="needs KiCad, ngspice and a router")
def test_cold_equals_cached_full_build(proj, monkeypatch):
    _cold_then_cached(proj, monkeypatch, "fab")


def test_a_stage_that_raises_gives_verdict_error_and_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("HWFLOW_CAS_DIR", str(tmp_path / "cas"))
    p = tmp_path / "sensor_preamp"
    shutil.copytree(EXAMPLE, p, ignore=shutil.ignore_patterns("build"))
    d = Design.load(p / "design.yaml")
    store = LocalCAS(tmp_path / "cas")
    cache = LocalStageCache(store.root)

    def boom(ctx):
        print("about to fail")
        raise RuntimeError("tool crashed")
    spec = dataclasses.replace(STAGE_SPECS["check"], run=boom)
    inp = StageInput(run_id="r1", stage="check", design=store.put_file(d.path), project=put_project(store, d),
                     settings=settings_mod.get_settings().snapshot())
    res = execute_stage(spec, inp, store, cache, tmp_path / "scratch")
    assert res.verdict == "error" and res.error["type"] == "StageException" and "tool crashed" in res.error["message"]
    assert "logs" in res.outputs and cache.get("check", res.cache_key) is None  # errors are never cached
    from hwflow.artifacts import read_tree
    assert "stdout.txt" in read_tree(store, res.outputs["logs"])
    assert not (tmp_path / "scratch" / "r1").exists()
