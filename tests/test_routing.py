"""routing.py: isolation keep-outs for a router pass and neck-down widening, on small synthetic boards."""
from pathlib import Path

from shapely.geometry import Point, box

from hwflow import pcb, routing, sexpr

CU = ["F.Cu", "B.Cu"]


def _pad(net, x, y, w=1.0, layers=("*.Cu",)):
    return {"number": "1", "x": x, "y": y, "w": w, "h": w, "net": net, "kind": "thru_hole", "shape": "rect",
            "layers": list(layers)}


def _board(pads, segs=()):
    return {"footprints": {f"P{i}": {"pads": [p]} for i, p in enumerate(pads)}, "segments": list(segs), "vias": []}


def _zones(zones, name):
    out = []
    for z in zones:
        if sexpr.value(z, "name") == name:
            pts = [(float(p[1]), float(p[2])) for p in sexpr.find(sexpr.find(z, "polygon"), "pts")[1:]]
            out.append((str(sexpr.value(z, "layers")), pts))
    return out


def test_member_pass_keeps_out_of_the_other_copper():
    from shapely.geometry import Polygon
    b = _board([_pad("AC", 0, 0), _pad("GND", 20, 0), _pad("SIG", 20, 10, layers=("F.Cu",))])
    groups = [{"members": ["AC"], "mm": 6.0}]
    zones, rules = routing.isolation_keepouts(groups, b, CU, 0.6, 0.2, {"AC"})
    z = _zones(zones, "hwflow_iso0_out")
    assert {L for L, _ in z} == {"F.Cu", "B.Cu"}
    front = [Polygon(p) for L, p in z if L == "F.Cu"]
    back = [Polygon(p) for L, p in z if L == "B.Cu"]
    assert any(p.contains(Point(20, 10)) for p in front) and not any(p.contains(Point(20, 10)) for p in back)
    gnd = next(p for p in front if p.contains(Point(20, 0)))
    reach = gnd.exterior.distance(Point(20, 0))  # pad half size 0.5, grown 6.0 - 0.2 + slack
    assert 0.5 + 5.8 <= reach <= 0.5 + 5.8 + routing.ISO_SLACK + 0.01
    assert not any(p.contains(Point(0, 0)) for p in front)
    assert rules == ["(rule \"hwflow_iso0_out\" (constraint disallow track via) "
                     "(condition \"A.intersectsArea('hwflow_iso0_out') && (A.NetName == 'AC')\"))"]


def test_other_pass_keeps_out_of_the_members():
    b = _board([_pad("AC", 0, 0), _pad("GND", 20, 0)],
               [{"x1": 0, "y1": 0, "x2": 0, "y2": 15, "w": 1.0, "layer": "B.Cu", "net": "AC"}])
    zones, rules = routing.isolation_keepouts([{"members": ["AC"], "mm": 6.0}], b, CU, 0.6, 0.2, {"GND", "SIG"})
    z = dict((L, p) for L, p in _zones(zones, "hwflow_iso0_in"))
    from shapely.geometry import Polygon
    assert Polygon(z["B.Cu"]).contains(Point(0, 18)) and not Polygon(z["F.Cu"]).contains(Point(0, 18))
    assert "A.NetName != 'AC'" in rules[0] and "hwflow_iso0_out" not in "".join(rules)


def test_no_group_no_keepout():
    assert routing.isolation_keepouts([], _board([_pad("A", 0, 0)]), CU, 0.6, 0.2, {"A"}) == ([], [])


def test_pass_input_replaces_the_areas_of_the_earlier_pass(tmp_path):
    src = tmp_path / "a.kicad_pcb"
    z = routing._zone("hwflow_iso0_in", "F.Cu", box(0, 0, 1, 1).exterior)
    src.write_text(sexpr.dumps(["kicad_pcb", ["version", 1], z, routing._zone("KO_ANT", "F.Cu", box(0, 0, 1, 1).exterior)]))
    out = routing.pass_input(src, tmp_path / "b.kicad_pcb", [routing._zone("hwflow_iso0_out", "B.Cu", box(0, 0, 2, 2).exterior)])
    names = [str(sexpr.value(x, "name")) for x in sexpr.loads(out.read_text()) if isinstance(x, list) and x[0] == "zone"]
    assert names == ["KO_ANT", "hwflow_iso0_out"]


