"""Placer experiments from ASIC placement (docs/asic-ideas.md): pad blockage in the RUDY capacity, analytic global
placement as the annealing start, macro-first schedule. Each is off by default."""
from hwflow import placer

from test_placer import H, W, channel, check_incremental


def test_rudy_pads_incremental_and_capacity(tmp_path, monkeypatch):
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    plain = placer.Placer(d, parts, W, H, seed=3)
    plain.cost()
    monkeypatch.setenv("HWFLOW_RUDY_PADS", "1")
    pl = placer.Placer(d, parts, W, H, seed=3)
    assert pl.rudy_pads and not plain.rudy_pads
    pl.cost()
    assert pl.ecap.sum() < plain.ecap.sum()  # the pads take capacity, the header's THT pads on both layers
    assert (pl.ecap == (plain.cap - pl.blk).clip(0)).all()
    check_incremental(pl, parts)  # the incremental cost with the moving blockage equals the full one, undo included


def test_analytic_gradient_and_start(tmp_path, monkeypatch):
    import numpy as np
    from hwflow import gplace
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, W, H, seed=3)
    mv = pl._movable()
    for p in mv:
        p.x, p.y = pl.rng.uniform(10, W - 10), pl.rng.uniform(8, H - 8)
    g = gplace.Global(pl, mv)
    v = g.x0.copy()
    _, grad = g.cost(v, 1.0, 1.0, 0.5)
    num = np.zeros_like(v)
    for i in range(len(v)):
        e = np.zeros_like(v)
        e[i] = 1e-5
        num[i] = (g.cost(v + e, 1.0, 1.0, 0.5)[0] - g.cost(v - e, 1.0, 1.0, 0.5)[0]) / 2e-5
    assert np.abs(num - grad).max() < 1e-4 * max(1.0, np.abs(grad).max())
    pl.cost()
    ov0 = pl.t_ov
    gplace.global_place(pl, mv)  # the crowded start spread out and legalized
    pl.cost()
    assert ov0 > 5 and pl.t_ov < 0.05 * ov0
    monkeypatch.setenv("HWFLOW_ANALYTIC", "1")
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, W, H, seed=3)
    pl.run(3000)
    assert not [v for v in pl.violations() if "overlap" in v]


def test_macro_first_places_blocks_then_small_parts(tmp_path, monkeypatch):
    from test_blocks import BOOST, _design, _rigid
    from hwflow import blocks
    monkeypatch.setenv("HWFLOW_MACRO_FIRST", "1")
    d, libs = _design(tmp_path)
    parts = placer.build_parts(d, libs)
    (u,) = parts.blocks
    pl = placer.Placer(d, parts, 30, 22, seed=3)
    seen = []
    orig = placer.Placer._anneal
    monkeypatch.setattr(placer.Placer, "_anneal", lambda self, mv, *a, **k: (seen.append(sorted(
        getattr(m, "ref", "?") for m in mv)), orig(self, mv, *a, **k))[1])
    pl.run(3000)
    assert seen[0] == ["block:boost"]  # macro pass: the block alone (R1, R2 are small)
    assert seen[1] == ["R1", "R2"]  # then the small parts around it
    assert len(seen[2]) == 3  # then everything at a low temperature
    _rigid(parts, u, blocks.load("tps61299_boost"))
    assert not [v for v in pl.violations() if "overlap" in v]
    assert all(not parts[r].fixed for r in BOOST.values())


def test_detailed_placement_is_local_and_greedy(tmp_path, monkeypatch):
    from hwflow import gplace
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, W, H, seed=3)
    mv = pl._movable()
    for p in mv:
        p.x, p.y = pl.rng.uniform(10, W - 10), pl.rng.uniform(8, H - 8)
    gplace.global_place(pl, mv)
    before = pl.cost()
    start = {p.ref: (p.x, p.y) for p in mv}
    after = pl.detailed(mv)
    assert after <= before + 1e-6
    reach = placer.DP_PASSES * 2 * (placer.DP_RADIUS + max(placer.DP_STEPS))
    assert all(abs(p.x - start[p.ref][0]) + abs(p.y - start[p.ref][1]) <= reach for p in mv)
    monkeypatch.setenv("HWFLOW_ANALYTIC_DP", "1")  # flow 3 end to end: global, legalize, detailed, no anneal
    parts = placer.build_parts(d, libs)
    n0, a0 = len(placer.STAGES), placer.ANNEAL["moves"]
    placer.place(d, libs, seeds=1, iters=3000)
    assert [e["stage"] for e in placer.STAGES[n0:]] == ["global", "legalize", "detailed"]
    assert placer.ANNEAL["moves"] == a0
