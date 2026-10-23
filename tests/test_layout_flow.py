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


def test_combined_flow_pin_access_incremental(tmp_path, monkeypatch):
    monkeypatch.setenv("HWFLOW_FLOW", "asic")
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, W, H, seed=3)
    assert pl.pa is not None and pl.wt["access"] > 0
    check_incremental(pl, parts)  # the pin access term moves with the parts, undo included
    monkeypatch.setenv("HWFLOW_ASIC_ACCESS", "0")
    assert placer.Placer(d, parts, W, H, seed=3).pa is None


def test_pin_access_flags_a_walled_pad(tmp_path, monkeypatch):
    from hwflow import asicflow
    monkeypatch.setenv("HWFLOW_FLOW", "asic")
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, W, H, seed=3)
    for i, r in enumerate(["R0", "R1", "R2"]):  # three resistors side by side, 0.1 mm apart: the middle one is walled
        parts[r].x, parts[r].y, parts[r].r = 20.0, 10.0 + i * 1.0, 0.0
    for i in range(3, placer_N()):
        parts[f"R{i}"].x, parts[f"R{i}"].y = 6.0 + 2.5 * i, 26.0
    parts["R5"].x, parts["R5"].y = 28.0, 15.0  # alone: free on every side but towards its own other pad
    pl.cost()
    s = {x[0]: x for x in pl.pa.subj["R1"]}
    assert pl.pa.exits("R1", s["1"], pl.buckets) <= 1
    s0 = {x[0]: x for x in pl.pa.subj["R5"]}
    assert pl.pa.exits("R5", s0["1"], pl.buckets) >= 3
    assert pl.pa.flagged() >= 2 and asicflow.PinAccess.value(0) == 2


def placer_N():
    from test_placer import N
    return N


def test_combined_flow_stages_and_feedback(tmp_path, monkeypatch):
    from hwflow import asicflow
    monkeypatch.setenv("HWFLOW_FLOW", "asic")
    d, libs = channel(tmp_path)
    for src in ("rudy", "none"):
        monkeypatch.setenv("HWFLOW_ASIC_DENSITY", src)
        n0, f0 = len(placer.STAGES), len(asicflow.FEEDBACK)
        parts, cost, viol = placer.place(d, libs, seeds=1, iters=2000)
        assert [e["stage"] for e in placer.STAGES[n0:]] == ["global", "legalize", "detailed", "anneal"]
        assert len(asicflow.FEEDBACK) - f0 == (3 if src == "rudy" else 0)
        assert not [v for v in viol if "overlap" in v]


def test_inflation_follows_the_map(tmp_path):
    import numpy as np
    from hwflow import asicflow, gplace
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, W, H, seed=3)
    mv = pl._movable()
    for i, u in enumerate(mv):  # spread on a grid, 6 mm apart
        u.x, u.y = 8.0 + 6 * (i % 4), 6.0 + 6 * (i // 4)
    g = gplace.Global(pl, mv)
    R = np.ones((int(W) + 1, int(H) + 1))
    u = mv[0]
    R[int(u.x) - 2:int(u.x) + 3, int(u.y) - 2:int(u.y) + 3] = 3.0  # one hot spot under the first unit
    asicflow._inflate(g, R, 1.0, 1.0, "test")
    k = list(g.mbi).index(0)
    assert g.infl[k] > 1.0 and g.infl.max() <= np.sqrt(asicflow.INFL_MAX) + 1e-9
    assert (g.infl[[i for i in range(len(g.infl)) if g.mbi[i] != 0]] < g.infl[k]).all()
