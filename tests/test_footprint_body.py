"""What the placer and the board writer take as a footprint's body: the arc of a courtyard counts to its extremes, a
shield outline without a courtyard has no body, a ringless mounting hole is no DRC error, and a board too dense for
the placer margin is placed again on the bare courtyards."""
import pytest
import yaml

from hwflow.core.design import Design
from hwflow.core.libs import Libs, _arc_extremes
from hwflow.layout.board import pcb
from hwflow.layout.place import placer

# a 5 mm LED: the courtyard is one arc from (-1.5, -1.55) through (4.5, 0) back to (-1.5, 1.55), a circle of radius 3.2
LED = """(footprint "LED"
  (fp_line (start -1.5 -1.55) (end -1.5 1.55) (layer "F.CrtYd") (stroke (width 0.05) (type solid)))
  (fp_arc (start -1.498248 -1.55316) (mid 4.50039 0.001807) (end -1.5 1.55) (layer "F.CrtYd") (stroke (width 0.05) (type solid)))
  (pad "1" thru_hole rect (at 0 0) (size 1.8 1.8) (drill 0.9) (layers "*.Cu" "*.Mask"))
  (pad "2" thru_hole circle (at 2.54 0) (size 1.8 1.8) (drill 0.9) (layers "*.Cu" "*.Mask"))
)
"""
# an old-library "No Annular" mounting hole: plated, copper as large as the drill, no courtyard
HOLE = """(footprint "HOLE"
  (pad "1" thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2) (layers "*.Cu" "*.Mask"))
)
"""
# a shield outline without a courtyard: two header pads 40 mm apart
SHIELD = """(footprint "SHIELD"
  (pad "1" thru_hole circle (at 0 0) (size 1.7 1.7) (drill 1) (layers "*.Cu" "*.Mask"))
  (pad "2" thru_hole circle (at 0 40) (size 1.7 1.7) (drill 1) (layers "*.Cu" "*.Mask"))
  (pad "3" smd rect (at 8 20) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
)
"""


def _lib(tmp_path, **fps):
    d = tmp_path / "lib" / "t.pretty"
    d.mkdir(parents=True)
    for name, text in fps.items():
        (d / f"{name}.kicad_mod").write_text(text)


def _design(tmp_path, comps, w=10.0, h=42.0):
    (tmp_path / "design.yaml").write_text(yaml.safe_dump({
        "project": "t", "board": {"width_mm": w, "height_mm": h, "layers": 2},
        "nets": {"A": {"class": "signal"}, "B": {"class": "signal"}}, "components": comps}))
    return Design.load(tmp_path)


def test_arc_extremes_bound_the_arc_not_only_its_three_points():
    pts = _arc_extremes((1, 0), (-0.70710678, 0.70710678), (0, -1))  # three quarters, counterclockwise
    assert sorted((round(x, 6), round(y, 6)) for x, y in pts) == [(-1, 0), (0, -1), (0, 1)]
    assert _arc_extremes((0, -1), (-0.70710678, 0.70710678), (1, 0)) == pytest.approx(pts)  # the same arc reversed
    assert _arc_extremes((0, 0), (1, 1), (2, 2)) == []  # collinear: a segment


def test_courtyard_arc_counts_to_its_extremes(tmp_path):
    _lib(tmp_path, LED=LED)
    fp = Libs(extra_dirs=[tmp_path / "lib"]).footprint("t:LED")
    x0, y0, x1, y1 = fp["sides"]["front"]
    assert (x0, x1) == pytest.approx((-1.5, 4.5), abs=1e-3)
    assert (y0, y1) == pytest.approx((-3.2, 3.2), abs=1e-3)  # not the +-1.55 of the arc's end points
    assert fp["courtyard"]


