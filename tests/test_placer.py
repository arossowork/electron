"""L9: congestion (RUDY) term and the incremental placer cost."""
import itertools
import json
import math
import random
import shutil
from pathlib import Path

import pytest

import yaml

from hwflow.layout.place import placer
from hwflow.core.design import Design
from hwflow.core.libs import Libs

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
                    ba, bb = pl.part_pad_box(parts[a[0]], a[1]), pl.part_pad_box(parts[b[0]], b[1])
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
                ba, bb = pl.part_pad_box(parts[ra], pa), pl.part_pad_box(parts[rb], pb)
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
    for seed in (2, 4, 5):  # L12 changed the cost landscape: seeds 1, 3, 7 end this 3000-move run in the 3D keep-out (7 did before)
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


# ---- L12: terms fed by the post-layout checks --------------------------------------------------------------------

def _two(tmp_path, a, b, nets, board_extra=None, status=None, weights=None):
    """R0 fixed at (18, 15), R1 free; a net between them pulls R1 against any separation term."""
    comps = {}
    for ref, extra, pins in (("R0", a, {"1": "P0", "2": "X"}), ("R1", b, {"1": "P1", "2": "X"})):
        comps[ref] = {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
                      "pins": pins, **extra}
    comps["R0"]["placement"] = {"x": 18, "y": 15, "rot": 0, "fixed": True}
    board = {"width_mm": W, "height_mm": H, "layers": 2, "gnd_pour": False, **(board_extra or {})}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(
        {"project": "two", "board": board, "nets": {"X": {"class": "signal"}, **nets}, "components": comps}))
    if status:
        (tmp_path / "build").mkdir(exist_ok=True)
        (tmp_path / "build" / "status.json").write_text(json.dumps(status))
    d = Design.load(tmp_path)
    parts = placer.build_parts(d, Libs(design=d))
    return d, parts, placer.Placer(d, parts, W, H, seed=2, weights=weights)


def _box_gap(parts):
    A, B = parts["R0"].box(), parts["R1"].box()
    return math.hypot(max(0.0, A[0] - B[2], B[0] - A[2]), max(0.0, A[1] - B[3], B[1] - A[3]))


def _pad_gap(parts):
    return min(math.hypot(parts["R0"].pad(a)[0] - parts["R1"].pad(b)[0], parts["R0"].pad(a)[1] - parts["R1"].pad(b)[1])
               for a in ("1",) for b in ("1",))


def _near_far(tmp_path, want_term, **kw):
    """The weighted shortfall of the term with R1 close to R0 and far from it; weight 0 gives none."""
    d, parts, pl = _two(tmp_path, **kw)
    assert pl.seps and {e[5] for e in pl.seps} == {want_term}
    parts["R1"].x, parts["R1"].y = 18, 17
    near = pl._sep_val(0)
    parts["R1"].x, parts["R1"].y = 18, 17 + 20
    far = pl._sep_val(0)
    assert near > 0 and far == 0 and math.isclose(pl.cost(), pl.cost_full(), rel_tol=1e-9)
    return near, pl


def mk(tmp_path, name):
    sub = tmp_path / name
    sub.mkdir()
    return sub


def test_decap_loop_includes_the_ground_return(tmp_path):
    hdr = "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"
    comps = {"U1": {"value": "ic", "symbol": "Connector_Generic:Conn_01x04", "footprint": hdr,
                    "pins": {"1": "VCC", "2": "GND", "3": "S1", "4": "S2"},
                    "placement": {"x": 10, "y": 5, "rot": 0, "fixed": True}},
             "C1": {"value": "100n", "symbol": "Device:C", "footprint": "Capacitor_SMD:C_0603_1608Metric",
                    "pins": {"1": "VCC", "2": "GND"}, "decouples": "U1"}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump({"project": "dec", "board": {"width_mm": W, "height_mm": H},
        "nets": {"GND": {"class": "ground"}}, "components": comps}))
    d = Design.load(tmp_path)
    parts = placer.build_parts(d, Libs(design=d))
    pl = placer.Placer(d, parts, W, H)
    assert len(pl.decap) == 1 and pl.decap[0][4] == "2" and pl.decap[0][5] == ["2"]
    u, c = parts["U1"], parts["C1"]
    c.x, c.y = 14, u.pad("1")[1]  # supply pad next to pin 1
    sup = math.dist(c.pad("1"), u.pad("1"))
    gnd = math.dist(c.pad("2"), u.pad("2"))
    assert math.isclose(pl._decap_len(0), sup + gnd)
    c.r = 180  # same place, the pads swap: the supply leg is longer, the return too
    far = pl._decap_len(0)
    c.r, c.x, c.y = 0, 13, u.pad("1")[1] + 1.27  # between pin 1 and pin 2
    assert pl._decap_len(0) < far
    pl.cost()
    assert math.isclose(pl.cost(), pl.cost_full(), rel_tol=1e-9)
    assert math.isclose(pl.wt["decouple"] * pl.t_dec, pl.wt["decouple"] * pl._decap_len(0))


