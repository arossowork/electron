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
    legs = [(pl.decap[k][:4], lim) for k, lim in enumerate(pl.dec_lim) if lim is not None]
    assert legs == [(("J1", "2", "R1", "1"), (3.0 * placer.BUDGET_FILL, 3.0 / 1.25))]
    assert pl.bud == [("stub", "J1", "2", "R1", "1", 3.0 / 1.25)]
    j = parts["J1"].pad("2")
    k = len(pl.decap) - 1
    r1 = parts["R1"]
    r1.r = 0
    r1.x, r1.y = j[0] + 1.0 - r1.pads["1"][0], j[1] - r1.pads["1"][1]
    near = pl._decap_len(k)
    assert pl.budget_violations() == []
    r1.x += 10.0
    far = pl._decap_len(k)
    # past the soft limit place_budget_w per mm, past max_mm the hard weight too
    assert far - near > 10.0 + (8.0 * 40.0 + 8.0 * placer.BUDGET_HARD_W) / pl.wt["decouple"]
    (fixed, msg), = pl.budget_violations()
    assert not fixed and msg.startswith("budget stub: J1.2 -> R1.1 pads 11.00 mm apart (straight line), over its 2.40 mm placement limit")
    pl.cost()
    assert any(v.startswith("budget stub") for v in pl.violations())
    assert [u.ref for u in pl._violators([msg])] == ["R1"]
    # the seed puts R1's pad next to the anchor pad, just outside J1's courtyard
    pl.budget_seed()
    assert pl._bud_dist(0) <= abs(parts["J1"].box()[2] - j[0]) + 1e-6
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
    assert all(x is None for x in pl.dec_lim)


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
