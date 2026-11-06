"""Decoupling legs (trunks.decap_tiers): krt routes each `decouples:` part's pad to the nearest same-net pad of its IC
first, a two-pad route, so the net's tree cannot join the cap by a cheaper edge elsewhere (timed-bathroom-vent: C8, C9
and C6 1.7-2.6 mm from their pins had 20-23 mm copper paths, over the 4 mm decap-path limit)."""

import yaml

from hwflow.core.design import Design
from hwflow.layout.route import trunks


def _pad(n, x, y, net):
    return {"number": n, "x": x, "y": y, "net": net, "layers": ["F.Cu"]}


def _fake(tmp_path):
    comps = {"U1": {"pins": {"1": "VDD", "2": "GND", "3": "VDD", "4": "VIO"}},
             "C1": {"pins": {"1": "VDD", "2": "GND"}, "decouples": "U1"},
             "C2": {"pins": {"1": "VDD", "2": "GND"}, "decouples": "U1"},
             "C3": {"pins": {"1": "VIO", "2": "GND"}, "decouples": "U1"},
             "C4": {"pins": {"1": "VDD", "2": "GND"}},                    # bulk cap, no decouples: not a leg
             "R1": {"pins": {"1": "VDD", "2": "VIO"}}}
    raw = {"project": "dec", "board": {"layers": 2, "gnd_pour": True}, "components": comps}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    # U1 pin 1 at x=0, pin 3 at x=10; C1 next to pin 3, C2 next to pin 1; C4 nearer than either to the other pin
    board = {"footprints": {
        "U1": {"pads": [_pad("1", 0, 0, "VDD"), _pad("2", 5, 0, "GND"), _pad("3", 10, 0, "VDD"), _pad("4", 15, 0, "VIO")]},
        "C1": {"pads": [_pad("1", 12, 2, "VDD"), _pad("2", 12, 4, "GND")]},
        "C2": {"pads": [_pad("1", -1, 1, "VDD"), _pad("2", -1, 3, "GND")]},
        "C3": {"pads": [_pad("1", 15, 3, "VIO"), _pad("2", 15, 5, "GND")]},
        "C4": {"pads": [_pad("1", 9, 1, "VDD"), _pad("2", 9, 3, "GND")]},
        "R1": {"pads": [_pad("1", 30, 0, "VDD"), _pad("2", 30, 2, "VIO")]}}}
    classes = {"VDD": {"width": 0.4}, "VIO": {"width": 0.25}, "GND": {"width": 0.4}}
    return d, board, classes


def test_each_decoupler_gets_a_two_pad_leg_to_its_nearest_same_net_ic_pad(tmp_path):
    d, board, classes = _fake(tmp_path)
    t = trunks.decap_tiers(d, board, None, classes, {"GND"})
    # one leg per net per tier, the shortest first; widest class first within a round; ground (pour) never a leg
    assert t == [(0.4, {"VDD": {("C2", "1"), ("U1", "1")}}), (0.25, {"VIO": {("C3", "1"), ("U1", "4")}}),
                 (0.4, {"VDD": {("C1", "1"), ("U1", "3")}})]
    for _, keep in t:
        assert all(len(v) == 2 for v in keep.values())
    # restricted to the nets of the call; a ground net routed (no pour) is skipped only by `skip`
    assert trunks.decap_tiers(d, board, {"VIO"}, classes, {"GND"}) == [(0.25, {"VIO": {("C3", "1"), ("U1", "4")}})]


def test_a_leg_tier_board_leaves_only_the_two_leg_pads_on_the_net(tmp_path):
    """The split board of a leg tier: the router sees VDD as the cap pad and the IC pad only, so its route is the
    direct leg (the other VDD pads are obstacles on VDD~off until join)."""
    from hwflow.core import sexpr
    d, board, classes = _fake(tmp_path)
    (w, keep), *_ = trunks.decap_tiers(d, board, None, classes, {"GND"})
    fp = "".join(f' (footprint "x" (property "Reference" "{r}")'
                 + "".join(f' (pad "{p["number"]}" smd (net "{p["net"]}"))' for p in v["pads"]) + ")"
                 for r, v in board["footprints"].items())
    src = tmp_path / "b.kicad_pcb"
    src.write_text(f"(kicad_pcb{fp})")
    t = sexpr.loads(trunks.split(src, tmp_path / "s.kicad_pcb", keep).read_text())
    on = {(trunks._ref(f), str(p[1])) for f in sexpr.find_all(t, "footprint") for p in sexpr.find_all(f, "pad")
          if str(sexpr.find(p, "net")[1]) == "VDD"}
    assert on == {("C2", "1"), ("U1", "1")}
