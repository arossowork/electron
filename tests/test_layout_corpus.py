"""Layout regression corpus (tests/fixtures/layout_corpus/, README there): cutouts of real designs with their
measured outcome in expected.yaml.

Fast tier (default): placement legality of the fixed placement, corridor channels, RUDY congestion, pin access and,
where expected.yaml says `tiers.check: fast`, `hwflow check`; a from-scratch re-placement of the free parts where
`tiers.replace: fast`. Values must match the recorded ones (tolerances below).

Slow tier: `hwflow check` on the other cases, and a real KRT route of every case (all cases in parallel,
LAYOUT_CORPUS_JOBS processes, default cpu/4); a case may not get worse than expected.yaml (more unrouted nets, DRC
errors or placement violations). An improvement is reported, never a failure: update expected.yaml with
`uv run python scripts/layout_corpus.py measure <case> --route --write`."""

from __future__ import annotations

import functools
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hwflow.layout.route import krt

_spec = importlib.util.spec_from_file_location("layout_corpus", Path(__file__).parents[1] / "scripts" / "layout_corpus.py")
lc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lc)

CASES = lc.case_dirs()
IDS = [c.name for c in CASES]
needs_router = pytest.mark.skipif(not (krt.krt_dir() / "py_router" / "route.py").exists(),
                                  reason="KiCadRoutingTools not installed")

CORRIDOR_TOL_MM = 0.02  # channel margin (channel - track width) of each constrained net
RUDY_REL = 0.05  # relative tolerance of the RUDY congestion sum
OVERFLOW_TOL = 0.05  # mm of wire per cell
GATE_PEAK_TOL = 0.25  # the gate's peak window ratio


def _exp(case: Path) -> dict:
    e = lc.expected(case)
    if not e.get("fast"):
        pytest.skip(f"{case.name}: expected.yaml has no fast metrics (scripts/layout_corpus.py measure --fast --write)")
    return e


@functools.lru_cache(maxsize=None)
def _fast(name: str, check: bool) -> dict:
    return lc.fast(lc.CORPUS / name, check=check)


def test_corpus_has_cases():
    assert len(CASES) >= 8
    for c in CASES:
        assert (c / "expected.yaml").exists(), c.name
        assert not (c / "build").exists(), f"{c.name}: no build outputs in the corpus"


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_fixed_placement(case):
    """Legality and the pre-routing estimates of the recorded placement, as measured on main."""
    e = _exp(case)["fast"]
    g = _fast(case.name, False)
    assert g["parts"] == e["parts"] and g["nets"] == e["nets"]
    assert g["placement_violations"] == e["placement_violations"]
    assert g["fixed_violations"] == e["fixed_violations"]
    assert set(g["corridors"]) == set(e["corridors"])
    for n, m in e["corridors"].items():
        assert abs(g["corridors"][n] - m) <= CORRIDOR_TOL_MM, (n, g["corridors"][n], m)
    assert g["rudy"]["congestion"] == pytest.approx(e["rudy"]["congestion"], rel=RUDY_REL, abs=0.5)
    assert abs(g["rudy"]["max_overflow"] - e["rudy"]["max_overflow"]) <= OVERFLOW_TOL
    pa, ea = g["pin_access"], e["pin_access"]
    assert pa["pads"] == ea["pads"]
    assert pa["necked"] == ea["necked"]
    assert pa["nets_no_exit_at_width"] == ea["nets_no_exit_at_width"]
    assert pa["no_access"] == ea["no_access"]
    if "gate" in e:
        assert g["gate"]["fail"] == e["gate"]["fail"], g["gate"]
        assert abs(g["gate"]["peak"] - e["gate"]["peak"]) <= GATE_PEAK_TOL, (g["gate"], e["gate"])


# clean cases the window-cut congestion gate fails (docs/layout-flow.md, "Known false positive"; the floorplan clears
# them by routing the spot, test_gate_route_check_clears_only_the_false_positive): a predictor change that clears one
# makes test_gate_spares_the_cases_that_route fail until it is taken off this list
GATE_FALSE_POSITIVES = {"pr_mcu"}  # pocket-recorder: a pad row of the ISP1807 LGA along a window border, 7.7 x
# cases the gate fails that do not route: the route check must keep them (oshw_b_parasite 1 unrouted in 31 s,
# oshw_rocketbeacon 4 in 23 s; the larger ones run out of the 120 s)
GATE_TRUE_FAILS = ("oshw_b_parasite", "oshw_rocketbeacon")