def test_shield_outline_without_courtyard_has_no_body(tmp_path):
    _lib(tmp_path, SHIELD=SHIELD)
    fp = Libs(extra_dirs=[tmp_path / "lib"]).footprint("t:SHIELD")
    assert not fp["courtyard"]
    assert placer._bodiless(fp, {"width_mm": 10, "height_mm": 42})
    assert not placer._bodiless(fp, {"width_mm": 50, "height_mm": 80})  # a fifth of the board: it may be a body
    assert not placer._bodiless({**fp, "courtyard": True}, {"width_mm": 10, "height_mm": 42})
    d = _design(tmp_path, {"U1": {"value": "x", "symbol": "Device:R", "footprint": "t:SHIELD", "pins": {"1": "A"}}})
    p = placer.build_parts(d, Libs(design=d))["U1"]
    assert not p.sides and p.guard == frozenset({"3"})
    # another part may sit between its pads, but not over one of them (the SMD pad is kept clear like a hole, with
    # half the pad clearance: (0.2 mm + the 0.1 mm placer margin) / 2)
    r = placer.Part("R", 2, 2, 0, 0, {}, x=0, y=20, sides={"front": (2, 2, 0, 0)})
    p.x = p.y = 0
    assert placer._side_overlap(p, r) == 0
    r.x = 8
    assert placer._side_overlap(p, r) == pytest.approx(1.3 * 1.3)  # the pad with half the pad clearance, 0.15 mm


def test_ringless_mounting_hole_is_exempt_from_the_annular_minimum(tmp_path):
    _lib(tmp_path, HOLE=HOLE, LED=LED)
    d = _design(tmp_path, {"H1": {"value": "x", "symbol": "Device:R", "footprint": "t:HOLE", "pins": {"1": "NC"}},
                           "H2": {"value": "x", "symbol": "Device:R", "footprint": "t:HOLE", "pins": {"1": "A"}},
                           "D1": {"value": "x", "symbol": "Device:R", "footprint": "t:LED", "pins": {"1": "A", "2": "B"}}})
    rules = pcb.ringless_hole_rules(d)
    # H2 carries a net: the ring is needed. D1's pads have rings
    assert rules == ["(rule \"ringless_hole_H1\" (constraint annular_width (min 0mm)) "
                     "(condition \"A.memberOfFootprint('H1') && (A.Pad_Number == '1')\"))"]


def test_tight_placement_fits_a_board_too_dense_for_the_placer_margin(tmp_path, monkeypatch):
    """Five 1206 resistors (courtyard 4.56 x 2.36 mm) on a 5 x 13 mm strip: they stack on their bare courtyards
    (11.8 mm, KiCad's test), not with the placer margin around each one (5.06 mm wide, or 14.3 mm stacked)."""
    comps = {f"R{i}": {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_1206_3216Metric",
                       "pins": {"1": "A", "2": "B"}} for i in range(5)}
    d = _design(tmp_path, comps, w=5.0, h=13.0)
    d.raw["board"]["margin_mm"] = 0.0
    libs = Libs(design=d)
    monkeypatch.setenv("HWFLOW_TIGHT", "0")
    _, _, loose = placer.place(d, libs, seeds=1, iters=3000)
    monkeypatch.setenv("HWFLOW_TIGHT", "1")
    parts, _, tight = placer.place(d, libs, seeds=1, iters=3000)
    assert loose and not tight
    ps = list(parts.values())
    assert all(placer._side_overlap(a, b, 0.0) == 0 for i, a in enumerate(ps) for b in ps[i + 1:])


def test_every_copper_pad_is_in_the_placer_model():
    """A fuse holder's two clips per number and a plated tab without a number are copper the placer checks: the
    second clip and the tab are entries of their own, without a net."""
    pad = lambda n, x, kind="thru_hole": {"number": n, "kind": kind, "shape": "circle", "x": x, "y": 0.0, "rot": 0,  # noqa: E731
                                          "w": 2.0, "h": 2.0, "layers": ["*.Cu", "*.Mask"], "drill": 1.0}
    kinds = {}
    cu = placer._pad_copper([pad("1", 0), pad("1", 10), pad("2", 20), pad("", 30),
                             {**pad("", 40, "np_thru_hole"), "layers": ["*.Mask"]}], kinds)
    assert set(cu) == {"1", "1#2", "2", "#1"}  # the non-plated hole is no copper (Part.npth)
    assert cu["1#2"][:2] == (10, 0.0) and cu["#1"][:2] == (30, 0.0)
    assert kinds == {"1": "thru_hole", "1#2": "thru_hole", "2": "thru_hole", "#1": "thru_hole"}


