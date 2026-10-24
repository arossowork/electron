"""End-to-end build of the reference design: zero ERC/DRC errors and full routing."""
import json
import shutil
from pathlib import Path

import pytest

from hwflow import pcb
from hwflow.cli import main

needs_a_router = pytest.mark.skipif(
    not ((pcb.KRT_DIR / "py_router" / "route.py").exists() or Path(pcb.FREEROUTING).exists()),
    reason="neither KiCadRoutingTools nor the Freerouting jar is installed")


@needs_a_router
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
    from hwflow.pcb import krt_dir
    if not (krt_dir() / "py_router" / "route.py").exists():
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


def test_fab_positions_put_mirrored_back_footprints_on_the_bottom(tmp_path):
    """A `_back` footprint is the front one mirrored in x, on F.Cu with its body on B.* layers; KiCad's position file
    said top. It must say bottom, with the rotation KiCad writes for the part really flipped to B.Cu (r + 180, from
    pcbnew's Flip), the board X/Y unchanged; cpl.csv carries the JLCPCB columns, and THT parts are listed."""
    import csv

    from hwflow import kicad
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text('''(kicad_pcb (version 20241229)
 (footprint "x:Relay_back" (layer "F.Cu") (at 12.5 5.6 90) (property "Reference" "K1" (at 0 0 0) (layer "B.SilkS"))
  (attr through_hole) (fp_rect (start -12.25 -5.25) (end 12.25 5.25) (layer "B.CrtYd"))
  (pad "1" thru_hole circle (at 10.5 -3.75) (size 2 2) (drill 1) (layers "*.Cu")))
 (footprint "x:R" (layer "F.Cu") (at 30 20 0) (property "Reference" "R1" (at 0 0 0) (layer "F.SilkS"))
  (attr smd) (fp_rect (start -1 -0.5) (end 1 0.5) (layer "F.CrtYd"))
  (pad "1" smd rect (at -0.8 0) (size 0.6 0.6) (layers "F.Cu"))))''')
    pos = tmp_path / "positions.csv"
    pos.write_text('Ref,Val,Package,PosX,PosY,Rot,Side\n"K1","ADW1","Relay_back",12.500000,-5.600000,90.000000,top\n'
                   '"R1","1k","R",30.000000,-20.000000,0.000000,top\n')
    r = kicad.fix_back_positions(pcb, pos, tmp_path / "cpl.csv")
    assert r == {"bottom": ["K1"], "tht": ["K1"]}
    rows = {x["Ref"]: x for x in csv.DictReader(open(pos))}
    assert (rows["K1"]["Side"], float(rows["K1"]["Rot"]), float(rows["K1"]["PosX"])) == ("bottom", -90.0, 12.5)
    assert (rows["R1"]["Side"], float(rows["R1"]["Rot"])) == ("top", 0.0)
    cpl = list(csv.reader(open(tmp_path / "cpl.csv")))
    assert cpl[0] == ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"]
    assert cpl[1] == ["K1", "12.5000mm", "-5.6000mm", "Bottom", "-90"]
