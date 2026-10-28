"""`hwflow floorplan --stage arch`: the stage-2 mechanical floorplan check (footprint and pin-row items drawn as parts
with real pads, fixed overlaps, through-hole pins under other parts, region fit, isolation corridors, area budget)."""
import shutil
from pathlib import Path

import yaml

from hwflow.core.design import Design
from hwflow.core.libs import Libs
from hwflow.layout import feasibility
from hwflow.layout.place import floorplan, fpsynth

FIX = Path(__file__).parent / "fixtures" / "arch_floorplan"


def arch(d):
    return floorplan.check(d, Libs(design=d), stage="arch", extra=feasibility.floorplan_checks)


def fails(res, name=None):
    return [c for c in res["checks"] if c["ok"] is False and (name is None or c["name"] == name)]


def fixture(tmp_path, name):
    shutil.copytree(FIX, tmp_path / "af")
    return Design.load(tmp_path / "af" / name)


def make(tmp_path, fp, board=None, nets=None, isolation=None):
    raw = {"project": "arch", "board": {"width_mm": 60, "height_mm": 40, "layers": 2, **(board or {})}, "floorplan": fp}
    if nets:
        raw["nets"] = nets
    if isolation:
        raw["board"]["isolation"] = isolation
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    return Design.load(tmp_path)


def test_e2e5_before_flags_holder_and_corridor(tmp_path):
    """thermo-e2e-5 stage 2/3: BT1 has no spot clear of the K1/K2 coil pins, and J1's OUT1/OUT2 corridor is closed
    between the cover-wall band and the bottom edge (found only in stage 4 in the timed run)."""
    res = arch(fixture(tmp_path, "e5_before"))
    assert res["verdict"] == "FAIL" and res["stage"] == "arch"
    fit = " | ".join(c["msg"] for c in fails(res, "fit"))
    assert "BT1" in fit and "K1.A1" in fit and "K2.A2" in fit
    cor = " | ".join(c["msg"] for c in fails(res, "proof-corridor"))
    assert "OUT1" in cor and "OUT2" in cor and "J1" in cor
    assert all(c.get("back_to") == "architecture" for c in fails(res, "fit") + fails(res, "proof-corridor"))
    assert res["seconds"] < 30
    assert not res["placement"]  # no quick placement in stage arch


def test_e2e5_after_passes(tmp_path):
    res = arch(fixture(tmp_path, "e5_after"))
    assert res["ok"], fails(res)
    assert res["drawn_parts"] == 8 and res["rest_items"] == ["rest_front"]
    room = {x["side"]: x for x in res["arch"]["room"]}
    assert room["front"]["rest_mm2"] == 1040 and room["front"]["left_mm2"] > 1040


def test_mirror_matches_back_footprint():
    libs = Libs()
    front = libs.footprint("hw:Relay_Panasonic_ADW1_1coil")
    back = libs.footprint("hw:Relay_Panasonic_ADW1_1coil_back")
    from hwflow.core import sexpr
    m = fpsynth.mirror(front["tree"])
    pads = {str(p[1]): tuple(float(v) for v in sexpr.find(p, "at")[1:3]) for p in sexpr.find_all(m, "pad")}
    assert pads == {p["number"]: (p["x"], p["y"]) for p in back["pads"]}
    layers = {str(sexpr.value(g, "layer", "")) for g in sexpr.find_all(m, "fp_rect")}
    assert "B.CrtYd" in layers and "F.CrtYd" not in layers


RELAY = "hw:Relay_Panasonic_ADW1_1coil_back"


def test_fixed_overlap_and_pins_under_other_face(tmp_path):
    fp = {"construction": "double", "items": [
        {"name": "k1", "refs": ["K1"], "footprint": RELAY, "at": [15, 10]},
        {"name": "k2", "refs": ["K2"], "footprint": RELAY, "at": [30, 12]},   # overlaps K1 on the back face
        {"name": "pad", "size_mm": [12, 8], "side": "front", "at": [10, 30],
         "pin_rows": [{"n": 2, "pitch": 5, "at": [-2.5, 0], "drill_mm": 1.0}]},
        {"name": "cover", "refs": ["H1"], "size_mm": [20, 6], "side": "back", "at": [10, 30],
         "pin_rows": [{"n": 1, "at": [8, 2]}]},
    ]}
    res = arch(make(tmp_path, fp))
    ov = fails(res, "overlap")
    assert len(ov) == 1 and "K1 and K2 overlap on the back face" in ov[0]["msg"]
    under = " | ".join(c["msg"] for c in fails(res, "tht-under"))
    assert "pad.1" in under and "under H1 on the back face" in under


