import shutil
import subprocess

import pytest

from hwflow import pcb, placer
from hwflow.design import Design
from hwflow.libs import Libs

EX = "examples/sensor_preamp"
needs_router = pytest.mark.skipif(not (pcb.KRT_DIR / "py_router" / "route.py").exists(), reason="KiCadRoutingTools not installed")


def _project(tmp_path, clearance=2.5):
    proj = tmp_path / "p"
    shutil.copytree(EX, proj)
    f = proj / "design.yaml"
    f.write_text(f.read_text().replace("AMP_OUT: {class: signal}", f"AMP_OUT: {{class: signal, clearance_mm: {clearance}}}")
                 .replace("board: {", "board: {isolation: [{name: amp, nets: AMP_OUT, clearance_mm: %s}], " % clearance))
    return Design.load(proj)


@needs_router
def test_router_pass_sees_the_drc_rules(tmp_path, monkeypatch):
    d = _project(tmp_path)
    libs = Libs()
    parts = placer.build_parts(d, libs)
    (tmp_path / "out").mkdir()
    seen = []

    def fake_run(args, **kw):
        board = next(a for a in args if str(a).endswith(".kicad_pcb"))
        sib = {s: (board.rsplit(".", 1)[0] + s) for s in (".kicad_pro", ".kicad_dru")}
        from pathlib import Path
        seen.append({s: Path(p).read_text() for s, p in sib.items() if Path(p).exists()})
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(pcb.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError):
        pcb.run_krt(d, libs, parts, tmp_path / "out")
    assert seen, "router was not called"
    for files in seen:
        assert '"clearance": 2.5' in files[".kicad_pro"]
        assert "amp clearance" in files[".kicad_dru"]


@needs_router
def test_large_class_clearance_has_no_drc_clearance_errors(tmp_path):
    from hwflow.cli import stage_layout
    d = _project(tmp_path, 1.2)
    d.raw["board"].update(width_mm=60, height_mm=45)
    res = stage_layout(d, Libs(), seeds=1, iters=3000)
    # copper the router drew against the 1.2 mm class; pad-to-pad distances are the placer's, other errors are not this test's
    bad = [x for x in res["drc"] if x["sev"] == "error" and "amp " in x["msg"]
           and any(i.startswith(("Track", "Via")) for i in x["items"])]
    assert bad == [], "\n".join(str(x) for x in bad)
