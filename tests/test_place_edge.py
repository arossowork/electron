"""Placement rules at the board edge and beside a module's pads (PCBWorld D3 boards, docs/research/placer-d3-attempts.md):

- a tight placement (no placer margin, no board margin) holds only the pad copper inside the board: a courtyard may
  overhang the edge, as `violations` and KiCad allow, and settle finds such spots (a radio module that fills a 16.5 mm
  carrier left room for a header beside it only with the header's courtyard past the edge);
- an SMD pad outside its own courtyard (an ESP-12E's pad rows outside its antenna-only courtyard) keeps other
  courtyards on its side off it: a resistor sat on pad 22 (a short) with no violation;
- a pad's drill offset moves its copper (a buzzer whose SMD pads sit 2.85 mm out from their origins).
"""

import pytest
import yaml

from hwflow.core.design import Design
from hwflow.core.libs import Libs
from hwflow.layout.place import placer

HDR = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"  # courtyard 0.95 mm past its 1.7 mm pads


def _strip(tmp_path, margin_mm):
    """A 3.0 mm wide strip: the header's 3.6 mm courtyard fits only past both edges, its pad copper with 0.65 mm."""
    comps = {f"J{i}": {"value": "hdr", "symbol": "Device:R", "footprint": HDR, "pins": {"1": f"A{i}", "2": f"B{i}"}}
             for i in (1, 2)}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump({"project": "strip", "board": {
        "width_mm": 3.0, "height_mm": 14.0, "layers": 2, "gnd_pour": False, "margin_mm": margin_mm},
        "components": comps}))
    d = Design.load(tmp_path)
    return d, Libs(design=d)


def test_tight_placement_lets_a_courtyard_overhang_the_edge(tmp_path):
    d, libs = _strip(tmp_path, 0.0)
    parts = placer.build_parts(d, libs, margin=0.0, tight=True)
    for p in parts.values():  # both on one spot, centred across the strip
        p.x, p.y, p.r = 1.5, 3.0, 0.0
    pl = placer.Placer(d, parts, 3.0, 14.0, seed=1)
    assert pl.edge_cu
    assert "courtyard overlap J1/J2" in pl.violations()
    assert pl._part_cost("J1")[0] == 0.0  # the copper is inside: no bounds cost for the overhanging courtyard
    assert pl.settle() == []
    for p in parts.values():
        x0, _, x1, _ = p.side_box("front")
        assert x0 < 0 and x1 > 3.0  # the courtyard past both edges
    pl.legalize()  # the clamp holds the copper, not the courtyard
    assert pl.violations() == []


def test_a_board_margin_keeps_the_courtyard_inside(tmp_path):
    d, libs = _strip(tmp_path, 0.5)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 3.0, 14.0, seed=1)
    assert not pl.edge_cu
    p = parts["J1"]
    p.x, p.y, p.r = 1.5, 3.0, 0.0
    assert pl._bound_box(p) == p.box()
    assert pl._part_cost("J1")[0] > 0


def test_an_smd_pad_outside_the_courtyard_keeps_other_courtyards_off():
    mod = placer.Part("U", 4, 4, 0, 0, {"1": (5.0, 0.0)}, x=10, y=10, pad_size={"1": (2.0, 1.0)},
                      sides={"front": (4, 4, 0, 0)}, cu={"1": frozenset({"F.Cu"})}, out=frozenset({"1"}),
                      out_keep=0.2)
    r = placer.Part("R", 2, 1, 0, 0, {"1": (0, 0)}, x=15.0, y=10.0, pad_size={"1": (1, 1)},
                    sides={"front": (2, 1, 0, 0)}, cu={"1": frozenset({"F.Cu"})})
    assert placer._side_overlap(mod, r) > 0  # its body on the module's pad
    assert placer._tht_pair(mod, r) is not None  # a pair legalize pushes apart
    r.sides = {"back": (2, 1, 0, 0)}  # on the other side: no conflict
    assert placer._side_overlap(mod, r) == 0
    r.sides = {"front": (2, 1, 0, 0)}
    r.x = 17.5  # 0.5 mm clear of the pad edge
    assert placer._side_overlap(mod, r) == 0


