"""Incremental (ECO) layout after a clean layout: small edits keep the last good placement (layout/place/eco.py), and
a blocked structure is built again when its fixed positions or floorplan changed (diagnose.blocked)."""
import json

import yaml

from hwflow.core.design import Design
from hwflow.core.libs import Libs
from hwflow.layout import diagnose
from hwflow.layout.place import eco, placer
from hwflow.pipeline import stages as pl_stages

R = "Resistor_SMD:R_0603_1608Metric"


def _raw(n=12):
    comps = {"J1": {"value": "conn", "symbol": "Device:R", "footprint": R, "pins": {"1": "N0", "2": "GND"},
                    "placement": {"x": 3, "y": 15, "rot": 0, "fixed": True}}}
    for i in range(1, n + 1):
        comps[f"R{i}"] = {"value": "1k", "symbol": "Device:R", "footprint": R,
                          "pins": {"1": f"N{i - 1}", "2": f"N{i}"}}
    return {"project": "eco", "description": "a resistor chain", "board": {"width_mm": 30, "height_mm": 30, "layers": 2},
            "nets": {"GND": {"class": "ground"}}, "components": comps}


def _design(tmp_path, raw):
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    d = Design.load(tmp_path)
    return d, Libs(design=d)


def _good(tmp_path, raw):
    d, libs = _design(tmp_path, raw)
    parts, _, viol = placer.place(d, libs, seeds=1, iters=3000)
    assert not viol
    out = tmp_path / "build"
    out.mkdir(exist_ok=True)
    (out / "placement.json").write_text(json.dumps({
        "__design_board__": [30, 30], "__board_mm__": [30, 30], **{r: [p.x, p.y, p.r] for r, p in parts.items()}}))
    assert eco.save_good(out, d, libs) == out / eco.GOOD
    good = eco.load_good(out / eco.GOOD)
    assert good["__footprints__"]["R1"] == R
    return good


def test_unchanged_design_keeps_every_position(tmp_path):
    raw = _raw()
    good = _good(tmp_path, raw)
    d, libs = _design(tmp_path, raw)
    parts, _, viol, info = eco.place(d, libs, good)
    assert not viol and info["touched"] == [] and info["moved"] == [] and info["kept"] == 12
    for r in (f"R{i}" for i in range(1, 13)):
        assert [parts[r].x, parts[r].y, parts[r].r] == good[r]


def test_a_new_part_and_a_pinned_part_move_only_locally(tmp_path):
    raw = _raw()
    good = _good(tmp_path, raw)
    raw["components"]["R13"] = {"value": "1k", "symbol": "Device:R", "footprint": R, "pins": {"1": "N12", "2": "GND"}}
    # R1 pinned right on top of R5: R5 (or R1's neighbours) must make room, the rest stays
    x5, y5, _ = good["R5"]
    raw["components"]["R1"]["placement"] = {"x": x5, "y": y5, "rot": 0, "fixed": True}
    d, libs = _design(tmp_path, raw)
    parts, _, viol, info = eco.place(d, libs, good)
    assert not viol
    assert info["touched"] == ["R13"] and "R5" in info["conflicts"]
    assert [parts["R1"].x, parts["R1"].y] == [x5, y5]
    same = [r for r in (f"R{i}" for i in range(2, 13)) if [parts[r].x, parts[r].y, parts[r].r] == good[r]]
    assert len(same) >= 8, f"only {same} kept their positions"


def test_a_footprint_swap_touches_the_part(tmp_path):
    raw = _raw()
    good = _good(tmp_path, raw)
    raw["components"]["R3"]["footprint"] = "Resistor_SMD:R_0805_2012Metric"
    raw["components"]["R4"]["mpn"] = "another 0603"  # an MPN swap with the same footprint touches nothing
    d, libs = _design(tmp_path, raw)
    _, _, viol, info = eco.place(d, libs, good)
    assert not viol and info["touched"] == ["R3"]


def test_a_large_edit_or_another_board_places_from_scratch(tmp_path):
    raw = _raw()
    good = _good(tmp_path, raw)
    for i in range(1, 6):
        raw["components"][f"R{i}"]["footprint"] = "Resistor_SMD:R_0805_2012Metric"
    d, libs = _design(tmp_path, raw)
    assert eco.place(d, libs, good) is None  # 5 of 12 parts changed: more than MAX_TOUCHED
    raw = _raw()
    raw["board"]["width_mm"] = 32
    d, libs = _design(tmp_path, raw)
    assert pl_stages._eco_place(d, libs, tmp_path / "build" / eco.GOOD, [32, 30], None) is None


def test_a_clean_layout_records_the_good_placement(tmp_path, monkeypatch):
    raw = _raw()
    d, libs = _design(tmp_path, raw)
    out = tmp_path / "build"
    out.mkdir()
    (out / "placement.json").write_text(json.dumps({"__board_mm__": [30, 30], "R1": [5, 5, 0]}))
    pl_stages._record_layout(d, {"ok": False, "unrouted": 3}, False)
    assert not (out / eco.GOOD).exists()
    pl_stages._record_layout(d, {"ok": True, "unrouted": 0, "drc_errors": 0}, False)
    assert eco.load_good(out / eco.GOOD)["R1"] == [5, 5, 0]
    assert pl_stages.eco_path(out) == out / eco.GOOD
    monkeypatch.setenv("HWFLOW_ECO", "0")
    assert pl_stages.eco_path(out) is None


