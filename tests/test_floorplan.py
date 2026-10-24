"""Feature B: the floorplan field, its arithmetic, the quick check and the placer region term."""
import math
import random

import pytest
import yaml

from hwflow.layout.place import floorplan, placer
from hwflow.core.design import Design
from hwflow.verify.checks import check
from hwflow.core.libs import Libs


def make(tmp_path, fp, board=None, comps=None, nets=None):
    raw = {"project": "fp", "board": {"width_mm": 88, "height_mm": 46, "layers": 4, "gnd_pour": False, **(board or {})},
           "floorplan": fp}
    if comps:
        raw["components"] = comps
    if nets:
        raw["nets"] = nets
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    return Design.load(tmp_path)


THERMO = {  # home-thermostat-3, architecture 6: 230 V left, cells right, antenna at the bottom edge
    "construction": "double",
    "depth": {"limit_mm": 30},
    "regions": [
        {"name": "notch_l", "kind": "keepout", "circle": [2.25, 23, 4], "conductive": True},
        {"name": "mains", "kind": "mains", "rect": [0, 0, 20, 46], "isolation_mm": 6},
        {"name": "hv", "kind": "barrier", "rect": [0, 0, 26, 46], "items": ["relays"]},
        {"name": "selv", "kind": "selv", "rect": [26, 0, 88, 46]},
        {"name": "battery", "kind": "battery", "rect": [27, 0, 82, 28], "items": ["cells"]},
    ],
    "items": [
        {"name": "relays", "size_mm": [24.5, 10.5], "count": 2, "side": "back", "height_mm": 15.8, "protrusion_mm": 1.0},
        {"name": "cells", "size_mm": [50, 13], "count": 2, "side": "front", "height_mm": 12.0},
        {"name": "mcu", "size_mm": [13.6, 17], "height_mm": 2.4},
    ],
    "nets": [{"between": ["mcu", "relays"], "count": 4}, {"between": ["mcu", "cells"], "count": 2}],
}


def test_parse_errors(tmp_path):
    bad = {"construction": "folded", "regions": [{"name": "a", "kind": "lava", "rect": [5, 5, 1, 1]}],
           "items": [{"name": "x", "region": "nowhere"}, {"name": "y", "size_mm": [3, 3], "side": "back"}],
           "nets": [{"between": ["x", "zz"]}]}
    d = make(tmp_path, bad)
    errs = floorplan.validate(d)
    text = " | ".join(errs)
    for want in ("construction 'folded'", "kind 'lava'", "rect must be", "region 'nowhere'", "needs size_mm",
                 "'zz' is not an item"):
        assert want in text, want
    assert any(f["code"] == "floorplan" for f in check(d))
    single = make(tmp_path, {"construction": "single", "items": [{"name": "y", "size_mm": [3, 3], "side": "back"}]})
    assert any("single-face" in e for e in floorplan.validate(single))
    stacked = make(tmp_path, {"construction": "stacked", "boards": [{"name": "a", "size_mm": [10, 10]}]})
    assert any("at least two boards" in e for e in floorplan.validate(stacked))
    assert floorplan.validate(make(tmp_path, THERMO)) == []


def heights(*rows):
    return [{"name": n, "board": b, "side": s, "mm": mm, "source": None} for n, b, s, mm in rows]


def test_depth_double_and_stacked():
    fp = {"construction": "double", "boards": [{"name": "board", "t": 1.6}], "depth": {"limit_mm": 30, "extra": []}}
    dep = floorplan.depth(fp, heights(("cells", "board", "front", 12.0), ("relay", "board", "back", 15.8),
                                      ("pins", "board", "front", 1.0)))
    assert dep["total_mm"] == pytest.approx(29.4) and dep["ok"]
    st = {"construction": "stacked", "depth": {"limit_mm": 30, "extra": []},
          "boards": [{"name": "top", "t": 1.6, "stack_mm": None}, {"name": "bottom", "t": 1.6, "stack_mm": None}]}
    rows = [("cells", "top", "front", 12.0), ("smd", "top", "back", 1.0), ("pins", "bottom", "back", 1.0)]
    tall = floorplan.depth(st, heights(*rows, ("relay", "bottom", "front", 15.8)))
    assert tall["total_mm"] == pytest.approx(12 + 1.6 + 1 + 15.8 + 1.6 + 1) and not tall["ok"]
    low = floorplan.depth(st, heights(*rows, ("relay", "bottom", "front", 10.0)))
    assert low["total_mm"] == pytest.approx(27.2) and low["ok"]
    st["boards"][0]["stack_mm"] = 8.0  # the board-to-board connector is shorter than the parts between the boards
    short = floorplan.depth(st, heights(*rows, ("relay", "bottom", "front", 10.0)))
    assert not short["ok"] and "connector stack 8.0 mm < 11.0 mm" in short["stack_errors"][0]


