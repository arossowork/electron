
from hwflow.core.settings import get_settings
import pytest

from hwflow.layout.board import kicad, pcb
from hwflow.layout.place import placer
from hwflow.layout.route import repair
from hwflow.core.design import Design
from hwflow.core.libs import Libs

EX = "examples/sensor_preamp"
YAML = """project: repairfix
description: three nets on 0603 parts and a header, used by the repair tests
board: {width_mm: 40, height_mm: 30, layers: 2}
nets:
  GND: {class: ground}
  A: {class: signal}
  B: {class: signal}
  C: {class: signal}
components:
  J1:
    value: Conn_01x04
    symbol: Connector_Generic:Conn_01x04
    footprint: Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical
    pins: {"1": A, "2": B, "3": C, "4": GND}
    spice: false
    placement: {x: 4, y: 15, rot: 0, fixed: true}
  R1: {value: 1k, symbol: Device:R, footprint: Resistor_SMD:R_0603_1608Metric, pins: {"1": A, "2": B}, placement: {x: 20, y: 8, rot: 0, fixed: true}}
  R2: {value: 1k, symbol: Device:R, footprint: Resistor_SMD:R_0603_1608Metric, pins: {"1": B, "2": C}, placement: {x: 20, y: 15, rot: 0, fixed: true}}
  R3: {value: 1k, symbol: Device:R, footprint: Resistor_SMD:R_0603_1608Metric, pins: {"1": C, "2": GND}, placement: {x: 20, y: 22, rot: 0, fixed: true}}
  R4: {value: 1k, symbol: Device:R, footprint: Resistor_SMD:R_0603_1608Metric, pins: {"1": A, "2": GND}, placement: {x: 30, y: 15, rot: 0, fixed: true}}
"""
needs_router = pytest.mark.skipif(not (pcb.krt_dir() / "py_router" / "route.py").exists(), reason="KiCadRoutingTools not installed")


def _routed(tmp_path):
    """a small fixed-placement board routed once; returns design, libs, parts, tracks, vias, board path, check()."""
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "design.yaml").write_text(YAML)
    d = Design.load(proj)
    libs = Libs()
    parts, _, viol = placer.place(d, libs, seeds=1, iters=1000)
    assert not viol
    out = tmp_path / "build"
    out.mkdir()
    tracks, vias, _, _ = pcb.run_krt(d, libs, parts, out)
    board = out / f"{d.name}.kicad_pcb"

    def check(tr, vi, island=0):
        pcb.write_board(board, pcb.build_board(d, libs, parts, tr, vi, island_removal=island))
        return kicad.drc(board, refill=True, parity=True)

    return d, libs, parts, tracks, vias, board, check


def _copper(tracks, vias, skip):
    return (sorted((t["net"], t["layer"], round(t["x1"], 3), round(t["y1"], 3), round(t["x2"], 3), round(t["y2"], 3))
                   for t in tracks if t["net"] not in skip),
            sorted((v["net"], round(v["x"], 3), round(v["y"], 3)) for v in vias if v["net"] not in skip))


@needs_router
def test_deleted_net_is_repaired_and_other_copper_stays(tmp_path):
    d, libs, parts, tracks, vias, board, check = _routed(tmp_path)
    base = check(tracks, vias)
    net = next(n for n in ("B", "C", "A") if any(t["net"] == n for t in tracks))
    t2 = [t for t in tracks if t["net"] != net]
    v2 = [v for v in vias if v["net"] != net]
    broken = check(t2, v2)
    assert repair.score(broken)[1] > repair.score(base)[1], "deleting the copper must leave the net unrouted"
    r = repair.repair(d, libs, parts, t2, v2, broken, board, check, rounds=2)
    assert r["report"]["rounds"] >= 1
    assert repair.score(r["drc"]) < repair.score(broken)
    assert repair.score(base) == (0, 0) and repair.score(r["drc"]) == (0, 0), r["report"]
    assert _copper(r["tracks"], r["vias"], {net}) == _copper(t2, v2, {net}), "other nets' copper changed"
    assert any(t["net"] == net for t in r["tracks"])
    assert not list(board.parent.glob("*_rep*")), "scratch files left behind"