@pytest.mark.slow
@pytest.mark.parametrize("name", sorted(GATE_FALSE_POSITIVES) + list(GATE_TRUE_FAILS))
def test_gate_route_check_clears_only_the_false_positive(name, tmp_path):
    """The floorplan routes each congestion-gate spot as a cutout before it sends a design back (floorplan.
    _confirm_by_route): pr_mcu routes clean in about 15 s, the true failures leave nets open."""
    from hwflow.core.design import Design
    from hwflow.core.libs import Libs
    from hwflow.layout.place import floorplan, placer, routability
    w = lc.stage(lc.CORPUS / name, tmp_path)
    d = Design.load(w / "design.yaml")
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    g = routability.predict(d, libs, parts=parts, corridors=False, access=False)["gate"]
    assert g["fail"]
    r = floorplan._confirm_by_route(d, parts, g["spots"])
    assert r["clean"] == (name in GATE_FALSE_POSITIVES), r


def test_gate_spares_the_cases_that_route():
    """The floorplan congestion gate sends a design back to architecture, so it must not fail a placement that
    routes clean (recorded values: expected.yaml fast.gate, which test_fixed_placement re-measures). pr_mcu is the
    known exception, kept as the benchmark of a fix."""
    clean, failed = [], []
    for c in CASES:
        e = lc.expected(c)
        r, g = e.get("route") or {}, (e.get("fast") or {}).get("gate")
        if g is None or not r.get("ok"):
            continue
        clean.append(c.name)
        if g["fail"]:
            failed.append(c.name)
    assert "pr_pmic" in clean and set(failed) == GATE_FALSE_POSITIVES, failed



def _check(case):
    e = _exp(case)
    f = lc.fast(case, check=True)
    got, want = f["check"], e["fast"]["check"]
    # scaling guard (keyboard: region nets put 387 nets under the corridor proof and check went from 13 to 54 min):
    # a generous budget, not a tight wall time
    budget = max(120.0, 5 * e["fast"]["seconds"]["check"])
    assert f["seconds"]["check"] <= budget, f"check {f['seconds']['check']} s > {budget:.0f} s"
    assert got["ok"] == want["ok"]
    assert got["errors"] == want["errors"], got
    assert got["codes"] == want["codes"]


FAST_CHECK = [c for c in CASES if (lc.expected(c).get("tiers") or {}).get("check") == "fast"]
SLOW_CHECK = [c for c in CASES if c not in FAST_CHECK]


@pytest.mark.parametrize("case", FAST_CHECK, ids=[c.name for c in FAST_CHECK])
def test_check(case):
    _check(case)


@pytest.mark.slow
@pytest.mark.parametrize("case", SLOW_CHECK, ids=[c.name for c in SLOW_CHECK])
def test_check_dense(case):
    _check(case)


REPLACE = [c for c in CASES if (lc.expected(c).get("tiers") or {}).get("replace") == "fast"]


@pytest.mark.parametrize("case", REPLACE, ids=[c.name for c in REPLACE])
def test_replacement_from_scratch(case):
    """The placer re-places the parts the source placer was free to move (terminals and fixed parts stay) with no
    more violations than recorded."""
    e = _exp(case)["replace"]
    g = lc.replace(case)
    assert g["free"] == e["free"]
    assert len(g["violations"]) <= len(e["violations"]), g["violations"]


def test_cutout_of_a_cutout(tmp_path):
    """hwflow cutout on a corpus case: the kept parts keep their placement (moved by the offset), each net that
    leaves the window ends on one fixed terminal on the border, and the placer finds the fixed placement legal."""
    import yaml

    from hwflow.core.design import Design
    from hwflow.core.libs import Libs
    from hwflow.layout.place import cutout, placer

    src = lc.stage(lc.CORPUS / "ht4_s11_mains", tmp_path / "src")
    rec = cutout.cutout(src, tmp_path / "cut", parts="K1,F1", name="t")
    d0 = yaml.safe_load((src / "design.yaml").read_text())["components"]
    d1 = Design.load(tmp_path / "cut" / "design.yaml")
    dx, dy = rec["offset"]
    for r in ("K1", "F1"):
        a, b = d0[r]["placement"], d1.components[r].get("placement")
        assert b["fixed"] and abs(a["x"] + dx - b["x"]) < 1e-6 and abs(a["y"] + dy - b["y"]) < 1e-6
    nets = d1.nets()
    for t in rec["terminals"]:
        assert any(r == t["ref"] for r, _ in nets[t["net"]])
        assert len([1 for r, _ in nets[t["net"]] if r.startswith(cutout.TP_PREFIX)]) == 1
    assert {t["net"] for t in rec["terminals"]} >= {"K1_COIL_A", "K1_COIL_B"}
    W, H = rec["board_mm"]
    for t in rec["terminals"]:
        assert 0 < t["at"][0] < W and 0 < t["at"][1] < H
    libs = Libs(design=d1)
    parts = placer.build_parts(d1, libs)
    pl = placer.Placer(d1, parts, W, H)
    assert not [v for v in pl.violations() if "TPB" in v]
    assert not rec["warnings"]


