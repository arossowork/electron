"""A copper pour keeps max(clearance, creepage) from the members of a board.isolation group, and the post-layout
isolation distance measures a roundrect pad by its rounded outline (not its bounding box)."""

import math
from types import SimpleNamespace

import pytest
import yaml

from hwflow.core.design import Design
from hwflow.layout.board import kicad, pcb
from hwflow.verify import postlayout


def _design(tmp_path, iso: dict) -> Design:
    raw = {"project": "pc", "board": {"width_mm": 20, "height_mm": 20, "layers": 2, "isolation": [
        {"name": "hv", "nets": "HV*", **iso}]},
        "nets": {"GND": {"class": "ground"}, "HV": {"class": "signal"}}, "components": {}}
    proj = tmp_path / "p"
    proj.mkdir(exist_ok=True)
    (proj / "design.yaml").write_text(yaml.safe_dump(raw))
    return Design.load(proj)


def _keepoff(g):
    return [r for r in g["kicad"] if "pour keep-off" in r]


def test_pour_keepoff_rule_is_written_at_the_creepage(tmp_path):
    (g,) = pcb.isolation_groups(_design(tmp_path, {"clearance_mm": 3.0, "creepage_mm": 5.0}))
    (r,) = _keepoff(g)
    assert "(constraint clearance (min 5mm))" in r and "A.NetName == 'HV'" in r and "B.NetName != 'HV'" in r
    assert r.endswith("&& B.Type == 'Zone'\"))")
    assert g["kicad"].index(r) > max(i for i, x in enumerate(g["kicad"]) if '"hv clearance"' in x)  # later rule wins
    (g,) = pcb.isolation_groups(_design(tmp_path, {"creepage_mm": 4.0}))  # creepage only: the fill used the board clearance
    assert "(min 4mm)" in _keepoff(g)[0]
    for iso in ({"clearance_mm": 3.0}, {"clearance_mm": 3.0, "creepage_mm": 3.0}, {"clearance_mm": 4.0, "creepage_mm": 3.0}):
        (g,) = pcb.isolation_groups(_design(tmp_path, iso))
        assert _keepoff(g) == []  # the clearance rule already keeps the pour at max(clearance, creepage)
    assert pcb.pour_keepoff_mm({"clearance_mm": 3.0, "creepage_mm": 5.0}) == 5.0
    assert pcb.pour_keepoff_mm({"clearance_mm": None, "creepage_mm": None}) is None


def test_pour_keepoff_spares_a_pad_the_fill_could_never_reach(tmp_path):
    """sensor_preamp, AMP_OUT with creepage 1.0 mm: U2 (SOT-23-5) has its GND pad 0.35 mm from the AMP_OUT pad, so a
    fill kept 1.0 mm off that pad could not touch the GND pad (an open GND pin); J1's GND pad (2.54 mm pitch through
    hole, 0.84 mm from AMP_OUT) stays reachable from its far side and keeps the rule."""
    import shutil
    from pathlib import Path
    proj = tmp_path / "p"
    shutil.copytree(Path(__file__).parents[1] / "examples" / "sensor_preamp", proj, ignore=shutil.ignore_patterns("build"))
    f = proj / "design.yaml"
    f.write_text(f.read_text().replace("board: {", "board: {isolation: [{name: amp, nets: AMP_OUT, creepage_mm: 1.0}], "))
    (g,) = pcb.isolation_groups(Design.load(proj))
    (r,) = _keepoff(g)
    assert r.endswith("&& B.Type == 'Zone' && !(A.memberOfFootprint('U2') && B.NetName == 'GND')\"))")
    assert pcb._pads_within(Design.load(proj), {"AMP_OUT"}, 0.3) == []  # 0.35 mm: the fill reaches it


