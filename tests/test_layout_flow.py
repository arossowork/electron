"""The layout flow (docs/layout-flow.md): macro stage, analytic global placement with RUDY-with-pad-blockage density
feedback, legalization, detailed local moves, the short anneal, and pin access with the narrow-first `necked` path."""
import numpy as np

from hwflow import asicflow, gplace, placer

from test_placer import H, W, channel


def test_rudy_map_takes_the_pad_blockage_off_the_capacity(tmp_path):
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, W, H, seed=3)
    pl.cost()
    R, cell, over = asicflow.rudy_map(pl)
    assert R.shape == pl.cap.shape and cell == pl.cell and over >= 0
    blk = np.zeros_like(pl.cap)
    for r in pl.parts:  # the header's THT pads take capacity on both layers
        e = pl._pad_blockage(r)
        if e is not None:
            blk[e[0]:e[0] + e[2].shape[0], e[1]:e[1] + e[2].shape[1]] += e[2]
    assert blk.sum() > 0
    ratio_plain = pl.grid / pl.cap
    assert (R >= ratio_plain - 1e-9).all() and (R > ratio_plain + 1e-9).any()  # less capacity: higher use


def test_global_placement_gradient_and_spread(tmp_path):
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


def test_detailed_placement_is_local_and_greedy(tmp_path):
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


def test_flow_stages_feedback_and_switches(tmp_path, monkeypatch):
    monkeypatch.delenv("HWFLOW_FLOW", raising=False)
    d, libs = channel(tmp_path)
    assert asicflow.on(d)  # the default
    for dens in ("1", "0"):
        monkeypatch.setenv("HWFLOW_ASIC_DENSITY", dens)
        n0, f0 = len(placer.STAGES), len(asicflow.FEEDBACK)
        parts, cost, viol = placer.place(d, libs, seeds=1, iters=2000)
        assert [e["stage"] for e in placer.STAGES[n0:]] == ["global", "legalize", "detailed", "anneal"]
        assert len(asicflow.FEEDBACK) - f0 == (3 if dens == "1" else 0)  # after GP rounds 2, 4 and 6
        assert not [v for v in viol if "overlap" in v]
    monkeypatch.delenv("HWFLOW_ASIC_DENSITY")
    monkeypatch.setenv("HWFLOW_ASIC_ANNEAL", "0")  # global, legalize, detailed: no annealing move at all
    n0, a0 = len(placer.STAGES), placer.ANNEAL["moves"]
    placer.place(d, libs, seeds=1, iters=2000)
    assert [e["stage"] for e in placer.STAGES[n0:]] == ["global", "legalize", "detailed"]
    assert placer.ANNEAL["moves"] == a0
    monkeypatch.delenv("HWFLOW_ASIC_ANNEAL")
    monkeypatch.setenv("HWFLOW_FLOW", "legacy")  # the escape hatch: plain annealing, no stages
    n0 = len(placer.STAGES)
    placer.place(d, libs, seeds=1, iters=2000)
    assert len(placer.STAGES) == n0 and not asicflow.on(d)


def test_macro_stage_places_the_block_first(tmp_path, monkeypatch):
    from test_blocks import BOOST, _design, _rigid
    from hwflow.core import blocks
    monkeypatch.delenv("HWFLOW_FLOW", raising=False)
    d, libs = _design(tmp_path)
    parts = placer.build_parts(d, libs)
    (u,) = parts.blocks
    pl = placer.Placer(d, parts, 30, 22, seed=3)
    assert [m.ref for m in asicflow.macros(pl, pl._movable())] == ["block:boost"]
    seen = []
    orig = gplace.Global.__init__
    monkeypatch.setattr(gplace.Global, "__init__", lambda self, p, mv: (seen.append(sorted(
        getattr(m, "ref", "?") for m in mv)), orig(self, p, mv))[1])
    n0 = len(placer.STAGES)
    pl.run(3000)
    assert seen == [["R1", "R2", "block:boost"], ["R1", "R2"]]  # every unit, then the small ones with the block held
    assert placer.STAGES[n0]["stage"] == "macro+global"
    _rigid(parts, u, blocks.load("tps61299_boost"))
    assert not [v for v in pl.violations() if "overlap" in v]
    assert all(not parts[r].fixed for r in BOOST.values())


