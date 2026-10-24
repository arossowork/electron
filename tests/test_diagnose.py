"""Layout diagnosis: which parts the failures stay on, the escape of fine-pitch pads, and when the layout cycle is
blocked and sent back to an earlier stage."""
from types import SimpleNamespace

import yaml

from hwflow import diagnose
from hwflow.core.design import Design


def _design(tmp_path, nets=None, fp="Package_TO_SOT_SMD:SOT-563", at=None):
    comps = {"U2": {"footprint": fp, "pins": {"1": "VIN", "2": "SW", "3": "VIN", "4": "GND", "5": "VOUT", "6": "GND"}},
             "C1": {"footprint": "Capacitor_SMD:C_0402_1005Metric", "pins": {"1": "VIN", "2": "GND"}}}
    if at:
        comps["U2"]["at"] = at
    raw = {"project": "dg", "board": {"layers": 4, "width_mm": 40, "height_mm": 30, "track_mm": 0.2,
                                      "clearance_mm": 0.2},
           "nets": nets or {"SW": {"width_mm": 0.6}, "VOUT": {"width_mm": 0.4}}, "components": comps}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    return Design.load(tmp_path)


def _pad(n, net, x, y, w=0.34, h=0.7):
    return {"number": n, "net": net, "x": x, "y": y, "w": w, "h": h, "kind": "smd", "shape": "rect", "layers": ["F.Cu"]}


def _sot563(x0=10.0, y0=10.0):
    """SOT-563: two rows of 3 pads, 0.5 mm pitch, 0.34 x 0.7 mm pads, rows 1.5 mm apart."""
    return {"x": x0, "y": y0, "r": 0, "footprint": "Package_TO_SOT_SMD:SOT-563", "pads": [
        _pad("1", "VIN", x0 - 0.5, y0 + 0.75), _pad("2", "SW", x0, y0 + 0.75), _pad("3", "VIN", x0 + 0.5, y0 + 0.75),
        _pad("4", "GND", x0 + 0.5, y0 - 0.75), _pad("5", "VOUT", x0, y0 - 0.75), _pad("6", "GND", x0 - 0.5, y0 - 0.75)]}


def _board():
    return {"footprints": {"U2": _sot563(), "C1": {"x": 20, "y": 10, "r": 0, "footprint": "C_0402", "pads": [
        _pad("1", "VIN", 19.5, 10, 0.5, 0.5), _pad("2", "GND", 20.5, 10, 0.5, 0.5)]}}, "segments": [], "vias": []}


def _parts():
    box = lambda x0, y0, x1, y1: (lambda: (x0, y0, x1, y1))  # noqa: E731
    return {"U2": SimpleNamespace(box=box(9, 9, 11, 11)), "C1": SimpleNamespace(box=box(19, 9.5, 21, 10.5))}


def test_failures_are_put_on_the_parts_they_touch():
    drc = [{"sev": "error", "type": "unconnected_items", "items": ["Pad 2 [SW] of U2 on F.Cu", "Track [SW] on F.Cu"]},
           {"sev": "error", "type": "clearance", "items": ["Track [VIN] on F.Cu", "Track [SW] on F.Cu"],
            "at": [[19.6, 10.4], None]},                       # no pad named: the parts with a pad within 1.5 mm
           {"sev": "warning", "type": "silk_overlap", "items": ["Pad 1 [VIN] of C1 on F.Cu"]}]
    assert diagnose.failing_parts(drc, _board()) == {"U2": ["unconnected_items"], "C1": ["clearance"]}


def test_a_part_is_persistent_when_it_fails_in_most_attempts():
    per = [{"U2": ["x"], "C1": ["x"]}, {"U2": ["x"]}, {"U2": ["x"], "R1": ["x"]}, {"U2": ["x"], "C1": ["x"]}]
    assert diagnose.persistent(per) == ["U2"]               # C1 in 2 of 4 attempts: moves with the seed
    assert diagnose.persistent(per[:1]) == []                # one attempt is no evidence