def _board(path, rratio=0.125):
    """20 x 20 mm, GND pour on F.Cu, one HV roundrect pad 2 x 3.75 mm at (10, 10)."""
    from hwflow.core.sexpr import Q, dumps
    uid = iter(f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 100))
    tree = ["kicad_pcb", ["version", "20241229"], ["generator", Q("hwflow")], ["general", ["thickness", 1.6]],
            ["paper", Q("A4")], pcb._layers(2), ["setup", ["pad_to_mask_clearance", 0]], ["net", 0, Q("")],
            ["net", 1, Q("GND")], ["net", 2, Q("HV")],
            ["gr_rect", ["start", 0, 0], ["end", 20, 20], ["stroke", ["width", 0.1], ["type", "default"]],
             ["fill", "no"], ["layer", Q("Edge.Cuts")], ["uuid", Q(next(uid))]],
            ["footprint", Q("X"), ["layer", Q("F.Cu")], ["uuid", Q(next(uid))], ["at", 10, 10],
             ["property", Q("Reference"), Q("F1"), ["at", 0, 0], ["layer", Q("F.Fab")], ["uuid", Q(next(uid))]],
             ["pad", Q("1"), "smd", "roundrect", ["at", 0, 0], ["size", 2, 3.75], ["layers", Q("F.Cu"), Q("F.Mask")],
              ["roundrect_rratio", rratio], ["net", 2, Q("HV")], ["uuid", Q(next(uid))]]],
            ["zone", ["net", 1], ["net_name", Q("GND")], ["layer", Q("F.Cu")], ["uuid", Q(next(uid))],
             ["hatch", "edge", 0.5], ["connect_pads", "yes", ["clearance", 0.2]], ["min_thickness", 0.25],
             ["filled_areas_thickness", "no"],
             ["fill", "yes", ["thermal_gap", 0.3], ["thermal_bridge_width", 0.4], ["island_removal_mode", 0]],
             ["polygon", ["pts", ["xy", 0.3, 0.3], ["xy", 19.7, 0.3], ["xy", 19.7, 19.7], ["xy", 0.3, 19.7]]]]]
    path.write_text(dumps(tree) + "\n")


def _measure(d, board):
    ex = SimpleNamespace(d=d, path=board, board=pcb.read_board(board))
    (r,) = [x for x in postlayout.group_clearances(d, ex) if x["rule"] == "hv"]
    return r["mm"]


def test_pour_keeps_the_creepage_in_kicad_and_the_measure_sees_the_rounded_corner(tmp_path):
    try:
        kicad.cli()
    except FileNotFoundError:
        pytest.skip("kicad-cli not installed")
    d = _design(tmp_path, {"clearance_mm": 1.0, "creepage_mm": 3.0})
    out = tmp_path / "b"
    out.mkdir()
    board = out / "pc.kicad_pcb"
    _board(board)
    pcb.write_project(out, "pc", d)
    drc = kicad.drc(board, refill=True)
    assert not [x for x in drc if x["sev"] == "error" and x["type"] in ("creepage", "clearance")], drc
    # the refilled pour sits at the creepage from the pad, measured on the rounded corner (the bounding box of the
    # roundrect put it 0.25 (sqrt 2 - 1) = 0.10 mm closer: the 2.90 of 3.0 mm the thermostat run reported)
    assert _measure(d, board) == pytest.approx(3.0, abs=0.01)
    # without the keep-off rule the pour stops at the clearance and KiCad reports the creepage
    dru = out / "pc.kicad_dru"
    dru.write_text("\n".join(ln for ln in dru.read_text().splitlines() if "pour keep-off" not in ln) + "\n")
    drc = kicad.drc(board, refill=True)
    assert [x for x in drc if x["type"] == "creepage"]
    assert _measure(d, board) == pytest.approx(1.0, abs=0.01)


def test_roundrect_pad_is_measured_by_its_outline():
    """A segment off the corner of a roundrect pad (2 x 3.75 mm, radius 0.25 mm), 3.0 mm from the rounded copper."""
    from hwflow.verify.postlayout import _copper_items
    pad = {"x": 0.0, "y": 0.0, "w": 2.0, "h": 3.75, "net": "HV", "layers": ["F.Cu"], "shape": "roundrect", "rratio": 0.125}
    r = 0.25
    cx, cy = 1.0 - r, 1.875 - r  # corner arc centre
    k = (3.0 + r) / math.sqrt(2)
    px, py = cx + k, cy + k  # 3.0 mm from the arc along the diagonal
    seg = {"net": "GND", "layer": "F.Cu", "x1": px, "y1": py, "x2": px, "y2": py, "w": 0.0}
    ex = SimpleNamespace(d=SimpleNamespace(board={"layers": 2, "via_dia_mm": 0.6}), path=None,
                         board={"segments": [seg], "vias": [], "footprints": {"F1": {"pads": [pad]}}})
    items = _copper_items(ex)
    hv = [i for i in items if i[0] == "HV"]
    g = [i for i in items if i[0] == "GND"][0]
    import numpy as np
    d = postlayout._seg_dist(np.array([i[2:6] for i in hv]), np.array([g[2:6]])) - np.array([i[6] for i in hv])[:, None]
    assert float(d.min()) == pytest.approx(3.0, abs=1e-6)
    rect = dict(pad, shape="rect")  # a plain rect keeps its sharp corner
    ex.board["footprints"]["F1"]["pads"] = [rect]
    hv = [i for i in _copper_items(ex) if i[0] == "HV"]
    d = postlayout._seg_dist(np.array([i[2:6] for i in hv]), np.array([g[2:6]])) - np.array([i[6] for i in hv])[:, None]
    assert float(d.min()) == pytest.approx(3.0 - r * (math.sqrt(2) - 1), abs=1e-6)