def test_a_routed_layout_with_a_failing_budget_records_the_good_placement(tmp_path):
    """Converged (0 unrouted, 0 DRC errors, legal) is enough: thermo-e2e-8's routed board failed only an ESD budget,
    and its sign-off edit (two parts pinned) then placed and routed from scratch."""
    d, libs = _design(tmp_path, _raw())
    out = tmp_path / "build"
    out.mkdir()
    (out / "placement.json").write_text(json.dumps({"__board_mm__": [30, 30], "R1": [5, 5, 0]}))
    routed = {"ok": False, "unrouted": 0, "drc_errors": 0, "placement_violations": [], "board_mm": [30, 30],
              "budgets": [{"name": "esd", "ok": False}]}
    for bad in ({"drc_errors": 1}, {"unrouted": 1}, {"placement_violations": ["R1 off the board"]},
                {"budget_exhausted": True}, {"blocked": "not routed: placement illegal"}):
        pl_stages._record_layout(d, {**routed, **bad}, False)
        assert not (out / eco.GOOD).exists(), bad
    pl_stages._record_layout(d, routed, False)
    assert eco.load_good(out / eco.GOOD)["R1"] == [5, 5, 0]


def test_blocked_structure_reopens_when_the_floorplan_or_a_fixed_position_changes(tmp_path):
    raw = _raw()
    d, _ = _design(tmp_path, raw)
    out = tmp_path / "build"
    out.mkdir()
    diagnose.record(out, {"structure": diagnose.structure_key(d), "constraints": diagnose.constraints_key(d),
                          "ok": False, "blocked": True, "override": False, "diagnosis": {"stage": "stage 2"}})
    assert diagnose.blocked(d, out) is not None  # nothing changed: still blocked
    raw["components"]["J1"]["placement"]["x"] = 4  # the verdict's remedy: move a fixed part
    d2, _ = _design(tmp_path, raw)
    assert diagnose.structure_key(d2) == diagnose.structure_key(d)
    assert diagnose.blocked(d2, out) is None
    raw = _raw()
    raw["floorplan"] = {"regions": [{"name": "zone", "kind": "other", "rect": [0, 0, 20, 20], "items": []}]}
    d3, _ = _design(tmp_path, raw)
    assert diagnose.blocked(d3, out) is None
    raw = _raw()
    raw["components"]["R1"]["mpn"] = "x"  # no placement constraint: still blocked
    d4, _ = _design(tmp_path, raw)
    assert diagnose.blocked(d4, out) is not None
    h = diagnose.history(out)
    h[-1].pop("constraints")  # an entry written before the key: blocked as before
    (out / "layout_history.json").write_text(json.dumps(h))
    assert diagnose.blocked(d2, out) is not None


def test_every_placement_constraint_is_in_the_constraints_key(tmp_path):
    """e2e-4 had to use --retry-layout after adding a floorplan zone: no placement constraint may leave the key."""
    base = _raw()
    base["blocks"] = {"xtal": {"use": "xtal32k_3215", "parts": {"Y": "Y1", "CP": "C9", "CN": "C10"}}}
    base["components"].update({
        "Y1": {"value": "32k", "symbol": "Device:Crystal", "footprint": "Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm",
               "pins": {"1": "XP", "2": "XN"}},
        "C9": {"value": "5p6", "symbol": "Device:C", "footprint": "Capacitor_SMD:C_0402_1005Metric",
               "pins": {"1": "XP", "2": "GND"}},
        "C10": {"value": "5p6", "symbol": "Device:C", "footprint": "Capacitor_SMD:C_0402_1005Metric",
                "pins": {"1": "XN", "2": "GND"}}})
    base["floorplan"] = {"regions": [{"name": "zone", "kind": "other", "rect": [0, 0, 10, 10], "items": ["x"]}],
                         "items": [{"name": "x", "refs": ["R1"]}]}
    k0 = diagnose.constraints_key(_design(tmp_path, base)[0])
    import copy
    edits = {
        "a new zone": lambda r: r["floorplan"]["regions"].append({"name": "z2", "kind": "other", "rect": [1, 1, 5, 5]}),
        "a zone moved": lambda r: r["floorplan"]["regions"][0].update(rect=[0, 0, 12, 10]),
        "a keep-out": lambda r: r["floorplan"]["regions"].append({"name": "ko", "kind": "keepout", "circle": [5, 5, 1]}),
        "an item's refs": lambda r: r["floorplan"]["items"][0].update(refs=["R1", "R2"]),
        "a block anchor": lambda r: r["blocks"]["xtal"].update(at={"x": 10, "y": 10, "rot": 180, "fixed": True}),
        "a block rotation only": lambda r: r["blocks"]["xtal"].update(at={"rot": 90}),
        "a fixed part": lambda r: r["components"]["R2"].update(placement={"x": 5, "y": 5, "fixed": True}),
        "an edge": lambda r: r["components"]["R3"].update(placement={"edge": "top"}),
        "the board margin": lambda r: r["board"].update(margin_mm=1.5),
    }
    for what, f in edits.items():
        raw = copy.deepcopy(base)
        f(raw)
        assert diagnose.constraints_key(_design(tmp_path, raw)[0]) != k0, what
    raw = copy.deepcopy(base)
    raw["components"]["R1"]["mpn"] = "x"  # not a placement constraint
    assert diagnose.constraints_key(_design(tmp_path, raw)[0]) == k0
