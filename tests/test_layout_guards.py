"""Guards from the home-thermostat-3 layout run: mains copper near a conductive keep-out, nets.<name>.layers against a
disallow rule area, the floorplan fit with fixed parts and conductive keep-outs, the --auto wall-time budget, repair
on fixed-part errors, and pads without copper in the isolation checks."""
import concurrent.futures as cf
import shutil
import time
from pathlib import Path

import pytest
import yaml
from shapely.geometry import Point, box

from hwflow.layout import diagnose
from hwflow.layout.place import floorplan, placer
from hwflow.verify import postlayout
from hwflow.layout.board import pcb
from hwflow.layout.route import krt
from hwflow.layout.route import repair
from hwflow.layout.board import routing
from hwflow.pipeline import stages as pl_stages
from hwflow.core.design import Design
from hwflow.verify.checks import check
from hwflow.core.libs import Libs

EX = "examples/sensor_preamp"
needs_router = pytest.mark.skipif(not (krt.krt_dir() / "py_router" / "route.py").exists(), reason="KiCadRoutingTools not installed")


def preamp(tmp_path, nets=None, floor=None, **board):
    proj = tmp_path / "p"
    shutil.copytree(EX, proj)
    raw = yaml.safe_load((proj / "design.yaml").read_text())
    raw["board"].pop("optimize_area", None)
    raw["board"].update(board)
    for n, extra in (nets or {}).items():
        raw["nets"][n] = {**(raw["nets"].get(n) or {}), **extra}
    if floor:
        raw["floorplan"] = floor
    (proj / "design.yaml").write_text(yaml.safe_dump(raw))
    return Design.load(proj)


MAINS = {"regions": [{"name": "screw", "kind": "keepout", "cut": True, "conductive": True, "circle": [0, 15, 3]},
                     {"name": "mains", "kind": "mains", "rect": [0, 0, 20, 30], "nets": ["AMP_*"], "isolation_mm": 4}]}


# ------------------------------------------------------------------ 1. conductive keep-outs for tracks and vias

def test_conductive_keepout_routes_and_checks_tracks(tmp_path, monkeypatch):
    d = preamp(tmp_path, floor=MAINS)
    needs = floorplan.conductive_needs(d)
    assert len(needs) == 1 and needs[0]["nets"] == ["AMP_OUT"] and needs[0]["mm"] == 4
    zones, rules = routing.conductive_keepouts(needs, ["F.Cu", "B.Cu"], 0.2, {"AMP_OUT", "VBUS"})
    assert len(zones) == 2 and len(rules) == 1 and "A.NetName == 'AMP_OUT'" in rules[0] and "VBUS" not in rules[0]
    assert routing.conductive_keepouts(needs, ["F.Cu", "B.Cu"], 0.2, {"VBUS"}) == ([], [])
    # the area is the cut grown by the distance less the clearance the router keeps from an area edge
    pts = [(float(p[1]), float(p[2])) for p in zones[0][-1][1][1:]]
    r = max(Point(0, 15).distance(Point(p)) for p in pts)
    assert 3 + 4 - 0.2 < r < 3 + 4 - 0.2 + routing.ISO_SLACK + 0.01
    # the layout and post-layout checks see a track (not only a pad) inside the band
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    tr = [{"net": "AMP_OUT", "layer": "B.Cu", "w": 0.25, "x1": 5, "y1": 15, "x2": 8, "y2": 15},
          {"net": "SENS_IN", "layer": "B.Cu", "w": 0.25, "x1": 5, "y1": 17, "x2": 8, "y2": 17}]
    board = tmp_path / "b.kicad_pcb"
    pcb.write_board(board, pcb.build_board(d, libs, parts, tr, [{"net": "AMP_OUT", "x": 6, "y": 12}]))
    rows = postlayout.conductive_clearances(d, pcb.read_board(board))
    assert [(x["net"], x["keepout"]) for x in rows] == [("AMP_OUT", "screw")] and rows[0]["mm"] < 4
    f = [x for x in postlayout.layout_checks(d, postlayout.Extraction(d, board)) if x["code"] == "conductive-isolation"]
    assert len(f) == 1 and f[0]["sev"] == "error" and "screw" in f[0]["msg"]


@needs_router
def test_router_pass_input_holds_the_conductive_keepout(tmp_path, monkeypatch):
    d = preamp(tmp_path, floor=MAINS)
    libs = Libs(design=d)
    out = tmp_path / "out"
    out.mkdir()
    src = out / "x.kicad_pcb"
    pcb.write_board(src, pcb.build_board(d, libs, placer.build_parts(d, libs)))
    pcb.write_project(out, d.name, d)
    seen = []

    def fake(base, stem, retries, run, variants=None):
        seen.append(Path(base[2]))
        return (Path(base[2]), {}, 0), [("default order", "0 unrouted")], [""]

    monkeypatch.setattr(krt, "route_class", fake)
    krt.krt_route(d, src, out, "x_")
    dru = [p.with_suffix(".kicad_dru").read_text() for p in seen if p.with_suffix(".kicad_dru").exists()]
    assert any("hwflow_isoc0" in t and "AMP_OUT" in t for t in dru)
    assert any("(name \"hwflow_isoc0\")" in p.read_text() for p in seen)