@needs_router
def test_round_that_gets_worse_is_rejected(tmp_path, monkeypatch):
    d, libs, parts, tracks, vias, board, check = _routed(tmp_path)
    net = next(n for n in ("B", "C", "A") if any(t["net"] == n for t in tracks))
    t2 = [t for t in tracks if t["net"] != net]
    v2 = [v for v in vias if v["net"] != net]
    own = check(t2, v2)
    before = board.read_bytes()
    calls = []

    def worse(tr, vi, island=0):  # a router result that "breaks" more than it fixes
        calls.append(1)
        pcb.write_board(board, pcb.build_board(d, libs, parts, tr[:3], vi, island_removal=island))
        return own + [{"sev": "error", "type": "unconnected", "msg": "x", "items": [f"Track [{net}] on F.Cu"], "at": [[1, 1]]}] * 3

    r = repair.repair(d, libs, parts, t2, v2, own, board, worse, rounds=3)
    assert calls and r["tracks"] == t2 and r["vias"] == v2
    assert repair.score(r["drc"]) == repair.score(own)
    assert board.read_bytes() == before, "the best board must stay on disk"
    assert any("rejected" in x for x in r["report"]["log"])


def test_plan_names_nets_islands_and_stitching_vias():
    d = Design.load(EX)
    drc = [{"sev": "error", "type": "clearance", "msg": "", "at": [[1, 1], [2, 2]],
            "items": ["Track [AMP_OUT] on F.Cu, length 1 mm", "PTH pad 1 [SENS_IN] of J1"]},
           {"sev": "error", "type": "unconnected", "msg": "", "at": [[3, 3]], "items": ["Track [LED_A] on B.Cu", ]},
           {"sev": "error", "type": "unconnected", "msg": "", "at": [[5, 5], [6, 6]],
            "items": ["Zone [GND] on F.Cu", "Zone [GND] on B.Cu"]},
           {"sev": "error", "type": "unconnected", "msg": "", "at": [[8, 8], [9, 9]],
            "items": ["Pad 2 [GND] of C1 on B.Cu", "Pad 2 [GND] of SW1 on F.Cu"]},  # a split pour seen between two pads
           {"sev": "error", "type": "items_not_allowed", "msg": "", "at": [[7, 7]], "items": ["Footprint U1"]},
           {"sev": "warning", "type": "silk_overlap", "msg": "", "at": [], "items": []}]
    p = repair.plan(d, [], [], drc)
    assert p["rip"] == ["AMP_OUT", "LED_A"]
    assert p["islands"] == ["GND"]
    assert p["unfixable"] == 1


def _island_board(path, vias):
    """20 x 20 mm, GND pour on both layers; a SIG track cuts the corner off F.Cu, and the GND pad TP1 sits in it."""
    from hwflow.core.sexpr import Q, dumps

    def zone(layer, n):
        return ["zone", ["net", 1], ["net_name", Q("GND")], ["layer", Q(layer)], ["uuid", Q(f"00000000-0000-0000-0000-00000000000{n}")],
                ["hatch", "edge", 0.5], ["connect_pads", "yes", ["clearance", 0.2]], ["min_thickness", 0.25],
                ["filled_areas_thickness", "no"], ["fill", "yes", ["thermal_gap", 0.3], ["thermal_bridge_width", 0.4],
                                                   ["island_removal_mode", 0]],
                ["polygon", ["pts", ["xy", 0.3, 0.3], ["xy", 19.7, 0.3], ["xy", 19.7, 19.7], ["xy", 0.3, 19.7]]]]

    tree = ["kicad_pcb", ["version", "20241229"], ["generator", Q("hwflow")], ["general", ["thickness", 1.6]], ["paper", Q("A4")],
            pcb._layers(2), ["setup", ["pad_to_mask_clearance", 0]], ["net", 0, Q("")], ["net", 1, Q("GND")], ["net", 2, Q("SIG")],
            ["gr_rect", ["start", 0, 0], ["end", 20, 20], ["stroke", ["width", 0.1], ["type", "default"]], ["fill", "no"],
             ["layer", Q("Edge.Cuts")], ["uuid", Q("00000000-0000-0000-0000-000000000009")]],
            ["segment", ["start", 1, 7], ["end", 7, 1], ["width", 0.25], ["layer", Q("F.Cu")], ["net", 2],
             ["uuid", Q("00000000-0000-0000-0000-000000000003")]],
            ["footprint", Q("X"), ["layer", Q("F.Cu")], ["uuid", Q("00000000-0000-0000-0000-000000000005")], ["at", 2.2, 2.2],
             ["property", Q("Reference"), Q("TP1"), ["at", 0, 0], ["layer", Q("F.Fab")], ["uuid", Q("00000000-0000-0000-0000-000000000006")]],
             ["pad", Q("1"), "smd", "rect", ["at", 0, 0], ["size", 1, 1], ["layers", Q("F.Cu"), Q("F.Mask")], ["net", 1, Q("GND")],
              ["uuid", Q("00000000-0000-0000-0000-000000000007")]]]]
    for i, v in enumerate(vias):
        tree.append(["via", ["at", v["x"], v["y"]], ["size", 0.6], ["drill", 0.3], ["layers", Q("F.Cu"), Q("B.Cu")], ["net", 1],
                     ["uuid", Q(f"00000000-0000-0000-0000-0000000001{i:02d}")]])
    tree += [zone("F.Cu", 1), zone("B.Cu", 2)]
    path.write_text(dumps(tree) + "\n")


