"""The design's own rules as placer terms: copper_len budgets pull their pads together (Placer._init_budget_pairs),
and a layout block's copper keeps the isolation and class distances like a pad (Placer._block_copper_pads)."""
import math

import yaml

from hwflow.core import blocks
from hwflow.core.design import Design
from hwflow.core.libs import Libs
from hwflow.layout.place import placer

from test_blocks import _boost_parts, _design
from test_placer import channel, check_incremental


def _budget_channel(tmp_path, max_mm=3.0):
    d, _ = channel(tmp_path)
    raw = yaml.safe_load((tmp_path / "design.yaml").read_text())
    # R1.1 is on A1 with J1.2 (fixed): a TVS-like part whose only other tie is a net that does not pull it there
    raw["budgets"] = [{"name": "stub", "kind": "copper_len", "path": ["J1.2 -> R1.1"], "max_mm": max_mm}]
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    return d, Libs(design=d)


def test_copper_len_budget_is_a_hard_placer_pair(tmp_path):
    d, libs = _budget_channel(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"], seed=1)
    legs = [(pl.decap[k][:4], lim) for k, lim in enumerate(pl.dec_lim) if lim is not None and lim["base"]]
    assert legs == [(("J1", "2", "R1", "1"), {"soft": 3.0 * placer.BUDGET_FILL, "hard": 3.0 / 1.25,
                                               "alt": [("R1", "1")], "base": True})]
    assert ("stub", "J1", "2", [("R1", "1")], 3.0 / 1.25) in pl.bud
    j = parts["J1"].pad("2")
    k = next(i for i, lim in enumerate(pl.dec_lim) if lim is not None and lim["base"])
    r1 = parts["R1"]
    r1.r = 0
    r1.x, r1.y = j[0] + 1.0 - r1.pads["1"][0], j[1] - r1.pads["1"][1]
    near = pl._decap_len(k)
    assert not [v for v in pl.budget_violations() if "stub" in v[1]]
    r1.x += 10.0
    far = pl._decap_len(k)
    # past the soft limit place_budget_w per mm, past max_mm the hard weight too
    assert far - near > 10.0 + (8.0 * 40.0 + 8.0 * placer.BUDGET_HARD_W) / pl.wt["decouple"]
    (fixed, msg), = [v for v in pl.budget_violations() if "stub" in v[1]]
    assert not fixed and msg.startswith("budget stub: J1.2 -> R1.1 pads 11.00 mm apart (straight line), over its 2.40 mm placement limit")
    pl.cost()
    assert any(v.startswith("budget stub") for v in pl.violations())
    assert [u.ref for u in pl._violators([msg])] == ["R1"]
    # the seed puts R1's pad next to the anchor pad, just outside J1's courtyard
    pl.budget_seed()
    kb = next(i for i, b in enumerate(pl.bud) if b[0] == "stub")
    assert pl._bud_dist(kb) <= abs(parts["J1"].box()[2] - j[0]) + 1e-6
    check_incremental(pl, parts)


def test_budget_violation_makes_the_placement_illegal(tmp_path):
    from hwflow.layout.place import legality
    d, libs = _budget_channel(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 36, 30, seed=1)
    parts["R1"].x, parts["R1"].y = 30, 25
    v = pl.violations()
    pv = legality.placement_verdict(d, parts, 36, 30, v)
    assert not pv["ok"] and pv["back_to"] == "layout" and "budget stub: J1.2 -> R1.1" in pv["msg"]


def test_budget_pairs_off_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("HWFLOW_PLACE_BUDGETS", "0")
    d, libs = _budget_channel(tmp_path)
    pl = placer.Placer(d, placer.build_parts(d, libs), 36, 30, seed=1)
    assert all(x is None for x in pl.dec_lim) and pl.bud == []


def test_block_copper_keeps_the_isolation_distance(tmp_path):
    # R2's X pad is an isolation member: the block copper (here the boost's tracks) must keep 3 mm from it like a pad
    comps = _boost_parts()
    board = {"isolation": [{"name": "hv", "nets": ["X"], "clearance_mm": 3.0}]}
    d, libs = _design(tmp_path, comps, board=board)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 30, 22, seed=1)
    (u,) = pl.units
    keys = [k for k in pl.pad_grp if k[0] == u.ref and k[1].startswith(placer.BLOCK_CU)]
    assert len(keys) == len(blocks.local_copper(d, u.name))
    # the pseudo-pads sit on the board copper the writer puts down (blocks.copper), in any rotation of the unit
    for r in (0, 90):
        u.x, u.y, u.r = 15, 11, r
        u.sync(pl.parts)
        tracks, vias = blocks.copper(d, parts)
        segs = [(min(t["x1"], t["x2"]) - t["w"] / 2, min(t["y1"], t["y2"]) - t["w"] / 2,
                 max(t["x1"], t["x2"]) + t["w"] / 2, max(t["y1"], t["y2"]) + t["w"] / 2) for t in tracks]
        got = [placer.part_pad_box(pl.parts[u.ref], k[1]) for k in keys[:len(segs)]]
        for a, b in zip(got, segs):
            assert all(math.isclose(x, y, abs_tol=1e-6) for x, y in zip(a, b)), (r, a, b)
    # an isolation pad right next to a block track is short of the distance, also where no block pad is near
    u.r = 0
    u.sync(pl.parts)
    k = keys[0]
    bx = placer.part_pad_box(pl.parts[u.ref], k[1])
    r2 = parts["R2"]
    r2.r = 0
    r2.x, r2.y = bx[2] + 1.0 - (r2.pads["1"][0]), (bx[1] + bx[3]) / 2 - r2.pads["1"][1]
    assert pl._iso_short(k, ("R2", "1")) > 1.0
    assert math.isclose(pl.cost(), pl.cost_full(), rel_tol=1e-9)


