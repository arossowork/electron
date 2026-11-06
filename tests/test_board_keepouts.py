"""`board.keepouts`: design-level rule areas (antenna keep-out on every layer, a pour-free band along an edge)."""
import yaml

from hwflow.core import sexpr
from hwflow.core.design import Design
from hwflow.core.libs import Libs
from hwflow.layout.board import pcb
from hwflow.layout.place import barrier, placer
from hwflow.verify.checks import check

COMPS = {
    "R1": {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0805_2012Metric",
           "pins": {"1": "A", "2": "GND"}, "placement": {"x": 10, "y": 10, "rot": 0, "fixed": True}},
}


def _design(tmp_path, keepouts, layers=4):
    raw = {"project": "ko", "board": {"width_mm": 30, "height_mm": 20, "layers": layers, "gnd_pour": True,
                                      "keepouts": keepouts},
           "nets": {"GND": {"class": "ground"}}, "components": COMPS}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    d = Design.load(tmp_path)
    return d, Libs(design=d)


def _zones(tree):
    return [z for z in tree if isinstance(z, list) and z and z[0] == "zone" and sexpr.find(z, "keepout")]


def test_board_keepouts_become_rule_areas_on_the_board(tmp_path):
    d, libs = _design(tmp_path, [
        {"name": "antenna", "rect": [20, 0, 30, 6]},                                       # all copper layers
        {"name": "edge band", "poly": [[0, 18], [30, 18], [30, 20], [0, 20]], "layers": ["In1.Cu", "B.Cu"],
         "forbids": ["copperpour"]},
    ])
    parts = placer.build_parts(d, libs)
    tree = pcb.build_board(d, libs, parts)
    ant, band = _zones(tree)
    assert str(sexpr.value(ant, "name", "")) == "antenna"
    assert [str(x) for x in sexpr.find(ant, "layers")[1:]] == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    rules = {r[0]: r[1] for r in sexpr.find(ant, "keepout")[1:]}
    assert rules == {"tracks": "not_allowed", "vias": "not_allowed", "pads": "allowed",
                     "copperpour": "not_allowed", "footprints": "allowed"}
    assert [str(x) for x in sexpr.find(band, "layers")[1:]] == ["In1.Cu", "B.Cu"]
    assert {r[0]: r[1] for r in sexpr.find(band, "keepout")[1:]}["tracks"] == "allowed"
    # the router and the corridor proofs see the same areas (barrier model)
    m = barrier.Model(d, libs, parts)
    assert {a["name"] for a in m.areas if a["owner"] is None} >= {"antenna", "edge band"}
    assert not [f for f in check(d, libs) if f["code"] == "board-keepout"]


def test_inner_layer_keepout_on_a_two_layer_board_is_dropped(tmp_path):
    d, libs = _design(tmp_path, [{"name": "inner", "rect": [0, 0, 5, 5], "layers": ["In1.Cu"]}], layers=2)
    assert pcb.board_keepout_zones(d) == []


def test_malformed_board_keepouts_are_check_errors(tmp_path):
    d, libs = _design(tmp_path, [{"name": "a"}, {"name": "b", "rect": [5, 0, 1, 3], "layers": ["F.Silk"]},
                                 {"name": "c", "rect": [0, 0, 1, 1], "forbids": ["holes"]}])
    msgs = [f["msg"] for f in check(d, libs) if f["code"] == "board-keepout"]
    assert len(msgs) == 4
    assert any("exactly one of rect" in m for m in msgs) and any("x1 > x0" in m for m in msgs)
    assert any("F.Silk" in m for m in msgs) and any("holes" in m for m in msgs)