def test_escape_of_a_fine_pitch_row():
    esc = diagnose.escape(_sot563(), {"SW": {"width": 0.6}, "VOUT": {"width": 0.4}, "VIN": {"width": 1.2}}, 0.2, 0.2)
    # pads 2 and 5 sit between foreign pads: 2 x (0.5 - 0.17 - 0.2) = 0.26 mm; pads 1, 3 (VIN, 1.2 mm) are row ends
    assert [(e["pad"], e["net"], e["escape"]) for e in esc] == [("2", "SW", 0.26), ("5", "VOUT", 0.26)]
    assert esc[0]["pitch"] == 0.5 and esc[0]["pitch_needed"] == 0.67   # 0.3 + 0.2 + 0.17
    assert diagnose.escape(_sot563(), {}, 0.2, 0.2) == []    # board-width tracks escape


def test_the_structure_key_ignores_placement_but_not_parts(tmp_path):
    k = diagnose.structure_key(_design(tmp_path))
    assert diagnose.structure_key(_design(tmp_path, at={"x": 5, "y": 5, "fixed": True})) == k
    assert diagnose.structure_key(_design(tmp_path, fp="Package_TO_SOT_SMD:SOT-23-6")) != k
    assert diagnose.structure_key(_design(tmp_path, nets={"SW": {"width_mm": 0.3}})) != k


def test_escape_limited_persistent_part_goes_back_to_parts_and_blocks(tmp_path):
    d = _design(tmp_path)
    out = tmp_path / "build"
    out.mkdir()
    per = [{"failing": {"U2": ["unconnected_items"]}} for _ in range(4)]
    res = diagnose.diagnose(d, _parts(), per, _board(), out)
    assert res["persistent"] == ["U2"] and res["back_to"] == "parts" and res["blocked"]
    assert "pitch >= 0.67 mm" in diagnose.summary(res) and "SW" in diagnose.summary(res)
    assert diagnose.blocked(d, out) is None                  # nothing recorded yet
    diagnose.record(out, {"structure": diagnose.structure_key(d), "ok": False, "persistent": res["persistent"],
                          "blocked": True, "diagnosis": res})
    assert diagnose.blocked(d, out)["diagnosis"]["back_to"] == "parts"
    # a new footprint is a new structure: not blocked
    assert diagnose.blocked(_design(tmp_path, fp="Package_TO_SOT_SMD:SOT-23-6"), out) is None
    # an override run resets the block for that structure
    d = _design(tmp_path)
    diagnose.record(out, {"structure": diagnose.structure_key(d), "ok": False, "persistent": [], "blocked": False,
                          "override": True, "diagnosis": {}})
    assert diagnose.blocked(d, out) is None


def test_moving_failures_allow_more_layout_and_a_repeat_does_not(tmp_path):
    d = _design(tmp_path, nets={"GND": {}})                  # no wide nets: no escape limit
    out = tmp_path / "build"
    out.mkdir()
    moving = [{"failing": {"U2": ["x"]}}, {"failing": {"C1": ["x"]}}, {"failing": {}}, {"failing": {"C1": ["x"]}}]
    res = diagnose.diagnose(d, _parts(), moving, _board(), out)
    assert res["back_to"] == "layout" and not res["blocked"] and "move between parts" in res["reasons"][0]
    stay = [{"failing": {"U2": ["x"]}} for _ in range(4)]
    res = diagnose.diagnose(d, _parts(), stay, _board(), out)
    assert res["back_to"] == "layout" and not res["blocked"]  # first time: one placement cycle is worth it
    diagnose.record(out, {"structure": diagnose.structure_key(d), "ok": False, "persistent": ["U2"], "blocked": False,
                          "diagnosis": res})
    res = diagnose.diagnose(d, _parts(), stay, _board(), out)
    assert res["back_to"] == "architecture" and res["blocked"] and res["repeat"]
