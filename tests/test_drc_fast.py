
from hwflow.core.settings import get_settings
import pytest

from hwflow.layout.board import kicad, pcb
from hwflow import placer
from hwflow.core.design import Design
from hwflow.core.libs import Libs

DRU = '''(version 1)
(rule "R8 mains-SELV clearance" (constraint clearance (min 4.0mm)) (condition "A.NetName == 'AC_*' && B.NetName != 'AC_*'"))
(rule "R8 mains-SELV creepage"
  (constraint creepage (min 6.0mm))
  (condition "A.NetName == 'AC_*' && B.NetName != 'AC_*'"))
(rule "gnd" (constraint clearance (min 0.3mm)))
'''


def test_without_creepage_drops_only_creepage_rules():
    out = kicad._without_creepage(DRU)
    assert "creepage" not in out
    assert out.count("(rule") == 2 and "(version 1)" in out and '"gnd"' in out and "mains-SELV clearance" in out
    assert kicad._without_creepage("(version 1)\n") == "(version 1)\n"


@pytest.mark.skipif(not bool(get_settings().tools.kicad_cli), reason="kicad-cli not installed")
def test_drc_fast_leaves_the_board_and_rules_alone(tmp_path):
    d = Design.load("examples/sensor_preamp")
    libs = Libs()
    parts = placer.build_parts(d, libs)
    b = tmp_path / "b.kicad_pcb"
    pcb.write_board(b, pcb.build_board(d, libs, parts))
    pcb.write_project(tmp_path, "b", d)
    b.with_suffix(".kicad_dru").write_text(DRU)
    before = b.read_bytes()
    res = kicad.drc_fast(b, refill=False)
    assert b.read_bytes() == before and b.with_suffix(".kicad_dru").read_text() == DRU
    assert not any(v["type"] == "creepage" for v in res)
    assert not [p for p in tmp_path.iterdir() if p.is_dir()]
