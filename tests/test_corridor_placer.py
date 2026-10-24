"""Corridor term of the placer: a constrained net (here `layers: [F.Cu]`, 1 mm wide) keeps a channel as wide as its
track between its pads; a channel the fixed parts close is a placement violation."""
import math
import random
import time

import yaml

from hwflow import placer
from hwflow.core.design import Design
from hwflow.core.libs import Libs

W, H = 30, 20
PIN1 = "Connector_PinHeader_2.54mm:PinHeader_1x01_P2.54mm_Vertical"


def _load(tmp_path, comps, sig_w=1.0):
    """SIG joins J1 (3, 10) and J2 (27, 10) on F.Cu only. t:Back1 is a one-pad SMD part on B.Cu (no F.Cu copper)."""
    pretty = tmp_path / "lib" / "footprints" / "t.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    (pretty / "Back1.kicad_mod").write_text(
        '(footprint "Back1" (version 20221018) (generator test) (layer "F.Cu")\n'
        '  (fp_rect (start -0.8 -0.8) (end 0.8 0.8) (layer "B.CrtYd") (width 0.05))\n'
        '  (pad "1" smd rect (at 0 0) (size 1 1) (layers "B.Cu" "B.Mask")))\n')
    comps = dict(comps)
    for ref, x in (("J1", 3), ("J2", W - 3)):
        comps[ref] = {"value": "pin", "symbol": "Connector_Generic:Conn_01x01", "footprint": PIN1, "pins": {"1": "SIG"},
                      "placement": {"x": x, "y": H / 2, "rot": 0, "fixed": True}}
    raw = {"project": "cor", "board": {"width_mm": W, "height_mm": H, "layers": 2, "track_mm": 0.25,
                                       "clearance_mm": 0.2, "gnd_pour": False},
           "nets": {"SIG": {"width_mm": sig_w, "layers": ["F.Cu"]}}, "components": comps}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    return d, Libs(design=d)


def _bait(n=1):
    """R1..Rn (0603, F.Cu) tied to a B.Cu pad on the SIG line: wire length alone puts them on the line."""
    comps = {}
    for i in range(1, n + 1):
        comps[f"B{i}"] = {"value": "pad", "symbol": "Connector_Generic:Conn_01x01", "footprint": "t:Back1",
                          "pins": {"1": f"X{i}"}, "placement": {"x": W / 2 + (i - 1) * 2, "y": H / 2, "rot": 0, "fixed": True}}
        comps[f"R{i}"] = {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
                          "pins": {"1": f"X{i}", "2": f"X{i}"}}
    return comps


def _width(d, libs, parts):
    c = placer.Placer(d, parts, W, H).corridors()
    assert [x["net"] for x in c] == ["SIG"]
    return c[0]["width"]


def test_corridor_opens_channel(tmp_path):
    d, libs = _load(tmp_path, _bait())
    widths = {}
    for wt in (0.0, None):
        parts = placer.build_parts(d, libs)
        pl = placer.Placer(d, parts, W, H, seed=1, weights=None if wt is None else {"corridor": wt})
        pl.run(4000)
        widths[wt] = _width(d, libs, parts)
    assert widths[0.0] <= 0.5, widths  # wire length alone: the resistor sits on the straight channel
    assert widths[None] >= 1.0, widths  # the corridor term moves it out of the way of the 1 mm track


def test_corridor_incremental_equals_full(tmp_path):
    d, libs = _load(tmp_path, _bait(3))
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, W, H)
    rng = random.Random(3)
    movable = [p for p in parts.values() if not p.fixed]
    for p in movable:
        p.x, p.y = rng.uniform(12, 18), rng.uniform(8, 12)
    cur = pl.cost()
    assert pl.t_cor > 0
    assert math.isclose(cur, pl.cost_full(), rel_tol=1e-9)
    for i in range(300):
        p = rng.choice(movable)
        old = (p.x, p.y, p.r)
        if i % 4 == 3:
            p.r = (p.r + 90) % 360
        else:
            p.x, p.y = p.x + rng.gauss(0, 1.5), p.y + rng.gauss(0, 1.5)
        new = pl._update([p.ref])
        assert math.isclose(new, pl.cost_full(), rel_tol=1e-9, abs_tol=1e-6), i
        if i % 3 == 0:
            p.x, p.y, p.r = old
            pl._undo()
            assert math.isclose(pl._total(), cur, rel_tol=1e-9, abs_tol=1e-6)
        else:
            cur = new


