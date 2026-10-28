"""The routability predictor (routability.py): raster primitives, window cuts, the exact bounds, the verdict."""
import numpy as np

from hwflow.layout.place import placer, routability

from test_placer import channel


def _placed(tmp_path, **kw):
    d, libs = channel(tmp_path, **kw)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"], seed=3)
    pl.run(1500)
    return d, libs, parts


def test_raster_distance_map_and_free_space(tmp_path):
    d, libs, parts = _placed(tmp_path)
    R = routability.Raster(d, libs, parts=parts)
    assert R.pad.shape == (2, R.nx, R.ny) and R.res >= routability.RES_MIN
    D = R.edt(0)
    assert (D[R.pad[0] != 0] == 0).all() and D.max() > 3  # zero on copper, wide open between the parts
    F = R.free(0)
    assert F.sum() < (D > 0).sum()  # the clearance and half width are taken off the free space
    lab, n = R.components(0)
    assert n >= 1 and lab.max() == n
    rg = R.ridge(0)
    assert rg.any() and not rg[R.pad[0] != 0].any()
    assert R.via_free().sum() < R.nx * R.ny


def test_window_cuts_rise_with_fewer_layers_and_wider_tracks(tmp_path):
    d, libs, parts = _placed(tmp_path)
    big = routability.predict(d, libs, parts=parts, corridors=False)
    (tmp_path / "s").mkdir()
    d2, libs2 = channel(tmp_path / "s", board_extra={"layers": 1, "track_mm": 0.8})
    parts2 = placer.build_parts(d2, libs2)
    for r, p in parts2.items():
        p.x, p.y, p.r = parts[r].x, parts[r].y, parts[r].r
    small = routability.predict(d2, libs2, parts=parts2, corridors=False)
    assert small["max_ratio"] > big["max_ratio"]
    assert small["hot_area_mm2"] >= big["hot_area_mm2"]
    assert set(big["per_net"]) == {n for n in d.nets() if len(d.nets()[n]) >= 2}
    assert big["verdict"] in routability.VERDICTS and big["message"].startswith("routability ")


def test_verdict_thresholds():
    assert routability.verdict_of(0.0) == "routable"
    assert routability.verdict_of(0.05) == "marginal"
    assert routability.verdict_of(routability.HARD_SHARE_FAIL) == "not routable"
    assert np.isfinite(routability.RATIO_HOT)


def test_run_counting():
    v = np.array([0, 1, 1, 1, 0, 1, 0, 0, 1, 1], bool)
    assert routability._nruns(v) == 3
    # runs of 0.3, 0.1 and 0.2 mm at res 0.1 and pitch 0.2: 2 + 1 + 2 centre lines
    assert routability._runs(v, 0.1, 0.2) == 5
    assert routability._runs(np.zeros(4, bool), 0.1, 0.2) == 0


def test_exact_bounds_prove_an_impossible_board(tmp_path):
    d, libs, parts = _placed(tmp_path)
    ok = routability.predict(d, libs, parts=parts, corridors=False, access=False)
    assert not ok["proofs"] and ok["bounds"]["line_x"]["ratio"] < 1
    (tmp_path / "s").mkdir()
    # one layer and tracks too wide for any gap between the pads: the straight cuts and rings cannot pass the nets
    d2, libs2 = channel(tmp_path / "s", board_extra={"layers": 1, "track_mm": 6.0})
    parts2 = placer.build_parts(d2, libs2)
    for r, p in parts2.items():
        p.x, p.y, p.r = parts[r].x, parts[r].y, parts[r].r
    bad = routability.predict(d2, libs2, parts=parts2, corridors=False, access=False)
    assert bad["bounds"]["line_x"]["ratio"] > ok["bounds"]["line_x"]["ratio"]
    if bad["proofs"]:
        assert bad["verdict"] == "not routable" and "proven" in bad["message"]


def test_forced_corridor_proof():
    rows = [{"net": "A", "plain": 0.4, "width": 0.5, "margin": -0.1}, {"net": "B", "plain": 1.2, "width": 1.0,
                                                                      "margin": 0.2}]
    out = routability.forced_corridors(rows)
    assert len(out) == 1 and out[0].startswith("net A")


def test_supply_takes_the_face_of_the_pads():
    # a back-side window escapes on B.Cu: before, F.Cu (nearly free on a back-side board) was the pad layer everywhere
    f, b = np.full((1, 1), 10.0), np.full((1, 1), 2.0)
    vias = np.full((1, 1), 1.0)
    on = lambda n: np.full((1, 1), n)  # noqa: E731
    assert routability._supply([f, b], vias, [on(0), on(5)])[0, 0] == 3.0  # B.Cu tracks + 1 via
    assert routability._supply([f, b], vias, [on(5), on(0)])[0, 0] == 11.0
    assert routability._supply([f, b], vias, [on(0), on(0)])[0, 0] == 11.0  # no SMD pad: the better face


def test_congestion_gate_names_the_spot_its_parts_and_nets(tmp_path):
    d, libs, parts = _placed(tmp_path)
    res = routability.predict(d, libs, parts=parts, corridors=False, access=False)
    assert not res["gate"]["fail"]  # the channel board routes
    r = res["_ratio"].copy()
    p = parts["R2"]
    i, j = int(p.x / routability.POS_MM), int(p.y / routability.POS_MM)
    r[i - 3:i + 4, j - 3:j + 4] = routability.RATIO_FAIL + 1  # 12.25 mm2 over the gate around R2
    res["_ratio"] = r
    g = routability.gate(res, d, parts)
    assert g["fail"] and g["area_mm2"] >= routability.AREA_FAIL_MM2
    h = g["spots"][0]
    assert "R2" in h["refs"] and "R2" in h["movable"] and h["nets"]
    msg = routability.gate_message(g, 2)
    assert "hot spot x" in msg and "R2" in msg and "Moves: spread the movable parts" in msg


def test_budget_trunk_adds_width_on_its_pads_only(tmp_path):
    # the layout routes a copper_r width on the budget path only (budgets.trunks): the prediction counts the trunk's
    # extra width where its pads lie on both sides of a cut, not the whole net at the budget width
    d, libs, parts = _placed(tmp_path)
    net = max((n for n, p in d.nets().items() if len(p) >= 3), key=lambda n: len(d.nets()[n]), default=None)
    net = net or next(n for n, p in d.nets().items() if len(p) >= 2)
    pads = sorted((str(r), str(p)) for r, p in d.nets()[net])[:2]
    R = routability.Raster(d, libs, parts=parts)
    base = routability.window_map(R, d)
    assert base["extra"] == {}
    d.budget_trunks = {net: [(base["widths"][net] + 1.0, pads)]}
    wm = routability.window_map(R, d)
    pts, add = wm["extra"][net]
    assert len(pts) == 2 and abs(add - 1.0) < 1e-9
    assert (wm["ratio"] >= base["ratio"] - 1e-9).all() and wm["ratio"].max() > base["ratio"].max()
    assert set(wm["nets"]) == set(base["nets"])  # the trunk is not a net of its own (hard share, per-net rows)
    d.budget_trunks = {}
