"""Isolation guards around a router pass (timed-bathroom-vent, inner layers allowed for mains nets):

- routing.isolation_keepouts keeps a hole of a grown outline open when it holds a pad of a net the area binds: filled,
  the net's own pad sat inside its own keep-out, the router freed its pad access, and the escape (an F.Cu stub and a
  through via) came 0.95 mm from a GND pad against a 1.8 mm group distance;
- routing.isolation_conflicts finds the copper of a pass that breaks a group distance (two nets of one pass keep only
  track-to-track rules: an SL_A track came 0.42 mm from an HV_RAW via);
- stages.reopened puts nets whose seed copper was dropped after the route was counted back into the router's count
  (the router said 0 unrouted, DRC counted SL_RC as unconnected)."""

from shapely.geometry import Point, Polygon, box

from hwflow.core import sexpr
from hwflow.layout.board import routing
from hwflow.pipeline.stages import reopened

CU = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]


def _pad(net, x, y, w=1.0, h=1.0, layers=("F.Cu",)):
    return {"number": "1", "x": x, "y": y, "w": w, "h": h, "net": net, "kind": "smd", "shape": "rect",
            "layers": list(layers)}


def _board(pads, segs=(), vias=()):
    return {"footprints": {f"P{i}": {"pads": [p]} for i, p in enumerate(pads)}, "segments": list(segs),
            "vias": list(vias)}


def _polys(zones, name, layer="F.Cu"):
    out = []
    for z in zones:
        if sexpr.value(z, "name") == name and str(sexpr.value(z, "layers")) == layer:
            out.append(Polygon([(float(p[1]), float(p[2])) for p in sexpr.find(sexpr.find(z, "polygon"), "pts")[1:]]))
    return out


def _ring(net, cx, cy, r=2.5):
    """Eight GND pads round (cx, cy) whose grown outlines close into a ring with a hole in the middle."""
    return [_pad(net, cx + dx, cy + dy) for dx, dy in ((-r, -r), (0, -r), (r, -r), (r, 0), (r, r), (0, r), (-r, r), (-r, 0))]


def test_hole_with_the_bound_nets_pad_stays_open():
    pads = _ring("GND", 10, 10) + [_pad("HV", 10, 10)] + _ring("GND", 30, 10)  # second ring: an empty hole
    g = [{"members": ["HV"], "mm": 1.0, "between": None}]
    zones, rules = routing.isolation_keepouts(g, _board(pads), CU, 0.6, 0.2, {"HV"})
    out = _polys(zones, "hwflow_iso0_out")
    assert out and any("hwflow_iso0_out" in r for r in rules)
    own = box(9.5, 9.5, 10.5, 10.5)
    assert not any(p.buffer(-1e-6).intersects(own) for p in out)  # the HV pad is outside its own keep-out
    assert any(p.contains(Point(30, 10)) for p in out)  # a hole with no pad of a bound net is filled
    # the open hole is still closed off by the ring: a point on the ring is inside the area
    assert any(p.contains(Point(8.5, 7.5)) for p in out)


def test_isolation_conflicts_finds_a_via_too_close():
    pads = [_pad("GND", 0, 0, w=2.15, h=0.7)]
    clr = lambda a, b: 1.8 if "HV" in (a, b) else 0.2  # noqa: E731
    near = _board(pads, vias=[{"net": "HV", "x": 0.0, "y": 1.6}])  # via edge 0.95 mm from the pad
    bad = routing.isolation_conflicts(near, {"HV"}, clr, CU, 0.6)
    assert ("HV", "GND", "F.Cu", 0.95) in bad
    far = _board(pads, vias=[{"net": "HV", "x": 0.0, "y": 2.6}])
    assert routing.isolation_conflicts(far, {"HV"}, clr, CU, 0.6) == []
    track = {"net": "SL", "layer": "B.Cu", "w": 0.25, "x1": -3, "y1": 2.0, "x2": 3, "y2": 2.0}
    both = _board(pads, segs=[track], vias=[{"net": "HV", "x": 0.0, "y": 2.6}])
    assert any(x[:3] == ("SL", "HV", "B.Cu") for x in routing.isolation_conflicts(both, {"SL", "HV"}, clr, CU, 0.6))


def test_reopened_counts_dropped_nets_the_router_left_open():
    rows = [{"name": "mains", "nets": ["SL_RC", "HV"], "unrouted": 0, "open": []},
            {"name": "rest", "nets": ["X"], "unrouted": 0, "open": []}]
    s, r = reopened("0 unrouted, krt 3 pass(es) in 2 route groups", rows, ["SL_RC"])
    assert s.startswith("1 unrouted, krt 3")
    assert r[0]["unrouted"] == 1 and r[0]["open"] == ["SL_RC"] and r[1] == rows[1]
    assert reopened("0 unrouted", rows, []) == ("0 unrouted", rows)