def test_fixed_leg_keeps_the_routing_allowance(tmp_path):
    """Two fixed ends are only reported (a fixed violation: the floorplan gate fails it), but against the same limit
    as a movable leg: thermo-e2e-1 fixed D2.1 4.5 mm from J2.1 under a 5 mm budget and routed 5.8 mm."""
    d, libs = _budget_channel(tmp_path)
    parts = placer.build_parts(d, libs)
    j = parts["J1"].pad("2")
    r1 = parts["R1"]
    r1.r, r1.fixed = 0, True
    r1.x, r1.y = j[0] + 2.7 - r1.pads["1"][0], j[1] - r1.pads["1"][1]  # under max_mm 3, over 3 / 1.25
    pl = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"], seed=1)
    assert ("stub", "J1", "2", [("R1", "1")], 3.0 / 1.25) in pl.bud
    assert all(x is None or not x["base"] for x in pl.dec_lim)  # no placer pair: nothing to move
    (fixed, msg), = [v for v in pl.budget_violations() if "stub" in v[1]]
    assert fixed and "pads 2.70 mm apart" in msg and placer.FIXED_LEG_RISK in msg  # under max_mm itself: a risk
    pl.cost()
    assert not any(v.startswith("budget stub") for v in pl.violations())
    assert any(v.startswith("budget stub") for v in pl.fixed_violations())
    from hwflow.layout.place import floorplan
    assert floorplan._fixed_severity(parts, {"regions": []}, msg, 1.0)[0] is None
    r1.x += 1.0  # 3.70 mm: past max_mm itself, a failure
    (fixed, msg), = [v for v in pl.budget_violations() if "stub" in v[1]]
    assert "past max_mm itself" in msg and floorplan._fixed_severity(parts, {"regions": []}, msg, 1.0)[0] is False


def test_decap_max_mm_is_a_hard_leg_to_the_nearest_ic_pad(tmp_path, monkeypatch):
    monkeypatch.setenv("HWFLOW_PLACE_DECAP_HARD", "1")
    # channel(): R0 decouples J1 on net A0 (J1.1); the leg's limit is board.decap_max_mm over the routing allowance
    d, libs = channel(tmp_path, board_extra={"decap_max_mm": 2.5})
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 36, 30, seed=1)
    (leg,) = [b for b in pl.bud if b[0] == "decap_max_mm R0"]
    assert leg[1:4] == ("R0", "1", [("J1", "1")]) and math.isclose(leg[4], 2.5 / 1.25)
    k = next(i for i, lim in enumerate(pl.dec_lim) if lim is not None and not lim["base"])
    r0, j = parts["R0"], parts["J1"].pad("1")
    r0.r = 0
    r0.x, r0.y = j[0] + 1.5 - r0.pads["1"][0], j[1] - r0.pads["1"][1]
    assert pl._decap_len(k) == 0.0 and not [v for v in pl.budget_violations() if "R0" in v[1]]
    r0.x += 5.0
    assert pl._decap_len(k) > 4.0 * placer.BUDGET_HARD_W / pl.wt["decouple"]
    (fixed, msg), = [v for v in pl.budget_violations() if "decap_max_mm R0" in v[1]]
    assert not fixed and msg.startswith("budget decap_max_mm R0: R0.1 -> J1.1 pads 6.50 mm apart")
    pl.cost()
    assert msg in pl.violations()
    assert [u.ref for u in pl._violators([msg])] == ["R0"]
    check_incremental(pl, parts)