def test_inflation_follows_the_map(tmp_path):
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


def test_pin_access_counts_directions_and_finds_a_walled_in_pad(tmp_path):
    from test_barrier import Libs, _design, _pad
    from hwflow import corridor, pinaccess
    # P1 alone: every direction is open. P2 in a ring of foreign pads 0.3 mm away: no track of any width leaves.
    ring = [_pad(str(k + 2), 20 + dx, 10 + dy, 0.8, 0.8) for k, (dx, dy) in
            enumerate([(-1.1, -1.1), (0, -1.1), (1.1, -1.1), (-1.1, 0), (1.1, 0), (-1.1, 1.1), (0, 1.1), (1.1, 1.1)])]
    fps = {"A": ([_pad("1", 5, 10, 0.8, 0.8)], ""), "R": ([_pad("1", 20, 10, 0.8, 0.8)] + ring, "")}
    pins = {"1": "SIG"} | {str(k + 2): f"N{k}" for k in range(8)}
    d = _design(tmp_path, {"U1": ("A", {"1": "SIG"}, True), "U2": ("R", pins, True)})
    acc = pinaccess.pin_access(d, corridor.Model(d, Libs(fps)))
    assert acc[("U1", "1")]["n"] == pinaccess.ACCESS_DIRS and not acc[("U1", "1")]["no_access"]
    assert acc[("U2", "1")]["n"] == 0 and acc[("U2", "1")]["floor"] == 0 and acc[("U2", "1")]["no_access"]
    assert pinaccess.summary(acc, 1)[0]["ref"] == "U2"


def test_a_pad_without_an_exit_at_its_width_routes_narrow_first(tmp_path, monkeypatch):
    from test_barrier import Libs, _design, _pad
    from hwflow import pcb
    # U2.1 (0.8 mm square at 20, 10) is boxed in on the left, top and bottom; to the right a 0.7 mm slit between two
    # foreign pads. A 0.6 mm track needs 2 x 0.2 clearance + 0.6 = 1.0 mm: no exit. The 0.15 mm floor needs 0.55 mm.
    box = [_pad("2", 18.9, 10, 1.0, 3.2), _pad("3", 19.5, 11.1, 2.2, 1.0), _pad("4", 19.5, 8.9, 2.2, 1.0),
           _pad("5", 21.3, 10.85, 1.4, 1.0), _pad("6", 21.3, 9.15, 1.4, 1.0)]
    fps = {"A": ([_pad("1", 5, 10, 0.8, 0.8)], ""), "R": ([_pad("1", 20, 10, 0.8, 0.8)] + box, "")}
    pins = {"1": "SIG"} | {str(k): f"N{k}" for k in range(2, 7)}
    comps = {"U1": ("A", {"1": "SIG"}, True), "U2": ("R", pins, True)}
    monkeypatch.setattr(pcb, "read_board", lambda p: None)  # the model from the design's placement
    monkeypatch.delenv("HWFLOW_FLOW", raising=False)
    d = _design(tmp_path, comps, nets={"SIG": {"width_mm": 0.6}})
    nk = asicflow.necked(d, Libs(fps), tmp_path / "x.kicad_pcb")
    assert set(nk) == {"SIG"} and nk["SIG"]["need_mm"] == 0.6 and nk["SIG"]["pad"] == "U2.1"
    assert asicflow.necked(d, Libs(fps), tmp_path / "x.kicad_pcb", nets=["N2"]) == {}
    d = _design(tmp_path, comps, nets={"SIG": {"width_mm": 0.2}})  # 0.2 mm fits the slit: nothing to neck
    assert asicflow.necked(d, Libs(fps), tmp_path / "x.kicad_pcb") == {}
    monkeypatch.setenv("HWFLOW_ASIC_NECK", "0")
    d = _design(tmp_path, comps, nets={"SIG": {"width_mm": 0.6}})
    assert asicflow.necked(d, Libs(fps), tmp_path / "x.kicad_pcb") == {}
