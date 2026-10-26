"""Routable first pass (layout/route/firstpass.py, layout/place/hotspot.py; experiment, off by default): the step
switch, the hotspot map (slope out of a hot spot, owner exemption, bumps) and the placer term (incremental cost
equals the reference cost)."""
import math

import numpy as np

from hwflow.layout.place import hotspot, placer
from hwflow.layout.route import firstpass
from test_placer import H, W, channel, check_incremental


def test_steps_off_by_default_and_parsed(monkeypatch):
    monkeypatch.delenv("HWFLOW_FIRSTPASS", raising=False)
    assert hotspot.steps() == set() and not firstpass.on()
    monkeypatch.setenv("HWFLOW_FIRSTPASS", "1")
    assert hotspot.steps() == set(hotspot.STEPS)
    monkeypatch.setenv("HWFLOW_FIRSTPASS", "quick, hot")
    assert hotspot.steps() == {"quick", "hot"} and hotspot.on()
    monkeypatch.setenv("HWFLOW_FIRSTPASS", "0")
    assert not firstpass.on()


def test_design_key_turns_it_on(monkeypatch):
    monkeypatch.delenv("HWFLOW_FIRSTPASS", raising=False)

    class D:
        raw = {"placement": {"firstpass": "roads"}}
    assert hotspot.steps(D()) == {"roads"}


def test_window_excess_slopes_out_of_a_hot_cluster():
    shape = (40, 40)
    ratio = np.zeros((33, 33))
    ratio[14:19, 14:19] = 6.0  # a cluster of hot windows (corner grid), side 8 cells
    g = hotspot.window_excess({"maps": [(4.0, 8, ratio)]}, shape)
    centre, edge, out = g[20, 20], g[15, 15], g[35, 35]
    assert centre > edge > out == 0.0


def test_owner_pays_nothing_in_its_own_spot():
    g = np.zeros((10, 10))
    g[2:5, 2:5] = 1.0
    labels = np.zeros((10, 10), int)
    labels[2:5, 2:5] = 1
    hm = hotspot.HotMap(g, 0.5, labels, {"U1": {1}})
    assert math.isclose(hm.at(1.75, 1.75), 1.0)
    assert hm.at(1.75, 1.75, "U1") == 0.0 and math.isclose(hm.at(1.75, 1.75, "R1"), 1.0)
    assert hm.at(-5, -5) == 0.0


def test_bumps_peak_at_points():
    b = hotspot.bumps([(2.25, 2.25)], (10, 10), 0.5, h=3.0, sigma=0.5)
    assert math.isclose(b.max(), 3.0) and b[4, 4] == b.max() and b[0, 9] < 1e-6


def test_placer_hotspot_term_is_incremental(tmp_path):
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, W, H, seed=1)
    g = np.zeros((int(W / 0.5), int(H / 0.5)))
    g[20:50, 15:45] = 0.8
    pl.hot = hotspot.HotMap(hotspot.bumps([(18.0, 15.0)], g.shape, 0.5) + g, 0.5)
    pl.wt["hotspot"] = 5.0
    check_incremental(pl, parts)
    assert pl.t_hot > 0
    hotspot.remove(pl)
    assert pl.cost() == pl.cost_full() or math.isclose(pl.cost(), pl.cost_full(), rel_tol=1e-9)
