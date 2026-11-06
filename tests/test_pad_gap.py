"""`hwflow check` pad-gap (layout.checks.pad_gap_findings): two pads of one footprint on different nets closer than
the clearance between them. pocket-recorder: a drawn SOT-883 put its lands at the lead pitch, 0.10 mm apart under a
0.15 mm clearance, and only the routed board's DRC found it."""

from hwflow.core.design import Design
from hwflow.core.libs import Libs
from hwflow.layout import checks
from hwflow.layout.board import pcb

YAML = """project: padgap
description: one transistor
board: {width_mm: 10, height_mm: 10, layers: 2, clearance_mm: 0.15}
nets:
  G: {class: signal}
  S: {class: signal}
  D: {class: signal}
components:
  M1: {footprint: "%s", pins: {"1": G, "2": S, "3": D}}
"""

DRAWN = """(footprint "SOT883_drawn" (layer "F.Cu")
  (pad "1" smd rect (at -0.35 -0.175) (size 0.25 0.25) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" smd rect (at -0.35 0.175) (size 0.25 0.25) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "3" smd rect (at 0.35 0) (size 0.25 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))
)
"""


def _findings(tmp_path, fp):
    (tmp_path / "design.yaml").write_text(YAML % fp)
    lib = tmp_path / "lib" / "footprints" / "t.pretty"
    lib.mkdir(parents=True)
    (lib / "SOT883_drawn.kicad_mod").write_text(DRAWN)
    d = Design.load(tmp_path)
    return checks.pad_gap_findings(d, Libs(design=d), d.nets(), pcb.cu_layers(2))


def test_drawn_land_pattern_under_the_clearance(tmp_path):
    f = _findings(tmp_path, "t:SOT883_drawn")
    assert len(f) == 1 and "pads 1 (G) and 2 (S) are 0.100 mm apart" in f[0] and "0.15 mm" in f[0]


def test_kicad_sot883_passes(tmp_path):
    assert _findings(tmp_path, "Package_TO_SOT_SMD:SOT-883") == []


def test_check_reports_it_as_a_warning(tmp_path):
    (tmp_path / "design.yaml").write_text(YAML % "t:SOT883_drawn")
    lib = tmp_path / "lib" / "footprints" / "t.pretty"
    lib.mkdir(parents=True)
    (lib / "SOT883_drawn.kicad_mod").write_text(DRAWN)
    d = Design.load(tmp_path)
    got = [x for x in checks.findings(d, Libs(design=d)) if x["code"] == "pad-gap"]
    assert [x["sev"] for x in got] == ["warn"]
