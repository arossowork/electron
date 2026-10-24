"""Barrier and channel proofs on small synthetic boards: a net whose pads no copper can connect, and a net whose
class width does not pass a gap longer than a neck."""
from types import SimpleNamespace

import yaml

from hwflow.layout.place import barrier
from hwflow.core import sexpr
from hwflow.core.design import Design

ALL = ["*.Cu"]


def _pad(n, x, y, w, h, layers=("F.Cu",), kind="smd"):
    return {"number": n, "kind": kind, "shape": "rect", "x": x, "y": y, "rot": 0.0, "w": w, "h": h,
            "layers": list(layers), "drill": None, "rratio": 0.25, "hull": None}


def _zone(x0, y0, x1, y1, layers='"F.Cu" "B.Cu"', tracks="not_allowed", vias="not_allowed", name="KO"):
    return (f'(zone (net 0) (net_name "") (layers {layers}) (name "{name}") (keepout (tracks {tracks}) (vias {vias}) '
            f'(pads allowed) (copperpour not_allowed) (footprints allowed)) '
            f'(polygon (pts (xy {x0} {y0}) (xy {x1} {y0}) (xy {x1} {y1}) (xy {x0} {y1}))))')


class Libs:
    """footprint id -> (pads, zones text): every footprint is drawn in board coordinates, placed at (0, 0)."""

    def __init__(self, fps):
        self.fps = fps

    def footprint(self, fid):
        pads = self.fps[fid][0]
        return {"pads": pads, "bbox": (-1, -1, 1, 1), "sides": {}}

    def footprint_tree(self, fid):
        return sexpr.loads(f'(footprint "{fid}" {self.fps[fid][1]})')


