"""layout_checks: geometric requirement checks measured on the built board and wired into `hwflow trace`."""

import json

import yaml

from hwflow.core.design import Design
from hwflow.pipeline import _design_hash, trace
from hwflow.verify import geomchecks

PCB = """(kicad_pcb (version 20241229)
 (net 0 "") (net 1 "AC") (net 2 "GND")
 (gr_rect (start 0 0) (end 60 40) (layer "Edge.Cuts") (width 0.1))
 (footprint "x:ANT" (layer "F.Cu") (at 5 5)
  (property "Reference" "U1")
  (fp_rect (start -3 -3) (end 3 3) (layer "F.CrtYd"))
  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 2 "GND")))
 (footprint "x:K" (layer "F.Cu") (at 40 20)
  (property "Reference" "K1")
  (fp_rect (start -4 -4) (end 4 4) (layer "F.CrtYd"))
  (pad "1" thru_hole circle (at 0 0) (size 2 2) (drill 1) (layers "*.Cu") (net 1 "AC")))
 (footprint "x:R" (layer "B.Cu") (at 20 30)
  (property "Reference" "R1")
  (pad "1" smd rect (at 0 0) (size 1 1) (layers "B.Cu") (net 2 "GND")))
 (segment (start 40 20) (end 40 38) (width 1) (layer "F.Cu") (net 1 "AC"))
 (segment (start 20 30) (end 30 30) (width 0.25) (layer "B.Cu") (net 2 "GND"))
)
"""


def project(tmp_path, checks, reqs=None):
    raw = {"project": "gc", "board": {"width_mm": 60, "height_mm": 40, "layers": 2},
           "nets": {"AC": {"class": "mains"}, "GND": {"class": "ground"}},
           "components": {"U1": {"role": "antenna", "pins": {"1": "GND"}},
                          "K1": {"pins": {"1": "AC"}}, "R1": {"pins": {"1": "GND"}}},
           "layout_checks": checks}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    (tmp_path / "build").mkdir(exist_ok=True)
    (tmp_path / "gc.kicad_pcb").write_text(PCB)
    (tmp_path / "requirements.yaml").write_text(yaml.safe_dump({"requirements": reqs or []}))
    return Design.load(tmp_path)


def run(tmp_path, check):
    d = project(tmp_path, [check])
    assert geomchecks.validate(d) == []
    return geomchecks.evaluate(d, tmp_path / "gc.kicad_pcb")[0]


def test_outline_max(tmp_path):
    assert run(tmp_path, {"kind": "outline_max", "width_mm": 60, "height_mm": 40})["ok"]
    r = run(tmp_path, {"kind": "outline_max", "width_mm": 59, "height_mm": 40})
    assert not r["ok"] and r["value"] == [60, 40]
    assert run(tmp_path, {"kind": "outline_max", "width_mm": 40, "height_mm": 60, "rotate_ok": True})["ok"]


def test_min_distance_parts_nets_and_areas(tmp_path):
    ok = run(tmp_path, {"kind": "min_distance", "from": {"role": "antenna"}, "to": {"parts": ["K1"]}, "min_mm": 25})
    assert ok["ok"] and abs(ok["value"] - 29.12) < 0.01   # courtyard corners: dx 28, dy 8
    bad = run(tmp_path, {"kind": "min_distance", "from": {"role": "antenna"}, "to": {"net_class": "mains"}, "min_mm": 40})
    assert not bad["ok"]
    area = run(tmp_path, {"kind": "min_distance", "from": {"rect": [0, 0, 10, 10]}, "to": {"nets": ["AC"]}, "min_mm": 20})
    assert area["ok"] and area["value"] > 25
    assert not run(tmp_path, {"kind": "min_distance", "from": {"rect": [0, 0, 10, 10]}, "to": {"nets": ["AC"]}, "min_mm": 40})["ok"]


def test_keepout_free(tmp_path):
    free = run(tmp_path, {"kind": "keepout_free", "area": {"rect": [0, 20, 15, 40]}})
    assert free["ok"]
    hit = run(tmp_path, {"kind": "keepout_free", "area": {"rect": [35, 30, 45, 40]}})
    assert not hit["ok"] and hit["value"] > 0 and "AC" in hit["detail"]
    assert run(tmp_path, {"kind": "keepout_free", "area": {"rect": [35, 30, 45, 40]}, "except_nets": ["AC"]})["ok"]
    assert run(tmp_path, {"kind": "keepout_free", "area": {"rect": [35, 30, 45, 40]}, "layers": ["B.Cu"]})["ok"]  # AC track is on F.Cu
    assert not run(tmp_path, {"kind": "keepout_free", "area": {"parts": ["R1"], "margin_mm": 1}, "layers": ["B.Cu"]})["ok"]


def test_side_only(tmp_path):
    r = run(tmp_path, {"kind": "side_only", "side": "front", "parts": ["smd"]})
    assert not r["ok"] and "R1" in r["detail"] and "U1" not in r["detail"]
    assert run(tmp_path, {"kind": "side_only", "side": "front", "parts": ["U*", "K1"]})["ok"]
    assert run(tmp_path, {"kind": "side_only", "side": "back", "parts": ["R1"]})["ok"]


def test_validate_flags_bad_entries(tmp_path):
    d = project(tmp_path, [{"kind": "nope"}, {"kind": "outline_max", "width_mm": -1, "height_mm": 3},
                           {"kind": "min_distance", "from": {"parts": ["Z9"]}, "to": {"nets": ["X"]}, "min_mm": 1},
                           {"kind": "side_only", "side": "top", "parts": []}])
    msgs = " ".join(geomchecks.validate(d))
    for frag in ("kind 'nope'", "width_mm", "no part matches 'Z9'", "unknown net 'X'", "side must be"):
        assert frag in msgs


def test_trace_verifies_and_fails_layout_requirements(tmp_path):
    checks = [{"name": "outline", "kind": "outline_max", "width_mm": 60, "height_mm": 40, "req": "R1"},
              {"name": "smd-top", "kind": "side_only", "side": "front", "parts": ["smd"], "req": "R2"}]
    reqs = [{"id": "R1", "verification": "layout", "text": "size"}, {"id": "R2", "verification": "layout", "text": "side"},
            {"id": "R3", "verification": "layout", "text": "no check"}]
    d = project(tmp_path, checks, reqs)
    geo = geomchecks.evaluate(d, tmp_path / "gc.kicad_pcb")
    (tmp_path / "build" / "status.json").write_text(json.dumps({"post": {"geometry": geo, "design_hash": _design_hash(d)}}))
    rows = {r["id"]: r for r in trace(d)["rows"]}
    assert rows["R1"]["status"] == "pass" and rows["R1"]["measures"][0].startswith("outline_max outline 60x40/60x40")
    assert rows["R2"]["status"] == "FAIL"
    assert rows["R3"]["status"] == "UNVERIFIED"