# ------------------------------------------------------------------ 2. nets.<name>.layers wins over a disallow rule area

def test_net_layers_win_over_a_disallow_rule_area(tmp_path, monkeypatch):
    rule = ("(rule \"no_inner\" (condition \"A.intersectsArea('INNER') && A.NetClass == 'signal'\") "
            "(constraint disallow track via))")
    outer = "(rule \"no_top\" (condition \"A.intersectsArea('TOP') && A.NetName == 'AMP_OUT'\") (constraint disallow track))"
    d = preamp(tmp_path, nets={"AMP_OUT": {"layers": ["B.Cu", "In2.Cu"]}}, layers=4, drc_rules=[rule, outer])
    monkeypatch.setattr(pcb, "area_layers", lambda design, libs=None: {"INNER": ["In1.Cu", "In2.Cu"], "TOP": ["F.Cu"]})
    rules, notes = pcb.net_layer_exemptions(d)
    assert "&& A.NetName != 'AMP_OUT'" in rules[0] and rules[1] == outer  # TOP shares no layer with the net's list
    assert notes == [{"rule": "no_inner", "area": "INNER", "net": "AMP_OUT", "layers": ["In2.Cu"]}]
    pcb.write_project(tmp_path, d.name, d)
    assert "A.NetName != 'AMP_OUT'" in (tmp_path / f"{d.name}.kicad_dru").read_text()
    w = [x for x in check(d, Libs(design=d)) if x["code"] == "net-layers" and x["sev"] == "warn"]
    assert len(w) == 1 and "no_inner" in w[0]["msg"] and "the net's layers win" in w[0]["msg"]


# ------------------------------------------------------------------ 3. floorplan fit with fixed parts and conductive keep-outs

def test_fit_counts_fixed_parts_and_mains_pads(tmp_path):
    floor = {"regions": [{"name": "amp", "rect": [0, 0, 4, 3.4], "items": ["amp"]}],
             "items": [{"name": "amp", "refs": ["R1"]}]}
    d = preamp(tmp_path, floor=floor)
    for ref, c in d.components.items():
        c.data["placement"] = {"x": 30, "y": 5, "fixed": False}
    d.components["C1"].data["placement"] = {"x": 2, "y": 1.7, "rot": 0, "fixed": True}  # across the middle
    libs = Libs(design=d)
    res = floorplan.check(d, libs, place=False)
    fit = [c for c in res["checks"] if c["name"] == "fit"]
    assert len(fit) == 1 and fit[0]["ok"] is False and fit[0]["back_to"] == "architecture"
    assert "R1" in fit[0]["msg"] and "fits nowhere in region amp" in fit[0]["msg"] and "C1" in fit[0]["msg"]
    d.components["C1"].data["placement"]["fixed"] = False  # a movable part leaves the room to the member
    assert not [c for c in floorplan.check(d, libs, place=False)["checks"] if c["name"] == "fit"]
    # the pad rule: a pad of a mains net must lie in its region (the band around a conductive keep-out excluded)
    parts = placer.build_parts(d, libs)
    p = parts["R1"]
    free = box(0, 0, 10, 10)
    assert floorplan.fit_anywhere(p, "front", free, [], placer.COURTYARD_MARGIN)
    assert floorplan.fit_anywhere(p, "front", free, [("1", box(0, 0, 10, 10))], placer.COURTYARD_MARGIN)
    band = box(0, 0, 10, 10).difference(Point(5, 5).buffer(9))  # only the corners are left, too small for a pad
    assert not floorplan.fit_anywhere(p, "front", free, [("1", band)], placer.COURTYARD_MARGIN)


# ------------------------------------------------------------------ 4. layout --auto wall-time budget

def _result(errors, vias=10):
    return {"ok": errors == 0, "drc_errors": errors, "unrouted": 0, "placement_violations": [], "vias": vias,
            "segments": 1, "drc_warnings": 0, "board_mm": [30, 20], "fixed_parts_only": None, "failing": {}}


