"""KiCad board importer (hwflow.layout.board.kicad_import): a placed .kicad_pcb becomes a design whose written board
has the source's pads, nets, rules and outline. Synthetic board, no KiCad needed."""

from __future__ import annotations

import json
import math

import pytest
import yaml

from hwflow.core.design import Design
from hwflow.core.libs import Libs
from hwflow.layout.board import kicad_import as ki
from hwflow.layout.board import pcb
from hwflow.layout.place import placer

R0603 = """
  (footprint "Resistor_SMD:R_0603_1608Metric" (layer "{layer}") (uuid "u-{ref}") (at {x} {y} {rot})
    (property "Reference" "{ref}" (at 0 -1.4 {rot}) (layer "{s}.SilkS") (uuid "p1"))
    (property "Value" "{value}" (at 0 1.4 {rot}) (layer "{s}.Fab") (uuid "p2"))
    (property "MPN" "{mpn}" (at 0 0 0) (layer "{s}.Fab") (hide yes) (uuid "p3"))
    (path "/abc/{ref}") (attr smd)
    (fp_rect (start -1.48 -0.73) (end 1.48 0.73) (stroke (width 0.05) (type solid)) (fill none) (layer "{s}.CrtYd"))
    (pad "1" smd roundrect (at {p1x} 0 {rot}) (size 0.8 0.95) (layers "{s}.Cu" "{s}.Paste" "{s}.Mask")
      (roundrect_rratio 0.25) (net {n1} "{net1}") (uuid "a"))
    (pad "2" smd roundrect (at {p2x} 0 {rot}) (size 0.8 0.95) (layers "{s}.Cu" "{s}.Paste" "{s}.Mask")
      (roundrect_rratio 0.25) (net {n2} "{net2}") (uuid "b"))
  )"""

HOLE = """
  (footprint "MountingHole:MountingHole_3.2mm_M3" (layer "F.Cu") (at 4 4)
    (property "Reference" "H1" (at 0 -4 0) (layer "F.SilkS"))
    (property "Value" "MountingHole" (at 0 4 0) (layer "F.Fab"))
    (fp_circle (center 0 0) (end 3.45 0) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))
    (pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2) (layers "*.Cu" "*.Mask"))
  )"""


def _board(tmp_path):
    fps = [
        R0603.format(ref="R1", layer="F.Cu", s="F", x=110, y=105, rot=90, value="10k 1%", mpn="RC0603FR-0710KL",
                     p1x=-0.825, p2x=0.825, n1=1, net1="/sheet/SIG", n2=2, net2="GND"),
        # back side: KiCad stores the flipped footprint mirrored, pads on B.*
        R0603.format(ref="C1", layer="B.Cu", s="B", x=120, y=108, rot=180, value="0.1uF 50V", mpn="~",
                     p1x=0.825, p2x=-0.825, n1=1, net1="/sheet/SIG", n2=2, net2="GND"),
        R0603.format(ref="R2", layer="F.Cu", s="F", x=115, y=112, rot=0, value="1k", mpn="",
                     p1x=-0.825, p2x=0.825, n1=3, net1="unconnected-(R2-Pad1)", n2=4, net2="+3V3"),
        R0603.format(ref="R3", layer="F.Cu", s="F", x=118, y=115, rot=0, value="1k", mpn="",
                     p1x=-0.825, p2x=0.825, n1=4, net1="+3V3", n2=2, net2="GND"),
        HOLE.replace("(at 4 4)", "(at 104 104)"),
    ]
    edge = "\n".join(f'  (gr_line (start {a} {b}) (end {c} {d}) (stroke (width 0.1) (type default)) (layer "Edge.Cuts"))'
                     for a, b, c, d in ((100, 100, 130, 100), (130, 100, 130, 120), (130, 120, 104, 120),
                                        (104, 120, 100, 116), (100, 116, 100, 100)))
    edge += '\n  (gr_rect (start 124 103) (end 126 113) (stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts"))'
    text = f"""(kicad_pcb (version 20240108) (generator "pcbnew")
  (general (thickness 1.2))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (net 0 "") (net 1 "/sheet/SIG") (net 2 "GND") (net 3 "unconnected-(R2-Pad1)") (net 4 "+3V3")
{chr(10).join(fps)}
{edge}
  (segment (start 110 104.175) (end 120.825 108) (width 0.3) (layer "F.Cu") (net 1) (uuid "s1"))
  (via (at 121 109) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 2) (uuid "v1"))
)
"""
    f = tmp_path / "src" / "demo.kicad_pcb"
    f.parent.mkdir()
    f.write_text(text)
    pro = {"board": {"design_settings": {"rules": {"min_clearance": 0.15}}},
           "net_settings": {"classes": [{"name": "Default", "clearance": 0.18, "track_width": 0.2,
                                         "via_diameter": 0.5, "via_drill": 0.25},
                                        {"name": "Power", "clearance": 0.25, "track_width": 0.5}],
                            "netclass_patterns": [{"netclass": "Power", "pattern": "+3V3"}]}}
    f.with_suffix(".kicad_pro").write_text(json.dumps(pro))
    return f