def test_region_isolation_and_conductive_keepout(tmp_path):
    fp = floorplan.parse(make(tmp_path, THERMO))
    dist = {(x["a"], x["b"]): x for x in floorplan.region_distances(fp)}
    assert dist[("mains", "selv")]["mm"] == pytest.approx(6.0) and dist[("mains", "selv")]["ok"]
    assert ("mains", "hv") not in dist  # a barrier region bridges the band on purpose
    mains = next(r for r in fp["regions"] if r["name"] == "mains")
    assert mains["poly"].distance(next(r for r in fp["regions"] if r["name"] == "notch_l")["poly"]) == pytest.approx(6.0, abs=0.05)
    near = dict(THERMO, regions=[dict(r, rect=[24, 0, 88, 46]) if r["name"] == "selv" else r for r in THERMO["regions"]])
    x = next(x for x in floorplan.region_distances(floorplan.parse(make(tmp_path, near))) if x["b"] == "selv")
    assert x["mm"] == pytest.approx(4.0) and not x["ok"]


def test_thermostat_verdicts(tmp_path):
    d = make(tmp_path, THERMO)
    res = floorplan.check(d, Libs(design=d), iters=3000, seeds=1)
    assert res["ok"], [c for c in res["checks"] if c["ok"] is False]
    assert res["depth"]["total_mm"] == pytest.approx(29.4)
    pos = res["placement"]["positions"]
    assert all(27 - 0.01 <= pos[c][0] - 25 and pos[c][0] + 25 <= 82.01 for c in ("cells#1", "cells#2"))
    # 74 mm: the cells (50 mm) no longer fit beside the 230 V zone and its band
    small = dict(THERMO, regions=[dict(r, rect=[r["rect"][0], 0, min(r["rect"][2], 74), 46 if r["name"] != "battery" else 26])
                                  if "rect" in r else r for r in THERMO["regions"]])
    d = make(tmp_path, small, board={"width_mm": 74})
    res = floorplan.check(d, Libs(design=d), iters=1500, seeds=1)
    assert not res["ok"] and any(c["name"] == "fit" and "cells" in c["msg"] for c in res["checks"])
    # area: the same items crowded into a 30 x 46 board side
    crowd = dict(THERMO, regions=[], depth={"limit_mm": 30})
    d = make(tmp_path, crowd, board={"width_mm": 30})
    res = floorplan.check(d, Libs(design=d), place=False)
    assert any(c["name"] == "area" and c["ok"] is False for c in res["checks"])
    md = floorplan.report(d, res)
    assert "Verdict: FAIL" in md and "## Depth" in md


RS = "Resistor_SMD:R_0603_1608Metric"


def region_design(tmp_path):
    comps = {f"R{i}": {"value": "1k", "symbol": "Device:R", "footprint": RS, "pins": {"1": f"N{i}", "2": f"N{i + 1}"}}
             for i in range(8)}
    comps["J1"] = {"value": "1k", "symbol": "Device:R", "footprint": RS, "pins": {"1": "N0", "2": "N8"},
                   "placement": {"x": 25, "y": 15, "rot": 0, "fixed": True}}
    comps["RA"] = {"value": "1k", "symbol": "Device:R", "footprint": RS, "pins": {"1": "AC1", "2": "N3"}}
    comps["RB"] = {"value": "1k", "symbol": "Device:R", "footprint": RS, "pins": {"1": "AC1", "2": "N5"}}
    fp = {"construction": "single",
          "regions": [{"name": "hv", "kind": "mains", "rect": [0, 0, 12, 30], "items": ["R0", "R1"], "nets": ["AC*"],
                       "allow": ["bridge"]},
                      {"name": "lv", "kind": "selv", "rect": [30, 0, 50, 30], "items": ["R6", "R7"]}],
          "items": [{"name": "bridge", "refs": ["RA", "RB"]}]}
    nets = {f"N{i}": {"class": "signal"} for i in range(9)}
    d = make(tmp_path, fp, board={"width_mm": 50, "height_mm": 30, "layers": 2}, comps=comps, nets=nets)
    return d, Libs(design=d)