def test_widen_keeps_the_neck_only_at_the_pinch(tmp_path):
    # net A: a 0.25 mm track from x=0 to x=10; a foreign pad of net B sits beside its first millimetre
    seg = ["segment", ["start", 0, 0], ["end", 10, 0], ["width", 0.25], ["layer", sexpr.Q("F.Cu")], ["net", 1],
           ["uuid", sexpr.Q("u")]]
    path = tmp_path / "x.kicad_pcb"
    path.write_text(sexpr.dumps(["kicad_pcb", ["net", 0, sexpr.Q("")], ["net", 1, sexpr.Q("A")], ["net", 2, sexpr.Q("B")], seg]))
    b = pcb.read_board(path)
    b["footprints"] = {"U1": {"pads": [_pad("B", 0.5, 0.55, w=0.3, layers=("F.Cu",))]}}
    stats = routing.widen(path, b, {"A": 0.6}, lambda a, c: 0.2, CU, 0.6, box(-5, -5, 15, 5), {})
    narrow, total = stats["A"]
    assert abs(total - 10) < 1e-6 and 0 < narrow <= 1.5
    segs = pcb.read_board(path)["segments"]
    assert {s["w"] for s in segs} == {0.25, 0.6}
    assert all(s["w"] == 0.6 for s in segs if s["x1"] >= 1.5)
    # the copper is continuous: the pieces chain from 0 to 10
    xs = sorted((s["x1"], s["x2"]) for s in segs)
    assert xs[0][0] == 0 and xs[-1][1] == 10 and all(a[1] == b[0] for a, b in zip(xs, xs[1:]))


def test_widen_stays_inside_the_outline_and_keepouts(tmp_path):
    seg = ["segment", ["start", 0, 0], ["end", 4, 0], ["width", 0.25], ["layer", sexpr.Q("F.Cu")], ["net", 1],
           ["uuid", sexpr.Q("u")]]
    path = tmp_path / "x.kicad_pcb"
    path.write_text(sexpr.dumps(["kicad_pcb", ["net", 1, sexpr.Q("A")], seg]))
    b = pcb.read_board(path)
    stats = routing.widen(path, b, {"A": 0.6}, lambda a, c: 0.2, CU, 0.6, box(-1, -0.2, 5, 1), {})
    assert stats["A"] == (4.0, 4.0)  # the wide track would cross the outline edge at y = -0.2
    path.write_text(sexpr.dumps(["kicad_pcb", ["net", 1, sexpr.Q("A")], seg]))
    stats = routing.widen(path, b, {"A": 0.6}, lambda a, c: 0.2, CU, 0.6, box(-1, -1, 5, 1),
                          {"F.Cu": [box(-1, 0.2, 5, 1)]})
    assert stats["A"] == (4.0, 4.0)


def test_pair_clearance_uses_the_isolation_distance():
    classes = {"AC": {"clearance": 0.2}, "S": {"clearance": 0.3}}
    c = pcb._pair_clearance(type("D", (), {"board": {"clearance_mm": 0.2}})(), classes, [{"members": ["AC", "AC2"], "mm": 6.0}])
    assert c("AC", "S") == 6.0 and c("AC", "AC2") == 0.2 and c("S", "X") == 0.3


def test_open_nets_of_a_router_summary():
    s = {"failed_single": ["A"], "failed_multipoint": [{"net_name": "B"}], "pad_pairs_open": {"nets": ["A", "C"]}}
    assert pcb.krt_open_nets(s) == ["A", "B", "C"]


def test_fit_narrows_a_wide_track_at_a_foreign_pad(tmp_path):
    # net A: a 0.6 mm track from a pad centre (0, 0); a foreign pad of net B 0.5 mm above the first millimetre
    # (the router's pad-entry neck shipped full width): only that stretch is narrowed to keep 0.2 mm
    seg = ["segment", ["start", 0, 0], ["end", 6, 0], ["width", 0.6], ["layer", sexpr.Q("F.Cu")], ["net", 1],
           ["uuid", sexpr.Q("u")]]
    path = tmp_path / "x.kicad_pcb"
    path.write_text(sexpr.dumps(["kicad_pcb", ["net", 1, sexpr.Q("A")], ["net", 2, sexpr.Q("B")], seg]))
    b = pcb.read_board(path)
    b["footprints"] = {"U1": {"pads": [_pad("B", 0.5, 0.5, w=0.34, layers=("F.Cu",))]}}
    stats = routing.widen(path, b, {"A": 0.6}, lambda a, c: 0.2, CU, 0.6, box(-5, -5, 15, 5), {})
    narrow, total = stats["A"]
    assert abs(total - 6) < 1e-6 and 0 < narrow <= 1.5
    segs = pcb.read_board(path)["segments"]
    pad = box(0.33, 0.33, 0.67, 0.67)
    for s in segs:
        from shapely.geometry import LineString
        g = LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2)
        assert g.distance(pad) >= 0.2 - 1e-6
    assert max(s["w"] for s in segs) == 0.6 and min(s["w"] for s in segs) < 0.6
