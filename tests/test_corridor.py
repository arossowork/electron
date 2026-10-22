"""Corridor proof on small synthetic boards: the rules of single nets (between_mm, nets.<name>.layers, conductive
isolation) close a corridor that barrier, without them, leaves open; a corridor open by 0.02 mm stays open."""
from types import SimpleNamespace

from test_barrier import ALL, Libs, _design, _pad, _wall

from hwflow import barrier, corridor

BETWEEN = {"isolation": [{"name": "mains", "nets": ["AC_*"], "between_mm": 1.5}]}


def _two_pads(layers=("F.Cu",), kind="smd"):
    return {"A": ([_pad("1", 5, 10, 1, 1, layers, kind)], ""), "B": ([_pad("1", 25, 10, 1, 1, layers, kind)], "")}


def _comps(net="AC_ROOM", wall=("AC_L", "AC_N"), wall_fixed=True):
    return {"U1": ("A", {"1": net}, True), "U2": ("B", {"1": net}, True),
            "W1": ("W", {"1": wall[0], "2": wall[1]}, wall_fixed)}


def test_between_mm_closes_a_slit_that_the_clearance_leaves_open(tmp_path):
    fps = _two_pads()
    fps["W"] = (_wall(3.0, 6.0), "")  # 3 mm slit, 6 mm long: 2.6 mm of track at 0.2 mm, none at 1.5 mm between
    comps = _comps()
    nets = {"AC_ROOM": {"width_mm": 0.5}}
    assert corridor.findings(_design(tmp_path, comps, nets), Libs(fps)) == []       # no between_mm: open
    d = _design(tmp_path, comps, nets, BETWEEN)
    assert barrier.findings(d, Libs(fps)) == []                                    # barrier does not see between_mm
    f = corridor.findings(d, Libs(fps))
    assert [(x["check"], x["nets"], x["back_to"], x["refs"], x["fixed"]) for x in f] == \
        [("corridor", ["AC_ROOM"], "architecture", ["W1"], True)]
    assert f[0]["need_mm"] == 0.5 and f[0]["have_mm"] == 0.0 and 12 <= f[0]["at"][0] <= 18
    assert {b["rule"] for b in f[0]["blockers"]} == {"between_mm"} and "1.5 mm between_mm" in f[0]["msg"]
    # a net outside the group passes the same slit, and the wall pads' own nets are not blocked by it
    comps["U1"], comps["U2"] = ("A", {"1": "SIG"}, True), ("B", {"1": "SIG"}, True)
    assert corridor.findings(_design(tmp_path, comps, nets, BETWEEN), Libs(fps)) == []


def test_a_corridor_open_by_002_mm_is_open_and_one_short_by_002_mm_is_blocked(tmp_path):
    """Free width = slit - 2 x 1.5 mm between_mm; a 0.5 mm track fits with 0.01 mm to spare on each side. A grid
    stride or a rounding to 0.1 mm would call it blocked."""
    fps = _two_pads()
    nets = {"AC_ROOM": {"width_mm": 0.5}}
    fps["W"] = (_wall(3.52, 6.0), "")
    d = _design(tmp_path, _comps(), nets, BETWEEN)
    assert corridor.findings(d, Libs(fps)) == []
    fps["W"] = (_wall(3.48, 6.0), "")
    f = corridor.findings(_design(tmp_path, _comps(), nets, BETWEEN), Libs(fps))
    assert [x["nets"] for x in f] == [["AC_ROOM"]]
    assert abs(f[0]["plain_mm"] - 0.48) <= 0.02 and f[0]["have_mm"] < 0.5   # 3.48 - 3.0
    fps["W"] = (_wall(3.48, 2.0), "")                                          # 2 mm long: a neck passes it
    assert corridor.findings(_design(tmp_path, _comps(), nets, BETWEEN), Libs(fps)) == []


def test_net_layers_close_a_wall_that_vias_cross_on_the_other_layer(tmp_path):
    fps = _two_pads(ALL, "thru_hole")
    fps["W"] = ([_pad("1", 15, 5, 2, 12), _pad("2", 15, 16.25, 2, 9.5)], "")  # F.Cu wall, slit y 11-11.5
    comps = _comps("PWR", ("X1", "X2"))
    assert barrier.findings(_design(tmp_path, comps, {"PWR": {"width_mm": 0.5}}), Libs(fps)) == []  # B.Cu is open
    d = _design(tmp_path, comps, {"PWR": {"width_mm": 0.5, "layers": ["F.Cu"]}})
    f = corridor.findings(d, Libs(fps))
    assert [(x["nets"], x["layer"], x["back_to"]) for x in f] == [(["PWR"], "F.Cu", "architecture")]
    assert abs(f[0]["plain_mm"] - 0.1) <= 0.02 and "layers F.Cu" in f[0]["msg"]
    d = _design(tmp_path, comps, {"PWR": {"width_mm": 0.5, "layers": ["B.Cu"]}})
    assert corridor.findings(d, Libs(fps)) == []                                   # the other layer is open
    d = _design(tmp_path, comps, {"PWR": {"width_mm": 0.5, "layers": ["F.Cu", "B.Cu"]}})
    assert corridor.findings(d, Libs(fps)) == []                                   # vias join the layers


def test_conductive_keepout_isolation_of_region_nets(tmp_path):
    fps = _two_pads(ALL, "thru_hole")
    comps = {"U1": ("A", {"1": "AC_L"}, True), "U2": ("B", {"1": "AC_L"}, True)}

    def fp(iso):
        return {"floorplan": {"construction": "single", "regions": [
            {"name": "screw", "kind": "keepout", "circle": [15, 10, 1], "conductive": True},
            {"name": "mains", "kind": "mains", "rect": [0, 0, 30, 20], "nets": ["AC_*"], "isolation_mm": iso}]}}

    def run(iso):
        d = _design(tmp_path, comps, None)
        d.raw.update(fp(iso))
        return corridor.findings(d, Libs(fps))

    f = run(9.5)  # the screw grown by 9.5 mm covers the 20 mm board height at x = 15
    assert [x["nets"] for x in f] == [["AC_L"]] and "conductive screw 9.5 mm" in f[0]["msg"]
    assert run(8.0) == []  # 1 mm left at the top and bottom edges: a 0.2 mm track passes


def test_after_placement_a_movable_blocker_sends_it_back_to_layout(tmp_path):
    fps = _two_pads()
    fps["W"] = (_wall(3.0, 6.0), "")
    comps = _comps(wall_fixed=False)
    d = _design(tmp_path, comps, {"AC_ROOM": {"width_mm": 0.5}}, BETWEEN)
    assert corridor.findings(d, Libs(fps)) == []                                  # before placement W1 is not there
    parts = {r: SimpleNamespace(x=0.0, y=0.0, r=0.0, fixed=r != "W1") for r in comps}
    f = corridor.findings(d, Libs(fps), parts)
    assert [(x["back_to"], x["refs"], x["fixed"]) for x in f] == [("layout", ["W1"], False)]