def test_two_out_pad_rows_keep_the_pad_clearance():
    def mod(ref, x, dx):
        return placer.Part(ref, 4, 4, 0, 0, {"1": (dx, 0.0)}, x=x, y=10, pad_size={"1": (2.0, 1.0)},
                           sides={"front": (4, 4, 0, 0)}, cu={"1": frozenset({"F.Cu"})}, out=frozenset({"1"}),
                           out_keep=0.2)
    a, b = mod("U", 10.0, 5.0), mod("V", 22.1, -5.0)  # pads face to face 0.1 mm apart, the courtyards far apart
    assert placer._side_overlap(a, b) > 0
    b.x = 22.3
    assert placer._side_overlap(a, b) == 0


def test_a_drill_offset_moves_the_pad_copper():
    pads = [{"number": "1", "kind": "smd", "shape": "rect", "x": 4.85, "y": 0.0, "rot": 0.0, "w": 5.7, "h": 4.3,
             "layers": ["B.Cu"], "offset": (2.85, 0.0)},
            {"number": "2", "kind": "smd", "shape": "rect", "x": -4.85, "y": 0.0, "rot": 90.0, "w": 5.7, "h": 4.3,
             "layers": ["B.Cu"], "offset": (-2.85, 0.0)}]
    cu = placer._pad_copper(pads)
    assert cu["1"][:2] == pytest.approx((7.7, 0.0))
    assert cu["2"][:2] == pytest.approx((-4.85, 2.85))  # the offset turns with the pad (KiCad rotation)


def test_a_bodiless_smd_part_blocks_only_its_own_side():
    """A part without a courtyard (tight placement: its pads are all the placer knows of it) keeps other courtyards off
    its pads on the side of their copper only; a through-hole pad blocks both sides (KiCad pth_inside_courtyard)."""
    smd = placer.Part("Q", 3, 3, 0, 0, {"1": (0, 0)}, x=10, y=10, pad_size={"1": (2.0, 2.0)}, sides={},
                      cu={"1": frozenset({"F.Cu"})}, guard=frozenset({"1"}), keep=0.1)
    back = placer.Part("T", 3.5, 3.5, 0, 0, {"1": (0, 0)}, x=10, y=10, pad_size={"1": (3, 3)},
                       sides={"back": (3.5, 3.5, 0, 0)}, cu={"1": frozenset({"B.Cu"})})
    assert placer._side_overlap(smd, back) == 0  # a back test pad under a front SOT-223: legal
    assert placer.side_holes(smd)["back"] == []
    smd.cu = {"1": frozenset({"*"})}
    smd.tht = frozenset({"1"})
    assert placer._side_overlap(smd, back) > 0


def test_a_bodiless_smd_pad_keeps_clear_of_through_hole_pads():
    """A bottom-mount USB socket without a courtyard: its B.Cu pads keep the pad clearance to the pins of a socket
    strip whose courtyard is on the front only (KiCad: a clearance error)."""
    usb = placer.Part("CON", 3, 3, 0, 0, {"1": (0, 0)}, x=10, y=10, pad_size={"1": (1.0, 1.0)}, sides={},
                      cu={"1": frozenset({"B.Cu"})}, guard=frozenset({"1"}), keep=0.1)
    pin = placer.Part("P", 2.5, 2.5, 0, 0, {"1": (0, 0)}, x=11.45, y=10, pad_size={"1": (1.7, 1.7)},
                      sides={"front": (2.5, 2.5, 0, 0)}, cu={"1": frozenset({"*"})}, tht=frozenset({"1"}))
    assert placer._side_overlap(usb, pin) > 0  # 0.1 mm apart, inside the 0.2 mm clearance
    assert placer._tht_pair(usb, pin) is not None
    pin.x = 12.0  # 0.65 mm apart
    assert placer._side_overlap(usb, pin) == 0


def test_net_class_clearance_is_per_copper_layer(tmp_path):
    d, libs = _strip(tmp_path, 0.0)
    parts = placer.build_parts(d, libs, margin=0.0, tight=True)
    pl = placer.Placer(d, parts, 3.0, 14.0, seed=1)
    a, b = ("J1", "1"), ("J2", "1")
    for r, y in (("J1", 3.0), ("J2", 4.9)):  # through-hole pads 0.2 mm apart: they share every layer
        parts[r].x, parts[r].y, parts[r].r = 1.5, y, 0.0
    assert pl._share_layer(a, b) and pl._iso_short(a, b) > 0
    parts["J1"].cu = {n: frozenset({"F.Cu"}) for n in parts["J1"].pads}  # as SMD pads on opposite sides
    parts["J2"].cu = {n: frozenset({"B.Cu"}) for n in parts["J2"].pads}
    assert not pl._share_layer(a, b) and pl._iso_short(a, b) == 0
