"""Annular-ring repair (layout/board/padring.py): a plated pad whose ring is under the board minimum grows to it when
the grown copper keeps its clearances, the placer and the board writer take the grown size, and a pad that cannot
grow, a ringless hole and a hole inside a larger pad of the same number stay as they are."""
import pytest
import yaml

from hwflow.core import sexpr
from hwflow.core.design import Design
from hwflow.core.libs import Libs
from hwflow.layout.board import padring, pcb
from hwflow.layout.place import placer

# a module's mounting holes: a 3.1 mm pad on a 3.0 mm drill (ring 0.05 mm), and two oval pins 1.2 x 2 mm on a 1.016 mm
# drill (ring 0.092 mm) 2.54 mm apart
MOD = """(footprint "MOD"
  (fp_rect (start -6 -4) (end 6 4) (layer "F.CrtYd") (stroke (width 0.05) (type solid)))
  (pad "" thru_hole circle (at -4 0) (size 3.1 3.1) (drill 3) (layers "*.Cu" "*.Mask"))
  (pad "1" thru_hole oval (at 1 0) (size 1.2 2) (drill 1.016) (layers "*.Cu" "*.Mask"))
  (pad "2" thru_hole oval (at 3.54 0) (size 1.2 2) (drill 1.016) (layers "*.Cu" "*.Mask"))
)
"""
# two pins whose grown copper would come within the clearance of each other (1.3 mm apart)
TIGHT = """(footprint "TIGHT"
  (fp_rect (start -2 -2) (end 2 2) (layer "F.CrtYd") (stroke (width 0.05) (type solid)))
  (pad "1" thru_hole circle (at 0 0) (size 1.1 1.1) (drill 1) (layers "*.Cu" "*.Mask"))
  (pad "2" thru_hole circle (at 1.3 0) (size 1.1 1.1) (drill 1) (layers "*.Cu" "*.Mask"))
)
"""
# a mounting hole ringed by small stitching holes inside its own large pad (all pad 1), and a ringless peg
STITCH = """(footprint "STITCH"
  (fp_rect (start -4 -4) (end 4 4) (layer "F.CrtYd") (stroke (width 0.05) (type solid)))
  (pad "1" thru_hole circle (at 0 0) (size 7 7) (drill 3.5) (layers "*.Cu" "*.Mask"))
  (pad "1" thru_hole circle (at 2.625 0) (size 0.6 0.6) (drill 0.5) (layers "*.Cu" "*.Mask"))
  (pad "" thru_hole circle (at 0 5) (size 1 1) (drill 1) (layers "*.Cu" "*.Mask"))
)
"""


def _design(tmp_path, comps, w=30.0, h=20.0):
    d = tmp_path / "lib" / "t.pretty"
    d.mkdir(parents=True, exist_ok=True)
    for name, text in (("MOD", MOD), ("TIGHT", TIGHT), ("STITCH", STITCH)):
        (d / f"{name}.kicad_mod").write_text(text)
    (tmp_path / "design.yaml").write_text(yaml.safe_dump({
        "project": "t", "board": {"width_mm": w, "height_mm": h, "layers": 2, "clearance_mm": 0.2,
                                  "via_dia_mm": 0.6, "via_drill_mm": 0.3},
        "nets": {"A": {"class": "signal"}, "B": {"class": "signal"}}, "components": comps}))
    return Design.load(tmp_path)


def _comp(fp, pins=None, **extra):
    return {"value": "x", "symbol": "Device:R", "footprint": f"t:{fp}", "pins": pins or {}, **extra}


def test_ring_and_grown_size():
    assert padring.ring(1.2, 2.0, (1.016, 1.016, 0, 0)) == pytest.approx(0.092)
    assert padring.ring(1.0, 1.0, (0.6, 0.6, 0.1, 0)) == pytest.approx(0.1)  # the offset takes from one side
    assert padring.grown_size("circle", 3.1, 3.1, (3.0, 3.0, 0, 0), 0.11) == (3.22, 3.22)
    assert padring.grown_size("oval", 1.2, 2.0, (1.016, 1.016, 0, 0), 0.11) == (1.236, 2.0)  # only the thin axis


def test_thin_rings_grow_to_the_minimum_and_reach_the_placer_and_the_board(tmp_path):
    d = _design(tmp_path, {"U1": _comp("MOD", {"1": "A", "2": "B"})})
    libs = Libs(design=d)
    p = padring.plan(d, libs)
    assert p["min_ring"] == 0.1 and not p["left"]
    assert [(r["pad"], r["new"]) for r in p["rows"]] == [("", [3.22, 3.22]), ("1", [1.236, 2.0]), ("2", [1.236, 2.0])]
    assert all(r["ring_new"] >= 0.11 - 1e-9 for r in p["rows"])
    part = placer.build_parts(d, libs)["U1"]
    assert part.pad_size["1"] == pytest.approx((1.236, 2.0))
    part.x, part.y = 15, 10
    tree = pcb.build_board(d, libs, {"U1": part})
    fp = next(x for x in tree if isinstance(x, list) and x[0] == "footprint")
    sizes = [tuple(float(v) for v in sexpr.find(q, "size")[1:3]) for q in sexpr.find_all(fp, "pad")]
    assert sizes == [(3.22, 3.22), (1.236, 2.0), (1.236, 2.0)]
    assert any("U1 pad 1: 1.2 x 2 -> 1.236 x 2 mm" in line for line in padring.summary(p))


def test_a_pad_that_would_come_within_the_clearance_stays_and_is_listed(tmp_path):
    d = _design(tmp_path, {"J1": _comp("TIGHT", {"1": "A", "2": "B"})})
    p = padring.plan(d, Libs(design=d))
    assert not p["rows"]
    assert [(r["pad"], "clearance" in r["why"]) for r in p["left"]] == [("1", True), ("2", True)]
    d2 = _design(tmp_path / "same", {"J1": _comp("TIGHT", {"1": "A", "2": "A"})})  # one net: no clearance between
    assert len(padring.plan(d2, Libs(design=d2))["rows"]) == 2


def test_a_part_at_a_given_position_keeps_the_edge_distance(tmp_path):
    # U1 at x = 5.85 puts the 3.1 mm mounting pad (at -4) 0.3 mm from the left edge, the grown one 0.24 mm: inside
    # the 0.3 mm copper-to-edge rule; the pins, far from the edge, grow
    d = _design(tmp_path, {"U1": _comp("MOD", {"1": "A", "2": "B"}, placement={"x": 5.85, "y": 10})})
    p = padring.plan(d, Libs(design=d))
    assert [r["pad"] for r in p["rows"]] == ["1", "2"]
    assert [(r["pad"], "edge" in r["why"]) for r in p["left"]] == [("", True)]


def test_holes_inside_a_larger_pad_and_ringless_pegs_are_left_alone(tmp_path):
    d = _design(tmp_path, {"H1": _comp("STITCH", {"1": "A"})})
    p = padring.plan(d, Libs(design=d))
    assert not p["rows"] and not p["left"]