def test_open_courtyard_is_written_as_its_bounding_rectangle():
    """KiCad malformed_courtyard: a closed rectangle with a stray antenna line across it fails DRC wherever the part
    is; the board writer gives it the bounding rectangle the placer held it by. A closed outline is kept."""
    from hwflow.core import sexpr
    line = lambda a, b: ["fp_line", ["start", *a], ["end", *b], ["layer", sexpr.Q("F.CrtYd")]]  # noqa: E731
    rect = [line((0, 0), (4, 0)), line((4, 0), (4, 9)), line((4, 9), (0, 9)), line((0, 9), (0, 0))]
    closed = ["footprint", "M"] + rect
    assert pcb._closed_courtyards([*closed]) == closed
    out = pcb._closed_courtyards(closed + [line((0, 2), (4, 2))])  # the T-junction leaves two loose ends
    yards = [g for g in out if isinstance(g, list) and str(sexpr.value(g, "layer", "")) == "F.CrtYd"]
    assert len(yards) == 1 and yards[0][0] == "fp_rect"
    assert (sexpr.find(yards[0], "start")[1:], sexpr.find(yards[0], "end")[1:]) == ([0, 0], [4, 9])


def test_extreme_placement_lowers_the_copper_edge_clearance_as_a_last_resort(tmp_path, monkeypatch):
    """A 1206 resistor (copper 4.0 x 1.75 mm) on a 4.4 x 2.2 mm board: 0.3 mm of copper-to-edge clearance leaves no
    room, 0.15 mm does. The board then keeps copper_edge_mm for the router and DRC, and placer.WORK records it."""
    comps = {"R1": {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_1206_3216Metric",
                    "pins": {"1": "A", "2": "B"}}}
    d = _design(tmp_path, comps, w=4.4, h=2.2)
    libs = Libs(design=d)
    placer.WORK.pop("extreme_edge_mm", None)
    board = dict(d.board)
    monkeypatch.setenv("HWFLOW_EXTREME", "0")
    _, _, v = placer.place(d, libs, seeds=1, iters=2000)
    assert v and d.board == board  # the board is left as it was
    monkeypatch.setenv("HWFLOW_EXTREME", "1")
    _, _, v = placer.place(d, libs, seeds=1, iters=2000)
    assert not v
    assert d.board["copper_edge_mm"] == placer.EXTREME_EDGE_MM == pcb.edge_mm(d)
    assert placer.WORK["extreme_edge_mm"] == placer.EXTREME_EDGE_MM
    placer.WORK.pop("extreme_edge_mm", None)


def test_courtyard_drawn_on_the_pads_keeps_a_routing_channel():
    """A pad row that reaches its courtyard (a module's castellated edge) keeps a track and two clearances, with the
    placer's clearance margin, less the other part's margin, to the box edge; a courtyard beyond the pads keeps the
    plain margin."""
    pads, sizes = {"1": (6.1, 0.0)}, {"1": (1.0, 1.0)}
    chan = 0.25 + 2 * 0.2 + placer.CLEAR_MARGIN - 0.25
    w, h, cx, cy = placer._yard_box((-6.6, -1.0, 6.6, 1.0), 0.25, pads, sizes, chan)
    assert (cx - w / 2, cx + w / 2) == pytest.approx((-6.6 - 0.25, 6.6 + chan))  # only the side the pad reaches
    assert h == pytest.approx(2.5)
    w, _, cx, _ = placer._yard_box((-6.7, -1.0, 6.7, 1.0), 0.25, pads, sizes, 1.0)
    assert cx + w / 2 == pytest.approx(6.7 + 0.25)  # 0.1 mm beyond the pad (IPC least): the margin, whatever chan


def test_pads_outside_the_courtyard_keep_clear_of_through_hole_copper():
    """A module whose pad row sticks out of its courtyard over a back-side header: the courtyards are on different
    sides and do not touch, but the SMD pads would sit on the header's plated pads."""
    m = placer.Part("U1", 4, 4, 0, 0, {"1": (2.5, 0.0)}, pad_size={"1": (1.0, 1.0)}, x=0, y=0,
                    sides={"front": (4, 4, 0, 0)}, out=frozenset({"1"}), out_keep=0.3)
    j = placer.Part("P1", 2, 2, 0, 0, {"1": (0.0, 0.0)}, pad_size={"1": (1.7, 1.7)}, x=3.5, y=0, fixed=True,
                    sides={"back": (2, 2, 0, 0)}, tht=frozenset({"1"}))
    assert placer._side_overlap(m, j) > 0 and placer._tht_pair(m, j) is not None
    j.x = 4.6  # the header pad from 3.75 mm: the module pad (to 3.0 mm) keeps 0.75 mm, more than the 0.3 mm asked
    assert placer._side_overlap(m, j) == 0
