"""End-to-end build of the reference design: zero ERC/DRC errors and full routing."""
import json
import shutil

import pytest

from hwflow.cli import main


@pytest.mark.slow
def test_full_build(tmp_path):
    proj = tmp_path / "sensor_preamp"
    shutil.copytree("examples/sensor_preamp", proj, ignore=shutil.ignore_patterns("build"))
    main(["build", str(proj)])
    score = json.loads((proj / "build" / "status.json").read_text())["score"]
    assert score["check_errors"] == 0
    assert score["erc_errors"] == 0
    assert score["drc_errors"] == 0
    assert score["unrouted"] == 0
    assert score["sim_measures"] >= 8


@pytest.mark.slow
def test_full_build_krt(tmp_path):
    """Same reference design routed with KiCadRoutingTools: clean DRC, full routing, no vias in pads."""
    from hwflow.pcb import KRT_DIR
    if not (KRT_DIR / "py_router" / "route.py").exists():
        pytest.skip("KiCadRoutingTools not installed")
    proj = tmp_path / "sensor_preamp"
    shutil.copytree("examples/sensor_preamp", proj, ignore=shutil.ignore_patterns("build"))
    text = (proj / "design.yaml").read_text().replace("optimize_area: true}", "optimize_area: true, router: krt}")
    (proj / "design.yaml").write_text(text)
    main(["build", str(proj), "--until", "post"])
    status = json.loads((proj / "build" / "status.json").read_text())
    score = status["score"]
    assert score["drc_errors"] == 0 and score["unrouted"] == 0
    assert score["post_pass_ratio"] == 1.0
    assert not [c for c in status["post"]["checks"] if c["code"] == "via-in-pad"]
