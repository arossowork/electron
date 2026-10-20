"""L9: congestion (RUDY) term and the incremental placer cost."""
import itertools
import math
import random
import shutil
from pathlib import Path

import pytest

import yaml

from hwflow import placer
from hwflow.design import Design
from hwflow.libs import Libs

W, H, N = 36, 30, 10


def channel(tmp_path, congestion_rules=True, board_extra=None):
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
    board = {"width_mm": W, "height_mm": H, "layers": 2, "track_mm": 0.4, "clearance_mm": 0.3, "gnd_pour": False, **(board_extra or {})}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(
        {"project": "chan", "board": board, "nets": nets, "components": comps}))
    d = Design.load(tmp_path)
    return d, Libs(design=d)


def check_incremental(pl, parts):
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


def test_incremental_cost_equals_full(tmp_path):
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    check_incremental(placer.Placer(d, parts, W, H, seed=3), parts)


AREAS = [{"name": "ko", "owner": "J1", "forbids": {"footprints", "tracks"}, "layers": ["F.Cu", "B.Cu"], "box": (13, 10, 19, 20)},
         {"name": "kp", "owner": "J1", "forbids": {"pads"}, "layers": ["F.Cu"], "box": (20, 12, 25, 18)}]


def test_incremental_cost_with_keepouts_and_isolation(tmp_path):
    iso = [{"name": "a-b", "nets": ["A*"], "clearance_mm": 1.0, "creepage_mm": 3.0}]
    d, libs = channel(tmp_path, board_extra={"isolation": iso})
    parts = placer.build_parts(d, libs)
    parts.areas = AREAS
    pl = placer.Placer(d, parts, W, H, seed=3)
    assert pl.iso_need == [3.5]
    check_incremental(pl, parts)
    assert pl.t_ko > 0 and pl.t_iso > 0, "the test must exercise the keep-out and isolation terms"
    assert pl.keepouts == [(13, 10, 19, 20, 0.0)]  # the router-capacity hook sees the track keep-out


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


def test_isolation_groups_keep_creepage_plus_margin(tmp_path):
    """HV and LV resistors tied by a bridging resistor: wirelength pulls them together, creepage keeps them apart."""
    rs = "Resistor_SMD:R_0603_1608Metric"
    comps = {"RH": {"value": "1k", "symbol": "Device:R", "footprint": rs, "pins": {"1": "HV1", "2": "HV2"}},
             "RB": {"value": "1k", "symbol": "Device:R", "footprint": rs, "pins": {"1": "HV2", "2": "LV1"}},
             "RL": {"value": "1k", "symbol": "Device:R", "footprint": rs, "pins": {"1": "LV1", "2": "LV2"}},
             "RC": {"value": "1k", "symbol": "Device:R", "footprint": rs, "pins": {"1": "HV1", "2": "LV2"}}}
    board = {"width_mm": 30, "height_mm": 20, "layers": 2, "track_mm": 0.25, "clearance_mm": 0.2, "gnd_pour": False,
             "isolation": [{"name": "hv", "nets": ["HV*"], "clearance_mm": 2.0, "creepage_mm": 3.0}]}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(
        {"project": "iso", "board": board, "nets": {n: {"class": "signal"} for n in ("HV1", "HV2", "LV1", "LV2")},
         "components": comps}))
    d = Design.load(tmp_path)
    libs = Libs(design=d)
    for seed in (1, 2, 3):
        parts = placer.build_parts(d, libs)
        pl = placer.Placer(d, parts, 30, 20, seed=seed)
        pl.run(6000)
        assert pl.violations() == [] and pl.t_iso < 0.05
        keys = list(pl.pad_grp)
        gaps = []
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                if pl.pad_grp[a] != pl.pad_grp[b] and a[0] != b[0]:
                    ba, bb = pl._pad_box(parts[a[0]], a[1]), pl._pad_box(parts[b[0]], b[1])
                    gaps.append(math.hypot(max(0, ba[0] - bb[2], bb[0] - ba[2]), max(0, ba[1] - bb[3], bb[1] - ba[3])))
        assert gaps and min(gaps) >= 3.5 - 0.02, (seed, min(gaps))


def _iso_design(tmp_path, nets, board_extra):
    rs = "Resistor_SMD:R_0603_1608Metric"
    comps = {"RH": {"value": "1k", "symbol": "Device:R", "footprint": rs, "pins": {"1": "HV1", "2": "HV2"}},
             "RB": {"value": "1k", "symbol": "Device:R", "footprint": rs, "pins": {"1": "HV2", "2": "LV1"}},
             "RL": {"value": "1k", "symbol": "Device:R", "footprint": rs, "pins": {"1": "LV1", "2": "LV2"}},
             "RC": {"value": "1k", "symbol": "Device:R", "footprint": rs, "pins": {"1": "HV1", "2": "LV2"}}}
    board = {"width_mm": 30, "height_mm": 20, "layers": 2, "track_mm": 0.25, "clearance_mm": 0.2, "gnd_pour": False,
             **board_extra}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(
        {"project": "iso", "board": board, "nets": nets, "components": comps}))
    d = Design.load(tmp_path)
    return d, Libs(design=d)