def _design(tmp_path, comps, nets=None, board=None):
    b = {"layers": 2, "width_mm": 30, "height_mm": 20, "track_mm": 0.2, "clearance_mm": 0.2, "via_dia_mm": 0.6}
    b.update(board or {})
    raw = {"project": "bt", "board": b, "nets": nets or {}, "components": {}}
    for ref, (fid, pins, fixed) in comps.items():
        raw["components"][ref] = {"footprint": fid, "pins": pins,
                                  "placement": {"x": 0, "y": 0, "rot": 0, "fixed": fixed}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    return Design.load(tmp_path)


def _two_pads():
    return {"A": [_pad("1", 5, 10, 1, 1)], "B": [_pad("1", 25, 10, 1, 1)]}


def test_a_keepout_strip_across_the_board_is_a_barrier(tmp_path):
    fps = {k: (v, "") for k, v in _two_pads().items()}
    fps["KO"] = ([], _zone(14, -1, 16, 21))
    d = _design(tmp_path, {"U1": ("A", {"1": "SIG"}, True), "U2": ("B", {"1": "SIG"}, True), "KO1": ("KO", {}, True)})
    f = barrier.findings(d, Libs(fps))
    assert [(x["check"], x["nets"], x["back_to"], x["fixed"]) for x in f] == [("barrier", ["SIG"], "architecture", True)]
    assert "KO1" in f[0]["refs"] and "keep-out areas of KO1" in f[0]["msg"]
    assert 14 <= f[0]["at"][0] <= 16


def test_a_strip_on_one_layer_or_one_that_lets_vias_through_is_no_barrier(tmp_path):
    fps = {k: (v, "") for k, v in _two_pads().items()}
    comps = {"U1": ("A", {"1": "SIG"}, True), "U2": ("B", {"1": "SIG"}, True), "KO1": ("KO", {}, True)}
    fps["KO"] = ([], _zone(14, -1, 16, 21, layers='"F.Cu"'))       # B.Cu stays open: two vias go around it
    assert barrier.findings(_design(tmp_path, comps), Libs(fps)) == []
    fps["KO"] = ([], _zone(14, 2, 16, 21))                          # a 2 mm gap at the top edge: no barrier
    assert barrier.findings(_design(tmp_path, comps), Libs(fps)) == []


def test_a_strip_with_a_hole_in_it_and_a_movable_part_before_placement(tmp_path):
    fps = {k: (v, "") for k, v in _two_pads().items()}
    fps["KO"] = ([], _zone(14, -1, 16, 21))
    # the second pad is on a movable part: before placement it can go to either side, nothing to prove
    d = _design(tmp_path, {"U1": ("A", {"1": "SIG"}, True), "U2": ("B", {"1": "SIG"}, False), "KO1": ("KO", {}, True)})
    assert barrier.findings(d, Libs(fps)) == []


def _wall(gap, thick, net_a="X1", net_b="X2"):
    """Two through-hole pads of foreign nets that wall the board at x = 15, with a slit of `gap` mm at y = 10."""
    h1 = 10 - gap / 2 + 1
    return [_pad("1", 15, (10 - gap / 2 - 1) / 2, thick, h1, ALL, "thru_hole"),
            _pad("2", 15, 10 + gap / 2 + (h1 / 2), thick, h1, ALL, "thru_hole")]


def test_isolation_band_closes_a_gap_that_clearance_leaves_open(tmp_path):
    fps = {k: (v, "") for k, v in _two_pads().items()}
    fps["W"] = ([_pad("1", 15, 10, 2, 12, ALL, "thru_hole")], "")   # a mains pad, y 4-16: 3.7 mm free above and below
    comps = {"U1": ("A", {"1": "SIG"}, True), "U2": ("B", {"1": "SIG"}, True), "J1": ("W", {"1": "AC_L"}, True)}
    assert barrier.findings(_design(tmp_path, comps), Libs(fps)) == []
    iso = {"isolation": [{"name": "mains", "nets": ["AC_*"], "clearance_mm": 6.0, "creepage_mm": 6.0}]}
    f = barrier.findings(_design(tmp_path, comps, board=iso), Libs(fps))
    assert [(x["check"], x["nets"]) for x in f] == [("barrier", ["SIG"])]
    assert "isolation distance" in f[0]["msg"]
    iso["isolation"][0].pop("clearance_mm")                          # creepage alone is no straight-line bound
    assert barrier.findings(_design(tmp_path, comps, board=iso), Libs(fps)) == []


def test_a_slit_longer_than_a_neck_is_a_channel_and_a_short_one_is_not(tmp_path):
    fps = {k: (v, "") for k, v in _two_pads().items()}
    comps = {"U1": ("A", {"1": "PWR"}, True), "U2": ("B", {"1": "PWR"}, True), "W1": ("W", {"1": "X1", "2": "X2"}, True)}
    nets = {"PWR": {"width_mm": 0.5}}
    fps["W"] = (_wall(0.6, 6.0), "")   # 0.6 mm slit, 6 mm long: 0.6 - 2 x 0.2 = 0.2 mm of track; no via fits
    f = barrier.findings(_design(tmp_path, comps, nets), Libs(fps))
    assert [(x["check"], x["nets"], x["back_to"]) for x in f] == [("channel", ["PWR"], "circuit")]
    assert abs(f[0]["channel_mm"] - 0.2) <= 0.02 and f[0]["need_mm"] == 0.9 and abs(f[0]["have_mm"] - 0.6) <= 0.02
    assert 12 <= f[0]["at"][0] <= 18 and abs(f[0]["at"][1] - 10) < 1
    fps["W"] = (_wall(0.6, 1.5), "")   # 1.5 mm long: a neck passes it
    assert barrier.findings(_design(tmp_path, comps, nets), Libs(fps)) == []
    fps["W"] = (_wall(0.4, 6.0), "")   # 0.4 mm slit: not even the 0.15 mm minimum passes
    f = barrier.findings(_design(tmp_path, comps, nets), Libs(fps))
    assert [x["check"] for x in f] == ["barrier"] and "pads of other nets" in f[0]["msg"]


def test_after_placement_movable_copper_counts_and_the_finding_says_it_depends_on_it(tmp_path):
    fps = {k: (v, "") for k, v in _two_pads().items()}
    fps["W"] = (_wall(0.6, 6.0), "")
    comps = {"U1": ("A", {"1": "PWR"}, True), "U2": ("B", {"1": "PWR"}, True), "W1": ("W", {"1": "X1", "2": "X2"}, False)}
    d = _design(tmp_path, comps, {"PWR": {"width_mm": 0.5}})
    assert barrier.findings(d, Libs(fps)) == []                      # before placement the wall is not there
    parts = {r: SimpleNamespace(x=0.0, y=0.0, r=0.0, fixed=r != "W1") for r in comps}
    f = barrier.findings(d, Libs(fps), parts)
    assert [(x["check"], x["fixed"]) for x in f] == [("channel", False)]
    parts["W1"].x = 40.0                                             # moved off the path: nothing left
    assert barrier.findings(d, Libs(fps), parts) == []


def test_drc_rule_parsing():
    d = SimpleNamespace(board={"drc_rules": [
        "(rule fine_pitch (condition \"A.memberOfFootprint('U2') || A.memberOfFootprint('U5')\") "
        "(constraint clearance (min 0.15mm)))",
        "(rule \"m\" (condition \"A.intersectsArea('NO_MAINS_INNER') && A.NetClass == 'iso_mains'\") "
        "(constraint disallow track via))",
        "(rule \"x\" (condition \"A.intersectsArea('Z') || A.NetName == 'N'\") (constraint disallow track))",
        "(rule \"c\" (constraint clearance (min 0.1mm)) (condition \"A.Type == 'pad'\"))"]})
    dis, clr = barrier._drc_rules(d)
    assert dis == [{"area": "NO_MAINS_INNER", "preds": [("class", "iso_mains")], "track": True, "via": True}]
    assert clr == [(0.15, {"U2", "U5"}), (0.1, None)]   # an unread clearance condition lowers every pair


def test_a_wide_net_necks_down_out_of_a_fine_pitch_row(tmp_path):
    """SOT-563 middle pad: 0.5 mm pitch, foreign pads on both sides; a 0.6 mm net leaves it as a neck (no finding)."""
    row = [_pad("1", 9.5, 10.75, 0.34, 0.7), _pad("2", 10, 10.75, 0.34, 0.7), _pad("3", 10.5, 10.75, 0.34, 0.7),
           _pad("4", 10.5, 9.25, 0.34, 0.7), _pad("5", 10, 9.25, 0.34, 0.7), _pad("6", 9.5, 9.25, 0.34, 0.7)]
    fps = {"U": (row, ""), "B": ([_pad("1", 25, 10, 1, 1)], "")}
    comps = {"U2": ("U", {"1": "VIN", "2": "SW", "3": "VIN", "4": "GND", "5": "VOUT", "6": "GND"}, True),
             "L1": ("B", {"1": "SW"}, True)}
    d = _design(tmp_path, comps, {"SW": {"width_mm": 0.6}, "VIN": {"width_mm": 0.6}})
    assert barrier.findings(d, Libs(fps)) == []