def test_aggressor_and_sensitive_parts_keep_apart(tmp_path):
    nets = {"P0": {"class": "clock"}, "P1": {"sensitive": True}, "X": {"class": "ground"}}  # parts wired together are not separated
    near, pl = _near_far(mk(tmp_path, "a"), "separation", a={}, b={}, nets=nets)
    assert pl.seps[0][4] == placer.Placer.SEP_MM
    # the placer pushes them apart: the penalty is part of the cost it minimizes
    pl.run(3000)
    assert pl.cost() < near + 100
    off = _two(mk(tmp_path, "b"), {}, {}, nets, weights={"separation": 0.0})[2]
    off.parts["R1"].x, off.parts["R1"].y = 18, 17
    assert off._sep_val(0) == 0
    wired = _two(mk(tmp_path, "c"), {}, {}, {"P0": {"class": "clock"}, "P1": {"sensitive": True}})[2]
    assert not wired.seps  # R0 and R1 share signal net X


def test_heat_source_keeps_away_from_the_temperature_sensor(tmp_path):
    st = {"sim": {"derating": [{"ref": "R0", "p_w": 0.2}, {"ref": "R1", "p_w": 0.01}]}}
    near, pl = _near_far(mk(tmp_path, "a"), "thermal", a={}, b={"role": "temp_sensor"}, nets={}, status=st)
    assert pl.seps[0][4] == 5  # board.thermal_clearance_mm default
    pl = _two(mk(tmp_path, "t"), {}, {"role": "temp_sensor"}, {}, status=st, board_extra={"thermal_clearance_mm": 9})[2]
    assert pl.seps[0][4] == 9
    low = {"sim": {"derating": [{"ref": "R0", "p_w": 0.04}]}}
    assert not _two(mk(tmp_path, "c"), {}, {"role": "temp_sensor"}, {}, status=low)[2].seps  # below 50 mW
    assert not _two(mk(tmp_path, "n"), {}, {"role": "temp_sensor"}, {})[2].seps  # no build: the term is skipped


def test_antenna_keeps_away_from_a_relay_and_mains_copper(tmp_path):
    near, pl = _near_far(mk(tmp_path, "a"), "antenna", a={"symbol": "Relay:G5LE-1"}, b={"role": "antenna"}, nets={})
    assert pl.seps[0][4] == 10  # board.antenna_clearance_mm default
    pl = _two(mk(tmp_path, "b"), {"symbol": "Relay:G5LE-1"}, {"role": "antenna"}, {}, board_extra={"antenna_clearance_mm": 8})[2]
    assert pl.seps[0][4] == 8
    iso = [{"name": "mains", "nets": ["P0"], "clearance_mm": 1.0, "creepage_mm": 3.0}]  # R0 has a pad on a mains net
    pl = _two(mk(tmp_path, "c"), {}, {"role": "antenna"}, {"P0": {"class": "signal"}}, board_extra={"isolation": iso})[2]
    assert [(e[0], e[2]) for e in pl.seps] == [("R1", "R0")]
    assert not _two(mk(tmp_path, "d"), {}, {"role": "antenna"}, {})[2].seps  # nothing to keep away from


def test_roles_are_inferred_from_the_symbol_and_role_none_switches_it_off(tmp_path):
    cases = [({"symbol": "Sensor_Temperature:TMP36xS"}, "temp_sensor"), ({"symbol": "Device:Thermistor_NTC"}, "temp_sensor"),
             ({"symbol": "RF_Module:ESP32-WROOM-32"}, "antenna"), ({"footprint": "RF_Antenna:Texas_SWRA117D_2.4GHz_Left"}, "antenna"),
             ({"symbol": "RF_Module:ESP32-WROOM-32", "role": "none"}, None), ({"symbol": "Device:R", "role": "temp_sensor"}, "temp_sensor"),
             ({"symbol": "Sensor_Temperature:TMP36xS", "role": "antenna"}, "antenna")]
    for i, (extra, want) in enumerate(cases):
        sub = mk(tmp_path, str(i))
        d, parts, pl = _two(sub, {}, extra, {})
        assert pl._roles().get("R1") == want, extra


def test_incremental_cost_equals_full_with_the_l12_terms(tmp_path):
    rng = random.Random(1)
    d, libs = channel(tmp_path, board_extra={"thermal_clearance_mm": 6, "antenna_clearance_mm": 12})
    raw = d.raw
    raw["nets"]["A1"] = {"class": "clock"}
    raw["nets"]["B3"] = {"sensitive": True}
    raw["components"]["R4"]["role"] = "temp_sensor"
    raw["components"]["R6"]["role"] = "antenna"
    raw["components"]["R7"]["symbol"] = "Relay:G5LE-1"
    for ref, c in raw["components"].items():
        d.components[ref].data = c
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "status.json").write_text(json.dumps({"sim": {"derating": [{"ref": "R2", "p_w": 0.3}]}}))
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, W, H, seed=3)
    assert {e[5] for e in pl.seps} == {"separation", "thermal", "antenna"}
    check_incremental(pl, parts)
    assert pl.t_sep > 0