def test_holder_fit_blocked_by_pins_then_free(tmp_path):
    def design(y):
        return make(tmp_path, {"construction": "double", "regions": [
            {"name": "cells", "kind": "battery", "side": "front", "rect": [2, 2, 58, 22], "items": ["holder"]}],
            "items": [{"name": "holder", "size_mm": [50, 16], "side": "front"},
                      {"name": "relay", "refs": ["K1"], "footprint": RELAY, "at": [30, y], "rot": 270}]})
    bad = arch(design(20))   # rot 270: the coil pins come through at y 20 - 10.5, inside the holder's region
    fit = " | ".join(c["msg"] for c in fails(bad, "fit"))
    assert "holder" in fit and "K1.A1" in fit
    assert not fails(arch(design(34)), "fit")   # pins at y >= 23.5: the region keeps an 18 mm strip


def test_pin_nets_reach_the_layout_proofs(tmp_path):
    """Pins with nets make the layout proofs run on the drawn parts: a mains terminal pad closer than isolation_mm to
    an accessible screw fails (proof-padiso and the fixed-pad isolation check); 6 mm further it passes."""
    iso = [{"name": "mains", "nets": ["L", "N"], "creepage_mm": 6.0, "clearance_mm": 3.0, "between_mm": 2.5}]

    def design(tx):
        return make(tmp_path, {"construction": "single", "regions": [
            {"name": "screw", "cut": True, "circle": [4, 20, 2.5], "conductive": True, "mechanical": "screw"},
            {"name": "mains", "kind": "mains", "rect": [0, 0, 60, 40], "nets": ["L", "N"], "isolation_mm": 6.0,
             "items": ["term", "relay"]}],
            "items": [
                {"name": "term", "refs": ["J1"], "size_mm": [11, 8], "at": [tx, 15], "height_mm": 10,
                 "pin_rows": [{"n": 2, "pitch": 5, "at": [-2.5, 0], "drill_mm": 1.2, "pad_mm": 2.4,
                               "nets": ["L", "N"]}]},
                {"name": "relay", "refs": ["K1"], "footprint": RELAY.replace("_back", ""), "at": [42, 20],
                 "height_mm": 16, "pins": {"13": "L", "14": "N"}},
                {"name": "rest", "area_mm2": 200, "side": "front"}]},
            nets={"L": {"width_mm": 1.0}, "N": {"width_mm": 1.0}}, isolation=iso)
    near = arch(design(14))
    assert any("J1.1 (L)" in c["msg"] and "screw" in c["msg"] for c in fails(near, "proof-padiso"))
    assert any("J1.1 (L, fixed)" in c["msg"] for c in fails(near, "isolation"))
    far = arch(design(20))
    assert not fails(far, "proof-padiso") and not fails(far, "isolation"), fails(far)


def test_parse_new_item_fields(tmp_path):
    d = make(tmp_path, {"items": [
        {"name": "rest", "area_mm2": 400, "side": "front"},
        {"name": "x", "size_mm": [5, 5], "rot": 45, "keepout_mm": -1, "pins": ["1"],
         "pin_rows": [{"n": 0}]},
        {"name": "y", "footprint": RELAY, "pin_rows": [{"n": 2}]}]})
    text = " | ".join(floorplan.validate(d))
    for want in ("rot must be 0, 90, 180 or 270", "keepout_mm must be", "pins must map", "needs n",
                 "pin_rows needs size_mm"):
        assert want in text, want
    assert "rest" not in text


def test_bad_pin_number_and_missing_footprint(tmp_path):
    d = make(tmp_path, {"construction": "double", "items": [
        {"name": "k", "refs": ["K1"], "footprint": RELAY, "at": [20, 20], "pins": {"99": "L"}},
        {"name": "z", "footprint": "hw:No_Such_Footprint", "at": [40, 20]}]})
    res = arch(d)
    msgs = " | ".join(c["msg"] for c in fails(res, "field"))
    assert "pins 99 are not pads" in msgs and "No_Such_Footprint not found" in msgs


def test_side_from_footprint_and_keepout(tmp_path):
    """Without `side`, a `_back` footprint puts the item on the back face; keepout_mm grows its courtyard."""
    def design(ko):
        return make(tmp_path, {"construction": "double", "items": [
            {"name": "k", "refs": ["K1"], "footprint": RELAY, "at": [20, 20], "keepout_mm": ko},
            {"name": "m", "refs": ["K2"], "footprint": RELAY, "at": [20, 31.5]}]})   # courtyards 1 mm apart
    res = arch(design(2.0))
    assert set(res["_parts"]["K1"].sides) == {"back"}
    assert any("K1 and K2 overlap" in c["msg"] for c in fails(res, "overlap"))
    assert not fails(arch(design(0)), "overlap")