def test_auto_stops_at_the_wall_time_budget(tmp_path, monkeypatch, capsys):
    (tmp_path / "design.yaml").write_text(yaml.safe_dump({
        "project": "perf", "board": {"width_mm": 30, "height_mm": 20, "layers": 2},
        "components": {"R1": {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
                              "pins": {"1": "A", "2": "B"}}}}))
    d = Design.load(tmp_path)
    monkeypatch.setenv("HWFLOW_LAYOUT_BUDGET_S", "0.5")
    plan = {0: 0.0, 1: 3.0, 2: 3.0, 3: 3.0}

    def attempt(k):
        time.sleep(plan[k])
        (pl_stages._AUTO["out"] / "auto" / f"a{k + 1}").mkdir(parents=True, exist_ok=True)
        return {"k": k + 1, "r": _result(2), "line": f"attempt {k + 1} line"}

    monkeypatch.setattr(pl_stages, "_auto_attempt", attempt)
    monkeypatch.setattr(pl_stages, "_auto_pool", lambda n: cf.ThreadPoolExecutor(n))
    monkeypatch.setattr(pl_stages, "layout_diagnosis", lambda d, libs, att: {"attempts": len(att)})
    t = time.time()
    r = pl_stages.layout_auto(d, Libs(design=d), attempts=4, workers=3)
    assert time.time() - t < 2.5, "running attempts are not waited for past the budget"
    assert r["auto"]["kept"] == 1
    assert "wall-time budget 0.5 s" in r["auto"]["stopped"] and "1 of 4 attempts" in r["auto"]["stopped"]
    assert r["diagnosis"] == {"attempts": 1}
    assert "[layout --auto] attempt 1 line" in capsys.readouterr().out


# ------------------------------------------------------------------ 5. repair on fixed-part errors

class _P:
    def __init__(self, fixed):
        self.fixed = fixed


def test_repair_skips_fixed_part_errors_and_stops_without_progress(tmp_path, monkeypatch):
    board = tmp_path / "b.kicad_pcb"
    board.write_text("x")
    parts = {"J1": _P(True), "F1": _P(True), "KO_NOTCH_L": _P(True)}
    fixed = [{"sev": "error", "type": "courtyards_overlap", "items": ["Footprint J1", "Footprint F1"]},
             {"sev": "error", "type": "items_not_allowed", "items": ["Footprint J1"]}]
    calls = []

    class D:
        name = "d"
        board = {"gnd_pour": False}

    r = repair.repair(D(), None, parts, [], [], fixed, board, lambda *a: calls.append(a) or fixed, 3)["report"]
    assert r["skipped"] and r["rounds"] == 0 and not calls and "fixed parts (F1, J1)" in r["log"][0]
    open_net = {"sev": "error", "type": "unconnected", "items": ["Pad 1 [AC_L] of K1", "Pad 2 [AC_L] of F1"]}
    monkeypatch.setattr(repair, "reroute", lambda *a, **k: ([], []))
    r = repair.repair(D(), None, parts, [], [], fixed + [open_net], board,
                      lambda *a: calls.append(a) or fixed + [open_net], 3)["report"]
    assert r["rounds"] == 1 and len(calls) == 1 and any("without improvement" in x for x in r["log"])


# ------------------------------------------------------------------ 6. pads without copper keep no distance

def test_pads_without_copper_keep_no_isolation(tmp_path):
    d = preamp(tmp_path, isolation=[{"name": "hv", "nets": ["AMP_OUT"], "creepage_mm": 3.0}])
    d.components["R1"].data["placement"] = {"x": 10, "y": 10, "fixed": True}   # pin 1 AMP_OUT
    d.components["R3"].data["placement"] = {"x": 10, "y": 11.5, "fixed": True}  # +3V3, LED_A: 1.5 mm away
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 40, 30)
    assert any("isolation R1" in v or "/R3." in v and "isolation" in v for v in pl.fixed_violations())
    parts = placer.build_parts(d, libs)
    parts["R3"].nocu = frozenset(parts["R3"].pads)  # a keep-out marker's dummy pads: drawing layer, no copper
    pl = placer.Placer(d, parts, 40, 30)
    assert not any("R3" in v and "isolation" in v for v in pl.fixed_violations() + pl.violations())
    # diagnose attributes an error position to copper pads only
    b = {"footprints": {"MP1": {"pads": [{"number": "1", "x": 1, "y": 1, "layers": ["Dwgs.User"]}]},
                        "R1": {"pads": [{"number": "1", "x": 1.2, "y": 1, "layers": ["F.Cu"]}]}}}
    assert [r for r, _ in diagnose._pads(b)] == ["R1"]


def test_fit_search_tries_every_position_before_a_miss(tmp_path):
    """A pad slot reachable from a few grid positions only: a strided sample skips them; a miss must be a proof."""
    d = preamp(tmp_path)
    p = placer.build_parts(d, Libs(design=d))["R1"]
    free = box(0, 0, 60, 60)  # about 57000 candidate positions: the sample takes every 14th
    w, h = p.pad_size["1"]
    slot = box(10.2 - w / 2, 50.9 - h / 2, 10.2 + w / 2, 50.9 + h / 2)  # the pad itself, nowhere else (a stride misses it)
    assert floorplan.fit_anywhere(p, "front", free, [("1", slot)], placer.COURTYARD_MARGIN)
