"""Escape-aware placement (layout.place.escape, prototype behind HWFLOW_PLACE_ESCAPE)."""
import math

from test_placer import AREAS, H, W, channel, check_incremental

from hwflow.layout.place import escape, placer


def test_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("HWFLOW_PLACE_ESCAPE", raising=False)
    d, libs = channel(tmp_path)
    assert not escape.on(d)
    pl = placer.Placer(d, placer.build_parts(d, libs), W, H)
    assert pl.esc is None and "escape" not in pl.wt


def test_incremental_cost_equals_full_with_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("HWFLOW_PLACE_ESCAPE", "1")
    iso = [{"name": "a-b", "nets": ["A*"], "clearance_mm": 1.0, "creepage_mm": 3.0}]
    d, libs = channel(tmp_path, board_extra={"isolation": iso})
    parts = placer.build_parts(d, libs)
    parts.areas = AREAS
    pl = placer.Placer(d, parts, W, H, seed=3)
    assert pl.esc is not None
    check_incremental(pl, parts)
    assert pl.t_esc > 0, "the test must exercise the escape term"


def test_a_foreign_pad_in_front_of_a_slot_blocks_it(tmp_path, monkeypatch):
    """R0 alone: all four slots of each pad are free (the other pad of R0 blocks the inward one). A part of other
    nets pressed against R0's outer side blocks one direction only, so the pad still escapes."""
    monkeypatch.setenv("HWFLOW_PLACE_ESCAPE", "1")
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, W, H)
    for r, p in parts.items():  # everything far apart, R0 in the middle
        if not p.fixed:
            p.x, p.y = 4 + 3 * int(r[1:]), 4
    R0, R1 = parts["R0"], parts["R1"]
    R0.x, R0.y, R0.r = 18, 15, 0
    pl.cost()
    e = pl.esc
    si, b = e.slot_block("R0")
    assert len(si) == 6 and b.max() == 0  # 2 pads x 3 outward slots
    assert e.part_cost("R0") == 0
    R1.x, R1.y, R1.r = 18, 15 + 1.0, 0  # right under R0: its pads sit in R0's +y slots
    pl.cost()
    si, b = e.slot_block("R0")
    assert b.max() > 0.9
    assert e.blocked("R0") == 0 and e.part_cost("R0") < 1  # R1's fans compete, but a direction stays free
    assert math.isclose(pl.cost(), pl.cost_full(), rel_tol=1e-9)
