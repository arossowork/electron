"""L9: congestion (RUDY) term and the incremental placer cost."""
import math
import random

import yaml

from hwflow import placer
from hwflow.design import Design
from hwflow.libs import Libs

W, H, N = 36, 30, 10


def channel(tmp_path, congestion_rules=True):
    """Two fixed connectors on opposite edges, N crossing nets, one series resistor per net. Wirelength alone puts
    every resistor on its straight line, so the nets pile up in the middle of the board."""
    hdr = f"Connector_PinHeader_2.54mm:PinHeader_1x{N:02d}_P2.54mm_Vertical"
    sym = f"Connector_Generic:Conn_01x{N:02d}"
    yj = (H - 2.54 * (N - 1)) / 2
    comps = {
        "J1": {"value": "hdr", "symbol": sym, "footprint": hdr, "pins": {str(i + 1): f"A{i}" for i in range(N)},
               "placement": {"x": 3, "y": yj, "rot": 0, "fixed": True}},
        "J2": {"value": "hdr", "symbol": sym, "footprint": hdr, "pins": {str(i + 1): f"B{N - 1 - i}" for i in range(N)},
               "placement": {"x": W - 3, "y": yj, "rot": 0, "fixed": True}},
    }
    for i in range(N):
        comps[f"R{i}"] = {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
                          "pins": {"1": f"A{i}", "2": f"B{i}"}, "group": "g" if i < 3 else None,
                          "decouples": "J1" if i == 0 else None}
        comps[f"R{i}"] = {k: v for k, v in comps[f"R{i}"].items() if v is not None}
    comps["R5"]["placement"] = {"edge": "top"}
    nets = {f"{p}{i}": {"class": "signal"} for i in range(N) for p in "AB"}
    board = {"width_mm": W, "height_mm": H, "layers": 2, "track_mm": 0.4, "clearance_mm": 0.3, "gnd_pour": False}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(
        {"project": "chan", "board": board, "nets": nets, "components": comps}))
    d = Design.load(tmp_path)
    return d, Libs(design=d)


def test_incremental_cost_equals_full(tmp_path):
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, W, H, seed=3)
    rng = random.Random(5)
    movable = [p for p in parts.values() if not p.fixed]
    for p in movable:  # crowded start so there are overlaps, group, edge and congestion terms
        p.x, p.y = rng.uniform(12, 24), rng.uniform(10, 20)
    cur = pl.cost()
    assert math.isclose(cur, pl.cost_full(), rel_tol=1e-9)
    for i in range(400):
        p = rng.choice(movable)
        old = (p.x, p.y, p.r)
        moved = [p]
        if i % 5 == 3:
            p.r = (p.r + 90) % 360
        elif i % 5 == 4:
            q = rng.choice(movable)
            oq = (q.x, q.y)
            p.x, p.y, q.x, q.y = q.x, q.y, p.x, p.y
            moved = [p, q]
        else:
            p.x, p.y = p.x + rng.gauss(0, 2), p.y + rng.gauss(0, 2)
        new = pl._update([m.ref for m in moved])
        assert math.isclose(new, pl.cost_full(), rel_tol=1e-9, abs_tol=1e-6), i
        if i % 3 == 0:  # reject: the undo must restore every cache
            p.x, p.y, p.r = old
            if len(moved) > 1:
                moved[1].x, moved[1].y = oq
            pl._undo()
            assert math.isclose(pl._total(), cur, rel_tol=1e-9, abs_tol=1e-6)
        else:
            cur = new
    assert pl.t_ov > 0 and pl.t_cong > 0, "the test must exercise overlaps and congestion"


def test_congestion_terms(tmp_path):
    d, libs = channel(tmp_path)
    pl = placer.Placer(d, placer.build_parts(d, libs), W, H)
    assert pl.cost() > 0
    # keep-out boxes remove capacity; an empty board has no overflow
    assert pl.congestion() >= 0
    free = pl.cap.sum()
    pl2 = placer.Placer(d, pl.parts, W, H, keepouts=[(0, 0, 10, 10, 0.0)])
    pl2.cost()
    assert pl2.cap.sum() < free


def test_congestion_term_keeps_the_channel_open(tmp_path):
    d, libs = channel(tmp_path)

    def overflow(weight):
        total = 0.0
        for seed in (1, 2, 3):
            parts = placer.build_parts(d, libs)
            placer.Placer(d, parts, W, H, seed=seed, weights={"congestion": weight}).run(4000)
            ev = placer.Placer(d, parts, W, H)
            ev.cost()
            total += ev.t_cong
        return total

    off, on = overflow(0.0), overflow(placer.Placer(d, {}, W, H).wt["congestion"])
    assert off > 1.0, "wirelength alone must congest this fixture"
    assert on < 0.5 * off