def test_placer_region_term_incremental(tmp_path):
    d, libs = region_design(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 50, 30, seed=2)
    assert {g["name"] for g in pl.regions} == {"hv", "lv"}
    hv = next(g for g in pl.regions if g["name"] == "hv")
    assert hv["members"] == {"R0", "R1"} and set(hv["pads"]) == {"RA", "RB"} and hv["rect"]
    rng = random.Random(4)
    movable = [p for p in parts.values() if not p.fixed]
    for p in movable:
        p.x, p.y = rng.uniform(2, 48), rng.uniform(2, 28)
    cur = pl.cost()
    assert math.isclose(cur, pl.cost_full(), rel_tol=1e-9) and pl.t_ko > 0
    for i in range(300):
        p = rng.choice(movable)
        old = (p.x, p.y, p.r)
        p.x, p.y = p.x + rng.gauss(0, 4), p.y + rng.gauss(0, 4)
        if i % 4 == 0:
            p.r = (p.r + 90) % 360
        new = pl._update([p.ref])
        assert math.isclose(new, pl.cost_full(), rel_tol=1e-9, abs_tol=1e-6), i
        if i % 3 == 0:
            p.x, p.y, p.r = old
            pl._undo()
            assert math.isclose(pl._total(), cur, rel_tol=1e-9, abs_tol=1e-6)
        else:
            cur = new


def test_parts_end_inside_their_regions(tmp_path):
    d, libs = region_design(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 50, 30, seed=1)
    pl.run(6000)
    for g in pl.regions:
        for r in parts:
            assert sum(pl._region_cost(parts[r], g)) < 0.01, (r, g["name"])
    assert not any("floorplan region" in v for v in pl.violations())
    for r in ("R0", "R1"):
        assert parts[r].box()[2] <= 12.3
    assert all(parts[r].box()[0] >= 11.7 for r in parts if r not in ("R0", "R1", "RA", "RB"))
    parts["R6"].x = 5  # moved by hand into the mains region: reported
    assert any(v.startswith("R6 outside floorplan region 'lv'") for v in pl.violations())
    assert any(v.startswith("R6 inside floorplan region 'hv'") for v in pl.violations())


def test_no_floorplan_no_regions(tmp_path):
    raw = {"project": "x", "board": {"width_mm": 20, "height_mm": 20},
           "components": {"R1": {"value": "1k", "symbol": "Device:R", "footprint": RS, "pins": {"1": "A", "2": "B"}},
                          "R2": {"value": "1k", "symbol": "Device:R", "footprint": RS, "pins": {"1": "A", "2": "B"}}}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    pl = placer.Placer(d, placer.build_parts(d, Libs(design=d)), 20, 20)
    assert pl.regions == [] and pl.region_start == {}


def test_fixed_mains_pad_keeps_isolation_from_a_conductive_keepout():
    from types import SimpleNamespace

    from shapely.geometry import box

    from hwflow.layout.place import floorplan
    notch = box(0, 10, 3, 16)
    mains = box(0, 0, 20, 46).difference(notch.buffer(6))
    fp = {"boards": [{"name": "main", "x": 0, "y": 0}],
          "regions": [{"name": "notch", "kind": "keepout", "conductive": True, "board": "main", "poly": notch},
                      {"name": "mains", "kind": "mains", "nets": ["AC_*"], "iso": 6.0, "board": "main",
                       "poly": mains, "conductive": False}]}
    comp = SimpleNamespace(pins={"1": "AC_L", "2": "AC_N"})
    design = SimpleNamespace(components={"F1": comp})

    def part(x):
        return SimpleNamespace(fixed=True, r=0, pads={"1": (0, 0), "2": (0, 5)}, pad_size={"1": (1, 1), "2": (1, 1)},
                               pad=lambda n, x=x: (x, {"1": 30.0, "2": 35.0}[n]) if x is None else (x, 13.0 + 5 * (n == "2")))
    near = part(6.0)                                    # pad 1 at (6, 13): 2.5 mm from the notch edge
    msgs = floorplan.fixed_pad_isolation(design, {"F1": near}, fp)
    assert len(msgs) == 2 and "F1.1 (AC_L, fixed) is 2.50 mm from conductive keep-out notch" in msgs[0]
    far = part(12.0)                                    # 8.5 mm away
    assert floorplan.fixed_pad_isolation(design, {"F1": far}, fp) == []
    design.components["F1"] = SimpleNamespace(pins={"1": "GND", "2": "SIG"})  # not a mains net: not checked
    assert floorplan.fixed_pad_isolation(design, {"F1": near}, fp) == []