def test_gnd_island_gets_a_stitching_via(tmp_path):
    try:
        kicad.cli()
    except FileNotFoundError:
        pytest.skip("kicad-cli not installed")
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "design.yaml").write_text(YAML)
    d = Design.load(proj)
    d.raw["board"].update(width_mm=20, height_mm=20)
    board = tmp_path / "isl.kicad_pcb"
    track = [{"net": "SIG", "layer": "F.Cu", "w": 0.25, "x1": 1, "y1": 7, "x2": 7, "y2": 1}]
    via = [{"net": "GND", "x": 12.0, "y": 12.0}]  # joins the main F.Cu pour to B.Cu

    def check(tr, vi, island=0):
        _island_board(board, vi)
        return kicad.drc(board)

    drc = check(track, via)
    assert any(x["sev"] == "error" and x["type"] == "unconnected" for x in drc), "fixture must have a split GND pour"
    r = repair.repair(d, None, {}, track, via, drc, board, check, rounds=2)
    assert repair.score(r["drc"])[0] == 0, r["report"]
    new = [v for v in r["vias"] if v not in via]
    assert len(new) == 1 and new[0]["x"] + new[0]["y"] < 8, "the via must sit inside the cut-off corner"
    assert r["tracks"] == track


def _stub_drc(n):
    return [{"sev": "error", "type": "clearance", "desc": f"Clearance [N{i}]", "at": (0, 0)} for i in range(n)]


def test_repair_skipped_above_error_limit(tmp_path, monkeypatch):
    board = tmp_path / "b.kicad_pcb"
    board.write_text("x")
    monkeypatch.setattr(repair, "plan", lambda *a, **k: pytest.fail("plan must not run"))
    class D: name = "d"
    r = repair.repair(D(), None, {}, [], [], _stub_drc(get_settings().repair.max_errors + 1), board, lambda *a: [], 3)["report"]
    assert r["skipped"] and r["rounds"] == 0 and "repair skipped" in repair.summary(r)


def test_repair_stops_on_wall_budget(tmp_path, monkeypatch):
    board = tmp_path / "b.kicad_pcb"
    board.write_text("x")
    clock = iter(range(0, 10000, 100))
    monkeypatch.setattr(repair.time, "time", lambda: next(clock))
    monkeypatch.setenv("HWFLOW_REPAIR_BUDGET_S", "150")
    monkeypatch.setattr(repair, "plan", lambda *a, **k: {"rip": [], "islands": [], "drop_vias": ["v"]})
    n = {"c": 0}
    def check(t, v, i):
        n["c"] += 1
        return _stub_drc(10 - n["c"])
    class D: name = "d"; board = {}
    r = repair.repair(D(), None, {}, [], [], _stub_drc(10), board, check, 10)["report"]
    assert r["rounds"] < 10 and any("budget" in l for l in r["log"])
