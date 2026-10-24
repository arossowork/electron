"""execute_stage with relative paths (the worker image ran a stage child with a relative scratch dir): the stage
works on absolute paths, whatever the caller passed and whatever directory its tools run in."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hwflow.artifacts import LocalCAS
from hwflow.core import settings as settings_mod
from hwflow.core.design import Design
from hwflow.pipeline.exec import execute_stage
from hwflow.pipeline.runner import put_project
from hwflow.pipeline.spec import STAGE_SPECS, LocalStageCache, StageInput

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sensor_preamp"
HAVE_KICAD = shutil.which(settings_mod.get_settings().tools.kicad_cli) is not None


@pytest.mark.skipif(not HAVE_KICAD, reason="needs kicad-cli and the KiCad libraries")
def test_check_and_sch_with_relative_scratch_cas_and_cache(tmp_path, monkeypatch):
    shutil.copytree(EXAMPLE, tmp_path / "sensor_preamp")
    monkeypatch.chdir(tmp_path)
    d = Design.load(Path("sensor_preamp") / "design.yaml")
    store = LocalCAS("cas")
    cache = LocalStageCache(store.root)
    snap = settings_mod.get_settings().snapshot()
    design = store.put_file(d.path, name="design.yaml")
    project = put_project(store, d)
    results = {}
    for st in ("check", "sch"):
        spec = STAGE_SPECS[st]
        inp = StageInput(run_id="rel", stage=st, design=design, project=project,
                         upstream={u: results[u] for u in spec.upstream if u in results}, settings=snap)
        res = execute_stage(spec, inp, store, cache, Path("scratch"), tool_cache_dir=Path("toolcache"),
                            rebuild=True)
        assert res.verdict == "pass", (st, res.error, res.summary)
        results[st] = res


@pytest.mark.skipif(not HAVE_KICAD, reason="needs the KiCad libraries")
def test_a_stage_child_returns_its_result(tmp_path):
    """The inline runner's stage child (`python -m hwflow.pipeline.exec`, used for layout attempts 2..): its
    StageResult comes back as the result, not as a crash."""
    import time

    from hwflow.pipeline.runner import _Child

    shutil.copytree(EXAMPLE, tmp_path / "sensor_preamp")
    d = Design.load(tmp_path / "sensor_preamp" / "design.yaml")
    store = LocalCAS(tmp_path / "cas")
    inp = StageInput(run_id="child", stage="check", design=store.put_file(d.path, name="design.yaml"),
                     project=put_project(store, d), settings=settings_mod.get_settings().snapshot())
    c = _Child("check", STAGE_SPECS["check"], inp, tmp_path / "job", store.root, tmp_path / "toolcache", True)
    t0 = time.time()
    while (res := c.poll(store)) is None:
        assert time.time() - t0 < 120
        time.sleep(0.2)
    assert res.verdict == "pass" and res.error is None and res.cache_key