def test_decap_hard_is_off_by_default(tmp_path):
    d, libs = channel(tmp_path)
    pl = placer.Placer(d, placer.build_parts(d, libs), 36, 30, seed=1)
    assert pl.bud == [] and all(x is None for x in pl.dec_lim)


def test_edge_part_off_its_edge_is_a_violation(tmp_path, monkeypatch):
    # channel(): R5 has placement.edge top; within placer.EDGE_TOL of it is legal, farther a violation unstick repairs
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 36, 30, seed=1)
    r5 = parts["R5"]
    for r, p in parts.items():
        if not p.fixed:
            p.x, p.y = 6.0 + 4 * int(r[1:]), 15.0
    r5.y = 15.0
    pl.cost()
    msg = next(v for v in pl.violations() if v.startswith("R5 off its top edge"))
    assert [u.ref for u in pl._violators([msg])] == ["R5"]
    pl.unstick()
    assert pl.part_edge["R5"] <= placer.EDGE_TOL
    assert not any(v.startswith("R5 off its") for v in pl.violations())
    monkeypatch.setenv("HWFLOW_PLACE_EDGE_HARD", "0")
    r5.y = 15.0
    pl.cost()
    assert not any(v.startswith("R5 off its") for v in pl.violations())


def _sep_channel(tmp_path, board_extra):
    d, libs = channel(tmp_path, board_extra=board_extra)
    d.raw["components"]["R6"]["role"] = "antenna"
    d.raw["components"]["R7"]["symbol"] = "Relay:G5LE-1"
    for ref, c in d.raw["components"].items():
        d.components[ref].data = c
    return d, libs


def test_stated_antenna_clearance_is_hard(tmp_path, monkeypatch):
    d, libs = _sep_channel(tmp_path, {"antenna_clearance_mm": 8})
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 36, 30, seed=1)
    (k,) = pl.sep_hard
    assert pl.seps[k][5] == "antenna" and pl.wt["antenna"] == pl.wt["isolation"]
    for r, p in parts.items():
        if not p.fixed:
            p.x, p.y = 6.0 + 3 * int(r[1:]), 4.0 + 3 * int(r[1:])
    parts["R7"].x, parts["R7"].y = parts["R6"].x + 3.0, parts["R6"].y
    pl.cost()
    msg = next(v for v in pl.violations() if v.startswith("separation R6/R7: "))
    assert "board.antenna_clearance_mm 8 mm" in msg
    assert [u.ref for u in pl._violators([msg])][0] in ("R6", "R7")
    check_incremental(pl, parts)
    monkeypatch.setenv("HWFLOW_PLACE_SEP_HARD", "0")
    pl = placer.Placer(d, parts, 36, 30, seed=1)
    assert not pl.sep_hard and pl.wt["antenna"] == 4.0


def test_default_antenna_clearance_stays_soft(tmp_path):
    d, libs = _sep_channel(tmp_path, {})
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 36, 30, seed=1)
    assert any(e[5] == "antenna" for e in pl.seps) and not pl.sep_hard
    parts["R7"].x, parts["R7"].y = parts["R6"].x + 3.0, parts["R6"].y
    pl.cost()
    assert not any(v.startswith("separation ") for v in pl.violations())


def test_fixed_decap_is_not_a_leg(tmp_path, monkeypatch):
    monkeypatch.setenv("HWFLOW_PLACE_DECAP_HARD", "1")
    # a cap the placement cannot move (both ends fixed) is left to postlayout.decap_paths, which warns on the copper:
    # the layout corpus cutouts fix every part where a real routed board had it, often past decap_max_mm
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    parts["R0"].fixed = True
    pl = placer.Placer(d, parts, 36, 30, seed=1)
    assert not [b for b in pl.bud if b[0].startswith("decap_max_mm")]