def test_net_class_clearance_keeps_pads_of_other_nets_apart(tmp_path):
    """A net class with a clearance above the board's is a placement distance between its pads and the pads of every
    other net, with no board.isolation entry."""
    nets = {n: {"class": "mains" if n.startswith("HV") else "signal", **({"clearance_mm": 2.0} if n.startswith("HV") else {})}
            for n in ("HV1", "HV2", "LV1", "LV2")}
    d, libs = _iso_design(tmp_path, nets, {})
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 30, 20, seed=2)
    assert pl.iso_need == []
    check_incremental(pl, parts)
    assert pl.t_iso > 0
    for seed in (1, 2):
        parts = placer.build_parts(d, libs)
        pl = placer.Placer(d, parts, 30, 20, seed=seed)
        pl.run(6000)
        assert pl.violations() == []
        for (ra, pa), (rb, pb) in itertools.combinations(pl.pad_grp, 2):
            na, nb = pl.pad_net[(ra, pa)], pl.pad_net[(rb, pb)]
            if ra != rb and na != nb and "HV" in (na[:2], nb[:2]):
                ba, bb = pl._pad_box(parts[ra], pa), pl._pad_box(parts[rb], pb)
                gap = math.hypot(max(0, ba[0] - bb[2], bb[0] - ba[2]), max(0, ba[1] - bb[3], bb[1] - ba[3]))
                assert gap >= 2.1 - 0.02, (seed, ra, rb, gap)


def test_raw_drc_rule_is_a_placement_distance(tmp_path):
    nets = {n: {"class": "signal"} for n in ("HV1", "HV2", "LV1", "LV2")}
    rule = "(rule \"x\" (constraint clearance (min 4.0mm)) (condition \"A.NetName == 'HV*' && B.NetName != 'HV*'\"))"
    d, libs = _iso_design(tmp_path, nets, {"drc_rules": [rule]})
    assert placer.Placer(d, placer.build_parts(d, libs), 30, 20).iso_need == [4.5]


def test_through_hole_pad_stays_out_of_the_courtyard_on_the_other_side():
    """KiCad pth_inside_courtyard: a plated pad inside a courtyard of another part, on any board side."""
    th = placer.Part("J", 4, 4, 0, 0, {"1": (0, 0)}, x=10, y=10, pad_size={"1": (1.6, 1.6)},
                     sides={"front": (4, 4, 0, 0)}, tht=frozenset({"1"}))
    under = placer.Part("R", 2, 1, 0, 0, {"1": (0, 0)}, x=10, y=10, pad_size={"1": (1, 1)},
                        sides={"back": (2, 1, 0, 0)})
    assert placer._side_overlap(th, under) == pytest.approx(1.6)  # the J pad area inside the R courtyard
    under.x = 20
    assert placer._side_overlap(th, under) == 0


BASELINE = Path(__file__).resolve().parents[1] / "evals" / "baselines" / "home-thermostat-2026-10-19" / "project"


@pytest.mark.skipif(not BASELINE.exists(), reason="baseline project not in this checkout")
def test_thermostat_never_places_a_footprint_in_ko_stack(tmp_path):
    """Baseline copy with every part movable except connectors, keep-out footprints, BT1 and the panel slot."""
    proj = tmp_path / "p"
    shutil.copytree(BASELINE, proj, ignore=shutil.ignore_patterns("build"))
    raw = yaml.safe_load((proj / "design.yaml").read_text())
    for ref, c in raw["components"].items():
        if not (ref.startswith(("J", "KO_", "SLOT")) or ref == "BT1"):
            (c.get("placement") or {}).pop("fixed", None)
            c.pop("placement", None)
    (proj / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    d = Design.load(proj)
    libs = Libs(design=d)
    stack = [a for a in placer.build_parts(d, libs).areas if a["owner"] == "KO_STACK" and "footprints" in a["forbids"]]
    assert len(stack) == 2
    for seed in (1, 2, 3):
        parts = placer.build_parts(d, libs)
        pl = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"], seed=seed)
        pl.run(3000)
        for r, p in parts.items():
            if p.fixed:
                continue
            b = p.side_box("front")
            for a in stack:
                assert not (b and placer._overlap(b, a["box"]) > 0), (seed, r, b, a["box"])
        assert not [v for v in pl.violations() if "3D 4 mm" in v]
        assert any("BT1" in v for v in pl.fixed_violations())  # the fixed BT1 is reported, not moved


@pytest.mark.skipif(not BASELINE.exists(), reason="baseline project not in this checkout")
def test_panel_slot_is_a_keep_out_for_courtyards():
    d = Design.load(BASELINE)
    slot = [a for a in placer.build_parts(d, Libs(design=d)).areas if a["owner"] == "SLOT1"]
    assert len(slot) == 1 and "footprints" in slot[0]["forbids"]
    assert tuple(round(v, 2) for v in slot[0]["box"]) == (62.0, 16.5, 68.0, 38.0)


def test_unstick_separates_parts_the_annealing_left_overlapping(tmp_path):
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    for r in ("R1", "R2", "R3"):  # three resistors on top of each other, in the middle of the board
        parts[r].x, parts[r].y = 18.0, 15.0
    for r, p in parts.items():
        if r not in ("R1", "R2", "R3") and not p.fixed:
            p.x, p.y = 18.0 + 3 * int(r[1:]), 4.0
    pl = placer.Placer(d, parts, W, H, seed=1)
    before = pl.cost()
    assert pl.t_ov > 0
    after = pl.unstick()
    assert pl.t_ov == 0 and after < before
    assert math.isclose(after, pl.cost_full(), rel_tol=1e-9, abs_tol=1e-6)
    assert not any(v.startswith("courtyard overlap") for v in pl.violations())