def test_corridor_speed(tmp_path):
    """The term recomputes only the links a move can change: the annealing keeps most of its speed even here, where
    every movable part sits near the constrained net (worst case; the thermostat loses about 10 %)."""
    d, libs = _load(tmp_path, _bait(3))
    rate = {}
    for cor in (False, True) * 3:  # interleaved, best of 3 each: a busy machine slows both alike
        parts = placer.build_parts(d, libs)
        pl = placer.Placer(d, parts, W, H, seed=2)
        if not cor:
            pl.cor = []
        t = time.perf_counter()
        pl._anneal(pl._movable(), 3000, 0.6, 0.01, ramp=False)
        rate[cor] = max(rate.get(cor, 0.0), 3000 / (time.perf_counter() - t))
    assert rate[True] >= 0.5 * rate[False], rate


def _header(cols):
    """A fixed pin header across the board (8 rows at 2.54 mm pitch, 0.84 mm between pads; `cols` columns): a 1 mm
    track with 0.2 mm clearance passes between two rows only as a neck (0.15 mm), and only if the header is thin."""
    return {"W": {"value": "hdr", "symbol": "Connector_Generic:Conn_01x08" if cols == 1 else
                  "Connector_Generic:Conn_02x08_Odd_Even",
                  "footprint": f"Connector_PinHeader_2.54mm:PinHeader_{cols}x08_P2.54mm_Vertical",
                  "pins": {str(i): f"W{i}" for i in range(1, 8 * cols + 1)},
                  "placement": {"x": W / 2 - (cols - 1) * 1.27, "y": (H - 7 * 2.54) / 2, "rot": 0, "fixed": True}}}


def _verdicts(d, libs):
    """(placer, its corridor violations, the corridor proof's widest SIG track with necks, as `hwflow check`)."""
    from hwflow.layout.route import corridor
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, W, H)
    v = [x for x in pl.violations() if x.startswith("corridor ")]
    row = [r for r in corridor.margin(d, libs) if r["net"] == "SIG"]
    return pl, v, row[0]["necks"]


def test_corridor_fixed_neck_passes(tmp_path):
    """One column: the full-width channel is closed but the neck between two pins (about 1.7 mm) is within
    routing.NECK_MAX_MM. The proof and the placer agree: no block; the channel is reported narrow, not blocked."""
    d, libs = _load(tmp_path, _header(1))
    pl, v, necks = _verdicts(d, libs)
    assert not v, v
    assert necks >= 1.0, necks
    c = pl.corridors()[0]
    assert not c["ok"] and "narrow (a neck may be needed)" in c["msg"], c


def test_corridor_fixed_long_neck_blocks(tmp_path):
    """Two columns and a 1.8 mm track: the narrow stretch runs across both columns (about 4.5 mm; the pocket between
    four round pins fits only 1.49 mm), longer than routing.NECK_MAX_MM. The proof and the placer agree: blocked, a
    placement violation; a 0.2 mm track passes without a neck."""
    d, libs = _load(tmp_path, _header(2), sig_w=1.8)
    _, v, necks = _verdicts(d, libs)
    assert necks < 1.8, necks
    assert len(v) == 1 and "SIG blocked between fixed parts" in v[0] and "J1.1" in v[0] and "J2.1" in v[0], v
    d, libs = _load(tmp_path, _header(2), sig_w=0.2)
    _, v, necks = _verdicts(d, libs)
    assert not v and necks >= 0.2, (v, necks)