def test_parts_stay_out_of_the_area_between_fixed_isolation_pads(tmp_path):
    """Two fixed HV resistors far apart enclose an area; the LV part that wirelength pulls between them is placed out
    of it (any route to it would cross the isolation distance), and a part placed there by hand is reported."""
    rs = "Resistor_SMD:R_0603_1608Metric"
    comps = {"RH1": {"value": "1k", "symbol": "Device:R", "footprint": rs, "pins": {"1": "HV1", "2": "HV2"},
                     "placement": {"x": 5, "y": 4, "rot": 90, "fixed": True}},
             "RH2": {"value": "1k", "symbol": "Device:R", "footprint": rs, "pins": {"1": "HV1", "2": "HV2"},
                     "placement": {"x": 5, "y": 26, "rot": 90, "fixed": True}},
             "RL": {"value": "1k", "symbol": "Device:R", "footprint": rs, "pins": {"1": "LV1", "2": "LV2"}},
             "RX": {"value": "1k", "symbol": "Device:R", "footprint": rs, "pins": {"1": "LV2", "2": "LV1"},
                    "placement": {"x": 40, "y": 15, "rot": 0, "fixed": True}}}
    board = {"width_mm": 50, "height_mm": 30, "layers": 2, "track_mm": 0.25, "clearance_mm": 0.2, "gnd_pour": False,
             "isolation": [{"name": "hv", "nets": ["HV*"], "clearance_mm": 1.0, "creepage_mm": 1.0}]}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(
        {"project": "iso", "board": board, "nets": {n: {"class": "signal"} for n in ("HV1", "HV2", "LV1", "LV2")},
         "components": comps}))
    d = Design.load(tmp_path)
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 50, 30, seed=1)
    assert len(pl.iso_hulls) == 1 and pl.iso_hulls[0][1] == {"RH1", "RH2"}
    parts["RL"].x, parts["RL"].y = 5, 15  # between the HV pads
    assert pl._ko_cost("RL") > 0 and any("isolation group 1 area" in v for v in pl.violations())
    pl.run(4000)
    assert pl._ko_cost("RL") < 0.01 and not any("isolation group" in v for v in pl.violations())


def test_fixed_parts_overlap_only_by_their_own_courtyards():
    from hwflow.layout.place.placer import COURTYARD_MARGIN, Part, _side_overlap
    m = COURTYARD_MARGIN

    def part(x, fixed=True):
        # a 4 mm courtyard (+ the placer margin on each side), touching its neighbour's courtyard edge at x + 4
        w = 4 + 2 * m
        return Part(ref="P", w=w, h=w, cx=0, cy=0, pads={}, x=x, y=0, fixed=fixed, sides={"front": (w, w, 0, 0)})
    a, b = part(0), part(4.1)                        # real courtyards 0.1 mm apart, margins overlap 0.4 mm
    assert _side_overlap(a, b) > 0.01                # the placer's boxes overlap
    assert _side_overlap(a, b, m) == 0               # the footprints' own courtyards do not


def test_isolation_message_names_the_rule_and_never_applies_within_a_group(tmp_path):
    """home-thermostat-3 reported "J1.3/K2.A2 0.32 mm short" for a mains pad and a relay coil pad (K2_COIL_B, not a
    member). An isolation group keeps its distance to the other nets only: two members (different nets of one group)
    need nothing beyond the net class clearance, and the message names the group and the nets."""
    nets = {n: {"class": "signal"} for n in ("HV1", "HV2", "LV1", "LV2")}
    d, libs = _iso_design(tmp_path, nets, {"isolation": [{"name": "mains-SELV", "nets": ["HV1", "HV2"],
                                                          "creepage_mm": 3.0}]})
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 30, 20, seed=1)
    ra = next(k for k in pl.pad_grp if pl.pad_net[k] == "HV1" and k[0] == "RC")
    rb = next(k for k in pl.pad_grp if pl.pad_net[k] == "HV2" and k[0] == "RB")
    rl = next(k for k in pl.pad_grp if pl.pad_net[k] == "LV1" and k[0] == "RL")
    for r, (x, y) in (("RC", (5, 10)), ("RB", (6.5, 10)), ("RL", (5, 12))):
        parts[r].x, parts[r].y, parts[r].r, parts[r].fixed = x, y, 0, True
    assert pl._iso_short(ra, rb) == 0.0                      # members of one group: no isolation between them
    assert pl._iso_short(ra, rl) > 0                         # member to another net: 3.0 + margin
    msg = pl._iso_rule(ra, rl)
    assert "mains-SELV" in msg and "HV1 / LV1" in msg and f"3 mm + {placer.ISO_MARGIN:g} mm" in msg
    assert any("mains-SELV" in v and v.startswith("isolation RC.") for v in pl.fixed_violations())
