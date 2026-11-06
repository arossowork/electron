"""Escape taps (layout.route.taps.escapes): a pad walled in by two footprint neighbours of one other net gets a stub
and via of its own net before routing (pocket-recorder: nPM1300 pads 28-32 alternate VDD_3V0 and the load-switch
outputs, and the VDD_3V0 track from pad 30 to 32 closed pad 31 in)."""

import sys

import pytest

from hwflow.core.design import Design
from hwflow.layout.board import kicad, pcb
from hwflow.layout.route import taps

YAML = """project: escapes
description: a row of pads alternating VDD and two outputs
board: {width_mm: 10, height_mm: 10, layers: 4}  # the default rules: the test board has no rules file of its own
nets:
  GND: {class: ground}
  VDD: {class: signal}
  OUT1: {class: signal}
  OUT2: {class: signal}
components: {}
"""

ROW = ["VDD", "OUT1", "VDD", "OUT2", "VDD", "GND"]  # pads 1-6 at 0.5 mm pitch along x, QFN-like 0.25 x 0.875


def _board(path, tracks=(), vias=()):
    from hwflow.core.sexpr import Q, dumps
    uid = iter(f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 10000))
    names = ["GND", "VDD", "OUT1", "OUT2"]
    nets = {n: i + 1 for i, n in enumerate(names)}
    pads = [["pad", Q(str(i + 1)), "smd", "rect", ["at", round(-1.25 + 0.5 * i, 3), 0], ["size", 0.25, 0.875],
             ["layers", Q("F.Cu"), Q("F.Mask"), Q("F.Paste")], ["net", nets[n], Q(n)], ["uuid", Q(next(uid))]]
            for i, n in enumerate(ROW)]
    tree = ["kicad_pcb", ["version", "20241229"], ["generator", Q("hwflow")], ["general", ["thickness", 1.6]],
            ["paper", Q("A4")], pcb._layers(4), ["setup", ["pad_to_mask_clearance", 0]], ["net", 0, Q("")],
            *[["net", i, Q(n)] for n, i in nets.items()],
            ["gr_rect", ["start", 0, 0], ["end", 10, 10], ["stroke", ["width", 0.1], ["type", "default"]],
             ["fill", "no"], ["layer", Q("Edge.Cuts")], ["uuid", Q(next(uid))]],
            ["footprint", Q("X"), ["layer", Q("F.Cu")], ["uuid", Q(next(uid))], ["at", 5, 5],
             ["property", Q("Reference"), Q("U1"), ["at", 0, 0], ["layer", Q("F.Fab")], ["uuid", Q(next(uid))]],
             *pads]]
    for t in tracks:
        tree.append(["segment", ["start", t["x1"], t["y1"]], ["end", t["x2"], t["y2"]], ["width", t["w"]],
                     ["layer", Q(t["layer"])], ["net", nets[t["net"]]], ["uuid", Q(next(uid))]])
    for v in vias:
        tree.append(["via", ["at", v["x"], v["y"]], ["size", 0.6], ["drill", 0.3], ["layers", Q("F.Cu"), Q("B.Cu")],
                     ["net", nets[v["net"]]], ["uuid", Q(next(uid))]])
    path.write_text(dumps(tree) + "\n")


@pytest.fixture
def design(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "design.yaml").write_text(YAML)
    return Design.load(proj)


def test_trapped_finds_the_pads_between_two_pads_of_one_other_net(design, tmp_path):
    """Pads 2 and 4 sit between VDD pads; pad 3 (VDD between OUT1 and OUT2) and the row ends are not walled in."""
    board = tmp_path / "b.kicad_pcb"
    _board(board)
    rb = pcb.read_board(board)
    got = sorted(p["number"] for _, p, _ in taps.trapped(design, rb, pcb.cu_layers(4)))
    assert got == ["2", "4"]


def test_escapes_give_each_trapped_pad_a_stub_via_clear_of_the_row(design, tmp_path, monkeypatch):
    board = tmp_path / "b.kicad_pcb"
    _board(board)
    e = taps.escapes(design, None, {}, board)
    assert e["stats"] == {"pads": 2, "stub": 2} and not e["untapped"]
    assert {v["net"] for v in e["vias"]} == {"OUT1", "OUT2"}
    assert all(abs(v["y"] - 5) > 0.4375 for v in e["vias"])  # off the pad row, past the pad ends
    monkeypatch.setenv("HWFLOW_ESCAPE_TAPS", "0")
    assert taps.escapes(design, None, {}, board)["stats"]["pads"] == 0
    monkeypatch.delenv("HWFLOW_ESCAPE_TAPS")
    try:
        kicad.cli()
    except FileNotFoundError:
        return
    _board(board, e["tracks"], e["vias"])
    assert not [x for x in kicad.drc(board) if x["sev"] == "error" and x["type"] != "unconnected"]


def test_two_nets_that_wall_each_other_in_get_no_tap(design, tmp_path, monkeypatch):
    """A crossover (USB-C D+/D-: A6 D+ between two D- pads, A7 D- between two D+ pads) is left to the router."""
    monkeypatch.setattr(sys.modules[__name__], "ROW", ["OUT1", "OUT2", "OUT1", "OUT2", "VDD", "GND"])
    board = tmp_path / "b.kicad_pcb"
    _board(board)
    assert taps.trapped(design, pcb.read_board(board), pcb.cu_layers(4)) == []


def test_preseed_puts_the_declared_thermal_vias_on_the_router_board(design, tmp_path, monkeypatch):
    """build_board adds thermal vias only to a routed board; the router must see them, or it runs inner-layer tracks
    through the pad's via sites (pocket-recorder: VDD_3V0 and VSYS on In1/In2 under the nPM1300 pad, 2 shorts)."""
    monkeypatch.setenv("HWFLOW_POUR_TAPS", "0")
    monkeypatch.setattr(pcb, "thermal_vias", lambda d, libs, parts: [{"net": "GND", "x": 5.0, "y": 7.0, "thermal": "U1.6"}])
    board = tmp_path / "b.kicad_pcb"
    _board(board)
    dst, _tracks, vias, _ = taps.preseed(design, None, {}, board, tmp_path, "t_")
    assert {"net": "GND", "x": 5.0, "y": 7.0} in vias  # kept by the router (keep_first) like the taps
    assert any(v["net"] == "GND" and (v["x"], v["y"]) == (5.0, 7.0) for v in pcb.read_board(dst)["vias"])
    assert all(abs(v["x"] - 5.0) + abs(v["y"] - 7.0) > 0.4 for v in vias if v["net"] != "GND")  # escapes avoid it
