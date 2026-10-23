"""padiso: a pad of a region net that a fixed part puts inside the isolation distance of a conductive keep-out is a
`hwflow check` error, measured with the pad's real outline (a round pad is a circle, not its box)."""
from test_barrier import ALL, Libs, _design, _pad

from hwflow import padiso
from hwflow.postlayout import pad_shape


def _run(tmp_path, x, shape):
    pad = _pad("1", x, 10, 2, 2, ALL, "thru_hole")
    pad["shape"] = shape
    fps = {"A": ([pad], ""), "B": ([_pad("1", 25, 10, 1, 1, ALL, "thru_hole")], "")}
    comps = {"U1": ("A", {"1": "AC_L"}, True), "U2": ("B", {"1": "AC_L"}, True)}
    d = _design(tmp_path, comps, None)
    d.raw.update({"floorplan": {"construction": "single", "regions": [
        {"name": "screw", "kind": "keepout", "circle": [0, 4, 1], "conductive": True},
        {"name": "mains", "kind": "mains", "rect": [0, 0, 30, 20], "nets": ["AC_*"], "isolation_mm": 6.0}]}})
    return padiso.findings(d, Libs(fps))


def test_fixed_pad_inside_the_isolation_of_a_conductive_keepout(tmp_path):
    # pad centre (5, 10), screw centre (0, 4) r 1: centre distance 7.81, circle pad r 1 -> 5.81 mm, box -> 5.4 mm
    f = _run(tmp_path, 5, "circle")
    assert [(x["refs"], x["back_to"], x["have_mm"]) for x in f] == [(["U1"], "architecture", 5.81)]
    assert "U1.1 (AC_L) is 5.81 mm from conductive screw" in f[0]["msg"]
    assert _run(tmp_path, 5.5, "circle") == []    # 8.2 - 2 = 6.2 mm


def test_post_layout_pad_shape_is_round_for_a_round_pad():
    c = pad_shape({"x": 0, "y": 0, "w": 2, "h": 2, "shape": "circle"})
    o = pad_shape({"x": 0, "y": 0, "w": 4, "h": 2, "shape": "oval"})
    r = pad_shape({"x": 0, "y": 0, "w": 2, "h": 2, "shape": "rect"})
    assert abs(c.area - 3.1416) < 0.01 and abs(r.area - 4) < 1e-9
    assert abs(o.area - (4 + 3.1416)) < 0.01 and o.bounds == (-2.0, -1.0, 2.0, 1.0)
