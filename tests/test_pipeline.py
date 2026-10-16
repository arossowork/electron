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