def test_import_board(tmp_path):
    src = _board(tmp_path)
    rec = ki.import_board(src, tmp_path / "case", name="demo")
    raw = yaml.safe_load((tmp_path / "case" / "design.yaml").read_text())
    b = raw["board"]
    # bounding box of Edge.Cuts; rules of the default net class
    assert (b["width_mm"], b["height_mm"], b["layers"], b["thickness_mm"]) == (30.0, 20.0, 2, 1.2)
    assert (b["clearance_mm"], b["track_mm"], b["via_dia_mm"], b["via_drill_mm"]) == (0.18, 0.2, 0.5, 0.25)
    assert rec["offset"] == [-100.0, -100.0]
    # net classes: Power by pattern; ground by name; sheet paths renamed
    assert raw["nets"]["+3V3"]["width_mm"] == 0.5 and raw["nets"]["+3V3"]["clearance_mm"] == 0.25
    assert [n for n, i in raw["nets"].items() if i.get("class") == "ground"] == ["GND"]  # one poured ground
    assert rec["net_renames"]["/sheet/SIG"] == "sheet_SIG"
    c = raw["components"]
    assert c["R1"]["pins"] == {"1": "sheet_SIG", "2": "GND"} and c["R1"]["mpn"] == "RC0603FR-0710KL"
    assert c["R1"]["value"] == "10k" and c["R1"]["source_value"] == "10k 1%"
    assert c["C1"]["value"] == "0.1uF" and "mpn" not in c["C1"]
    assert c["R2"]["nc"] == ["1"]  # a one-pad net is no connection
    assert c["C1"]["footprint"].endswith("_back")
    assert c["H1"]["pins"] == {"1": "NC"}  # NPTH hole: a dummy pad without copper
    assert c["R1"]["placement"] == {"x": 10.0, "y": 5.0, "rot": 90.0, "fixed": True}
    assert set(rec["free"]) == {"R1", "C1", "R2", "R3"}
    # outline: the corner chamfer is a cut at the edge, the slot a cut inside
    cuts = raw["floorplan"]["regions"]
    assert len(cuts) == 2 and all(r["cut"] for r in cuts)
    areas = sorted(abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(r["poly"], r["poly"][1:] + r["poly"][:1]))) / 2
                   for r in cuts)
    assert areas == pytest.approx([8.0, 20.0], abs=0.05)


def test_written_board_has_the_source_pads(tmp_path):
    """The board hwflow writes from the imported design puts every pad where the source board has it (same size,
    same net after renaming), front and back, rotated or not."""
    src = _board(tmp_path)
    rec = ki.import_board(src, tmp_path / "case", name="demo")
    d = Design.load(tmp_path / "case" / "design.yaml")
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    for r, p in parts.items():
        pl = d.components[r].get("placement")
        p.x, p.y, p.r = pl["x"], pl["y"], pl["rot"]
    out = tmp_path / "w.kicad_pcb"
    pcb.write_board(out, pcb.build_board(d, libs, parts))
    a, b = pcb.read_board(src), pcb.read_board(out)
    dx, dy = rec["offset"]
    ren = rec["net_renames"]
    n = 0
    for ref in ("R1", "C1", "R3"):
        pb = {p["number"]: p for p in b["footprints"][ref]["pads"]}
        for p in a["footprints"][ref]["pads"]:
            q = pb[p["number"]]
            assert math.isclose(q["x"], p["x"] + dx, abs_tol=1e-4) and math.isclose(q["y"], p["y"] + dy, abs_tol=1e-4)
            assert (round(q["w"], 4), round(q["h"], 4)) == (round(p["w"], 4), round(p["h"], 4))
            assert q["net"] == ren.get(p["net"], p["net"])
            assert q["layers"][0] == p["layers"][0]  # B.Cu stays B.Cu
            n += 1
    assert n == 6
    assert parts["C1"].sides and "back" in parts["C1"].sides


def test_human_copper(tmp_path):
    src = _board(tmp_path)
    rec = ki.import_board(src, tmp_path / "case", name="demo")
    cop = ki.human_copper(src, rec["offset"], rec["net_renames"])
    assert cop["tracks"][0]["net"] == "sheet_SIG" and cop["tracks"][0]["x1"] == pytest.approx(10.0)
    m = ki.copper_metrics(cop)
    assert m["vias"] == 1 and m["segments"] == 1 and m["track_mm"] == pytest.approx(11.5, abs=0.1)


def test_unsupported_stackup(tmp_path):
    src = _board(tmp_path)
    t = src.read_text().replace('(31 "B.Cu" signal)', '(1 "In1.Cu" signal) (31 "B.Cu" signal)')
    src.write_text(t)
    with pytest.raises(ki.UnsupportedBoard):
        ki.import_board(src, tmp_path / "case")


def test_numeric_value():
    assert ki.numeric_value("C3", "100nF/50V") == "100nF"
    assert ki.numeric_value("L1", "600R@100MHz") == "1"
    assert ki.numeric_value("U1", "ESP32") == "ESP32"