# ---------------------------------------------------------------- slow: real routing


@pytest.fixture(scope="session")
def routed():
    jobs = int(os.environ.get("LAYOUT_CORPUS_JOBS") or max(1, (os.cpu_count() or 4) // 4))
    timeout = float(os.environ.get("LAYOUT_CORPUS_TIMEOUT") or 5400)
    # each case routes in its own hwflow subprocess, so threads are enough
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        return dict(ex.map(lc._route_one, [(c, timeout, None, False, {}) for c in CASES]))


@pytest.mark.slow
@needs_router
@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_route_no_regression(case, routed):
    e = lc.expected(case).get("route")
    if not e:
        pytest.skip(f"{case.name}: expected.yaml has no route outcome")
    g = routed[case.name]
    bad = lc.regressions(e, g)
    better = lc.improvements(e, g)
    if better:
        print(f"{case.name}: better than expected.yaml ({'; '.join(better)}); "
              f"record it: scripts/layout_corpus.py measure {case.name} --route --write")
    assert not bad, f"{case.name}: {'; '.join(bad)} (open {g.get('open_nets')})"


# ---------------------------------------------------------------- lessons from earlier boards (fast, on corpus data)


def _moved_design(tmp_path, case, ref, x, y, rot):
    import yaml

    from hwflow.core.design import Design

    w = lc.stage(lc.CORPUS / case, tmp_path)
    raw = yaml.safe_load((w / "design.yaml").read_text())
    raw["components"][ref]["placement"] = {"x": x, "y": y, "rot": rot, "fixed": True}
    (w / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    return Design.load(w / "design.yaml")


def test_footprint_keepout_zone_follows_part(tmp_path):
    """ht3 (a86a3a6): a footprint's keep-out zone is written at the part's position and rotation, not at the origin.
    The board writer and the placer's rule areas (two code paths) give the same polygon."""
    from hwflow.core import sexpr
    from hwflow.core.libs import Libs
    from hwflow.layout.board import keepout, pcb
    from hwflow.layout.place import placer

    d = _moved_design(tmp_path, "ht4_s11_mains", "KO_NOTCH_L", 9.0, 30.0, 90)
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    want = [(a["name"], a["poly"]) for a in keepout.placement_areas(d, libs) if a["owner"] == "KO_NOTCH_L"]
    assert want
    pcb.write_board(tmp_path / "b.kicad_pcb", pcb.build_board(d, libs, parts))
    tree = sexpr.loads((tmp_path / "b.kicad_pcb").read_text())
    got = [a["poly"] for a in keepout.rule_areas(tree) if a["name"] == want[0][0]]  # matched by zone name
    assert got, "no zone of KO_NOTCH_L in the written board"
    assert want[0][1].symmetric_difference(got[0]).area < 1e-3
    from hwflow.core.geometry import rot
    lib = keepout.rule_areas(libs.footprint_tree(d.components["KO_NOTCH_L"].get("footprint")))[0]["poly"].centroid
    ox, oy = rot(lib.x, lib.y, 90)  # footprint coordinates -> board: rotate about the part origin, then move
    c = got[0].centroid
    assert abs(c.x - (9.0 + ox)) < 1e-3 and abs(c.y - (30.0 + oy)) < 1e-3


def test_keepout_circle_is_not_a_box(tmp_path):
    """ht3: a part 0.02 mm clear of a round cut (its bounding box overlaps the circle's) is no violation."""
    import math

    import yaml

    from hwflow.core.design import Design
    from hwflow.core.libs import Libs
    from hwflow.layout.place import placer

    w = lc.stage(lc.CORPUS / "ht4_s11_mains", tmp_path)
    raw = yaml.safe_load((w / "design.yaml").read_text())
    cx, cy, rad = next(r for r in raw["floorplan"]["regions"] if r["name"] == "notch_l")["circle"]
    d = Design.load(w / "design.yaml")
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    p = parts["R8"]
    x0, y0, x1, y1 = p.box()
    k = (rad + float(d.board["margin_mm"]) + 0.02) / math.sqrt(2)  # the placer keeps the board margin from a cut
    # lower-left courtyard corner on the circle's diagonal, just outside it: the boxes overlap, the shapes do not
    p.x += (cx + k) - x0
    p.y += (cy - k) - y1
    pl = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"])
    v = [x for x in pl.violations() + pl.fixed_violations() if x.startswith("R8 ") and "notch_l" in x]
    assert not v, v
