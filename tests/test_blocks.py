"""Layout blocks (feature A): binding checks, rigid placement, block copper on the board, extraction."""
import math
import random
from collections import Counter

import pytest
import yaml

from hwflow.core import blocks
from hwflow.verify import blocks as authoring
from hwflow.layout.board import pcb
from hwflow.layout.route import krt
from hwflow.layout.place import placer
from hwflow.core.design import Design
from hwflow.verify.checks import check
from hwflow.core.libs import Libs

needs_router = pytest.mark.skipif(not (krt.krt_dir() / "py_router" / "route.py").exists(), reason="KiCadRoutingTools not installed")

BOOST = {"U": "U2", "L": "L1", "CIN": "C3", "COUT": "C4"}


def _boost_parts(**over):
    comps = {
        "U2": {"value": "TPS61299", "symbol": "Device:R", "footprint": "Package_TO_SOT_SMD:SOT-563",
               "pins": {"1": "VBAT", "2": "SW", "3": "VBAT", "4": "GND", "5": "VOUT", "6": "GND"}},
        "L1": {"value": "1u", "symbol": "Device:L", "footprint": "Inductor_SMD:L_Murata_DFE201610P",
               "pins": {"1": "VBAT", "2": "SW"}},
        "C3": {"value": "10u", "symbol": "Device:C", "footprint": "Capacitor_SMD:C_0805_2012Metric",
               "pins": {"1": "VBAT", "2": "GND"}},
        "C4": {"value": "10u", "symbol": "Device:C", "footprint": "Capacitor_SMD:C_0805_2012Metric",
               "pins": {"1": "VOUT", "2": "GND"}},
        "J1": {"value": "conn", "symbol": "Connector_Generic:Conn_01x03",
               "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
               "pins": {"1": "VBAT", "2": "GND", "3": "VOUT"}, "placement": {"x": 4, "y": 8, "rot": 0, "fixed": True}},
        "R1": {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
               "pins": {"1": "VOUT", "2": "X"}},
        "R2": {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
               "pins": {"1": "X", "2": "GND"}},
    }
    for ref, v in over.items():
        comps[ref] = dict(comps[ref], **v)
    return comps


def _design(tmp_path, comps=None, entry=None, board=None):
    raw = {"project": "blk", "board": {"width_mm": 30, "height_mm": 22, "layers": 2, "gnd_pour": True, **(board or {})},
           "nets": {"GND": {"class": "ground"}, "VBAT": {"external_supply": True}},
           "blocks": {"boost": entry or {"use": "tps61299_boost", "parts": dict(BOOST)}},
           "components": comps or _boost_parts()}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    d = Design.load(tmp_path)
    return d, Libs(design=d)


def _block_errors(d, libs):
    return [f["msg"] for f in check(d, libs) if f["code"] == "block" and f["sev"] == "error"]


# ---- binding -----------------------------------------------------------------------------------------------------

def test_a_consistent_binding_has_no_block_errors(tmp_path):
    d, libs = _design(tmp_path)
    assert _block_errors(d, libs) == []
    (bnd,) = blocks.bindings(d)
    assert blocks.netmap(d, bnd) == ({"VIN": "VBAT", "SW": "SW", "VOUT": "VOUT", "GND": "GND"}, [])


def test_binding_errors_name_the_part_and_the_rule(tmp_path):
    comps = _boost_parts(C3={"footprint": "Capacitor_SMD:C_0603_1608Metric"},       # wrong footprint
                         C4={"pins": {"1": "VOUT", "2": "VBAT"}},                     # GND of the block on VBAT
                         R1={"pins": {"1": "SW", "2": "X"}},                          # internal SW leaves the block
                         U2={"placement": {"x": 5, "y": 5}})                          # anchored member
    d, libs = _design(tmp_path, comps)
    errs = "\n".join(_block_errors(d, libs))
    assert "C3 has footprint Capacitor_SMD:C_0603_1608Metric" in errs
    assert "block net GND is GND on one pin and VBAT on C4.2" in errs
    assert "internal net SW of tps61299_boost (SW) also reaches R1.1" in errs
    assert "U2 has its own placement" in errs


def test_unknown_block_and_unbound_role_are_errors(tmp_path):
    d, libs = _design(tmp_path, entry={"use": "tps61299_boost", "parts": {"U": "U2", "L": "L1", "CIN": "C3"}})
    assert any("role COUT of tps61299_boost is not bound" in e for e in _block_errors(d, libs))
    d, libs = _design(tmp_path, entry={"use": "no_such_block", "parts": {}})
    assert any("block 'no_such_block' not found" in e for e in _block_errors(d, libs))


def test_instantiate_mode_adds_the_parts_and_save_leaves_them_out(tmp_path):
    comps = {k: v for k, v in _boost_parts().items() if k in ("J1", "R1", "R2")}
    d, libs = _design(tmp_path, comps, entry={"use": "tps61299_boost", "nets": {"VIN": "VBAT", "VOUT": "VOUT", "GND": "GND"}})
    assert {"U2", "L1", "C3", "C4"} <= set(d.components)
    assert d.components["L1"].pins == {"1": "VBAT", "2": "boost_SW"}
    assert _block_errors(d, libs) == []
    d.save()
    assert "U2" not in yaml.safe_load((tmp_path / "design.yaml").read_text())["components"]


# ---- placement ---------------------------------------------------------------------------------------------------

def _rigid(parts, u, block):
    """Every member sits where the block puts it relative to the unit origin."""
    for role, ref in BOOST.items():
        q = block["placement"][role]
        dx, dy = placer.rot(q["x"], q["y"], u.r)
        p = parts[ref]
        assert math.isclose(p.x, u.x + dx, abs_tol=1e-9) and math.isclose(p.y, u.y + dy, abs_tol=1e-9), ref
        assert p.r == (q.get("rot", 0) + u.r) % 360, ref


def test_placer_moves_a_block_as_one_rigid_unit(tmp_path):
    d, libs = _design(tmp_path)
    parts = placer.build_parts(d, libs)
    (u,) = parts.blocks
    assert not any(parts[r].fixed for r in BOOST.values())
    pl = placer.Placer(d, parts, 30, 22, seed=3)
    pl.run(4000)
    _rigid(parts, u, blocks.load("tps61299_boost"))
    assert "block:boost" not in parts  # the courtyard part stays inside the placer
    assert not [v for v in pl.violations() if "overlap" in v]


def test_incremental_cost_equals_full_with_a_block(tmp_path):
    d, libs = _design(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 30, 22, seed=3)
    rng = random.Random(7)
    movable = pl._movable()
    assert any(isinstance(m, placer.Rigid) for m in movable)
    for p in movable:  # crowded start: overlaps between the block courtyard and the loose parts
        p.x, p.y = rng.uniform(10, 18), rng.uniform(8, 14)
        pl._refs(p)
    cur = pl.cost()
    assert math.isclose(cur, pl.cost_full(), rel_tol=1e-9)
    seen = pl.t_ov
    for i in range(200):
        p = rng.choice(movable)
        old = (p.x, p.y, p.r)
        if i % 4 == 3:
            p.r = (p.r + 90) % 360
        else:
            p.x, p.y = p.x + rng.gauss(0, 2), p.y + rng.gauss(0, 2)
        new = pl._update(pl._refs(p))
        seen = max(seen, pl.t_ov)
        assert math.isclose(new, pl.cost_full(), rel_tol=1e-9, abs_tol=1e-6), i
        if i % 3 == 0:
            p.x, p.y, p.r = old
            pl._refs(p)
            pl._undo()
            assert math.isclose(pl._total(), cur, rel_tol=1e-9, abs_tol=1e-6)
        else:
            cur = new
    assert seen > 0, "the test must exercise the block courtyard overlap"


def test_fixed_block_places_its_members_from_at(tmp_path):
    d, libs = _design(tmp_path, entry={"use": "tps61299_boost", "parts": dict(BOOST), "at": {"x": 15, "y": 11, "rot": 90}})
    parts = placer.build_parts(d, libs)
    (u,) = parts.blocks
    assert u.fixed and all(parts[r].fixed for r in BOOST.values())
    _rigid(parts, u, blocks.load("tps61299_boost"))
    assert blocks.anchor(d, "L1") == pytest.approx({"x": parts["L1"].x, "y": parts["L1"].y, "rot": parts["L1"].r})


# ---- board -------------------------------------------------------------------------------------------------------

def _board_segments(tree):
    return Counter(tuple(round(float(v), 4) for v in (n[1][1], n[1][2], n[2][1], n[2][2])) for n in tree
                   if isinstance(n, list) and n and n[0] == "segment")


def test_board_has_the_block_copper_once(tmp_path):
    d, libs = _design(tmp_path, entry={"use": "tps61299_boost", "parts": dict(BOOST), "at": {"x": 15, "y": 11, "rot": 270}})
    parts = placer.build_parts(d, libs)
    bt, bv = blocks.copper(d, parts)
    src = blocks.load("tps61299_boost")["copper"]
    assert len(bt) == sum(len(t["pts"]) - 1 for t in src["tracks"]) and len(bv) == len(src["vias"])
    unrouted = _board_segments(pcb.build_board(d, libs, parts))
    assert sum(unrouted.values()) == len(bt) and max(unrouted.values()) == 1
    # a routed board read back holds the block copper plus the router's: written once
    extra = [{"net": "VOUT", "layer": "F.Cu", "w": 0.25, "x1": 1.0, "y1": 1.0, "x2": 5.0, "y2": 1.0}]
    routed = pcb.build_board(d, libs, parts, [dict(t) for t in bt] + extra, [dict(v) for v in bv])
    segs = _board_segments(routed)
    assert sum(segs.values()) == len(bt) + 1 and max(segs.values()) == 1
    assert sum(1 for n in routed if isinstance(n, list) and n and n[0] == "via") == len(bv)


def test_router_skips_only_the_nets_the_block_closes(tmp_path):
    d, libs = _design(tmp_path)
    assert blocks.connected_nets(blocks.load("tps61299_boost"), libs) == {"SW", "VIN", "VOUT"}
    assert blocks.closed_nets(d, libs) == {"SW"}  # VBAT and VOUT go on to J1 and R1


def test_block_drc_rule_and_keepouts_follow_the_board(tmp_path):
    d, libs = _design(tmp_path)
    assert blocks.drc_rules(d) == ['(rule "boost fine pitch" (condition "A.memberOfFootprint(\'U2\')") '
                                   '(constraint clearance (min 0.15mm)))']
    raw = {"project": "esp", "board": {"width_mm": 40, "height_mm": 30, "layers": 2},
           "blocks": {"mcu": {"use": "esp32c3_mini_decoupling", "nets": {"VDD": "+3V3", "GND": "GND"},
                              "at": {"x": 20, "y": 15, "rot": 0}}}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    parts = placer.build_parts(d, Libs(design=d))
    assert blocks.keepout_zones(d, parts) == []  # inner-layer keep-out, 2-layer board
    d.raw["board"]["layers"] = 4
    (z,) = blocks.keepout_zones(d, parts)
    assert [str(x) for x in z[3][1:]] == ["In1.Cu", "In2.Cu"]


# ---- extraction --------------------------------------------------------------------------------------------------

def test_extract_round_trip(tmp_path):
    d, libs = _design(tmp_path, entry={"use": "tps61299_boost", "parts": dict(BOOST), "at": {"x": 15, "y": 11, "rot": 0}})
    parts = placer.build_parts(d, libs)
    f = tmp_path / "b.kicad_pcb"
    pcb.write_board(f, pcb.build_board(d, libs, parts))
    got = authoring.extract(f, list(BOOST.values()), "boost_copy", "test board", roles=dict(BOOST), design=d, libs=libs)
    src = blocks.load("tps61299_boost")
    assert got["ports"] == ["GND", "VBAT", "VOUT"]  # J1 and R1 reach these nets
    for role in BOOST:
        q, g = src["placement"][role], got["placement"][role]
        assert (g["x"], g["y"], g["rot"]) == pytest.approx((q["x"], q["y"], q.get("rot", 0)), abs=1e-4)
    sw = [t for t in got["copper"]["tracks"] if t["net"] == "SW"]
    assert len(sw) == 2  # the internal net, whole
    n = sum(len(t["pts"]) - 1 for t in src["copper"]["tracks"])
    assert len(got["copper"]["tracks"]) == n and len(got["copper"]["vias"]) == 3  # the port copper inside the courtyard
    out = blocks.write(got, tmp_path / "lib")
    again = yaml.safe_load(out.read_text())
    assert again["roles"]["U"]["pins"]["2"] == "SW" and again["source"] == "test board"


# ---- router ------------------------------------------------------------------------------------------------------

@needs_router
@pytest.mark.slow
def test_routed_board_keeps_the_block_copper(tmp_path):
    from hwflow.pipeline import stage_layout
    comps = {k: v for k, v in _boost_parts().items() if k == "J1"}
    d, libs = _design(tmp_path, comps, entry={"use": "tps61299_boost", "nets": {"VIN": "VBAT", "VOUT": "VOUT", "GND": "GND"}})
    r = stage_layout(d, libs, seeds=1, iters=3000)
    assert r["unrouted"] == 0 and r["drc_errors"] == 0, r["drc"]
    rb = pcb.read_board(tmp_path / "build" / "blk.kicad_pcb")
    segs = Counter((s["net"], s["layer"], round(s["x1"], 4), round(s["y1"], 4), round(s["x2"], 4), round(s["y2"], 4))
                   for s in rb["segments"])
    assert max(segs.values()) == 1
    bt, _ = blocks.copper(d, {k: placer.Part(k, 0, 0, 0, 0, {}, x=v["x"], y=v["y"], r=v["r"])
                              for k, v in rb["footprints"].items()})
    have = {(s["net"], s["layer"], s["x1"], s["y1"], s["x2"], s["y2"]) for s in rb["segments"]}
    assert all((t["net"], t["layer"], t["x1"], t["y1"], t["x2"], t["y2"]) in have for t in bt)
    assert len([s for s in rb["segments"] if s["net"] == "boost_SW"]) == 2  # the closed net is not routed again


def test_library_root_follows_env_and_use(tmp_path, monkeypatch):
    """library_root is cached on its environment variables (the placer asks for it thousands of times) but still
    follows a changed HWFLOW_BLOCKS_DIR and the settings a `use()` block sets."""
    from hwflow.core import blocks as B
    from hwflow.core.settings import Settings, use
    monkeypatch.setenv("HWFLOW_BLOCKS_DIR", str(tmp_path / "a"))
    assert B.library_root() == tmp_path / "a"
    monkeypatch.setenv("HWFLOW_BLOCKS_DIR", str(tmp_path / "b"))
    assert B.library_root() == tmp_path / "b"
    s = Settings()
    s.paths.blocks_dir = str(tmp_path / "c")
    with use(s):
        assert B.library_root() == tmp_path / "c"
    assert B.library_root() == tmp_path / "b"


def test_block_match_by_function_before_capture(tmp_path):
    """The parts stage has no captured parts to match on footprints: `block match` names the library blocks whose
    active part or function the architecture names (thermo-e2e-3 drew a discrete H-bridge next to a DRV8837 block)."""
    from hwflow.core import blocks as B
    from hwflow.core.design import Design
    (tmp_path / "design.yaml").write_text("project: t\nboard: {width_mm: 30, height_mm: 20, layers: 2}\n")
    (tmp_path / "architecture.md").write_text("Relay drive: an H-bridge per latching coil (DRV8837 or discrete).\n")
    res = B.by_function(Design.load(tmp_path))
    hb = next(m for m in res if m["block"] == "drv8837_hbridge")
    assert "DRV8837" in hb["terms"] and hb["active_part"] and hb["files"] == ["architecture.md"]
    assert res[0]["active_part"]


def test_block_via_slides_along_its_track_off_a_pad_under_the_block(tmp_path):
    """A part on the other board side may lie under a block, and a block via goes through the board: a via closer
    than the clearance to its pad slides along the block's own track of the via's net, as little as it must."""
    d, libs = _design(tmp_path, entry={"use": "tps61299_boost", "parts": dict(BOOST), "at": {"x": 15, "y": 11, "rot": 0}})
    parts = placer.build_parts(d, libs)
    bt, bv = blocks.copper(d, parts)
    assert {(v["x"], v["y"]) for v in bv} == {(16.75, 8.25), (18.9, 9.1), (15.75, 12.5)}  # where the block puts them
    # a 1 x 1 mm pad of a back-side part 0.1 mm below the via at (15.75, 12.5); the via's GND track runs up from it
    parts["X9"] = placer.Part("X9", 1.0, 1.0, 0.0, 0.0, {"1": (0.0, 0.0)}, x=15.75, y=13.4, pad_size={"1": (1.0, 1.0)})
    _, moved = blocks.copper(d, parts)
    v = next(v for v in moved if v["x"] == 15.75)
    assert 12.37 <= v["y"] <= 12.39   # 0.21 mm from the pad (clearance 0.2 + 0.01), on the track x = 15.75
    assert [(w["x"], w["y"]) for w in moved if w is not v] == [(16.75, 8.25), (18.9, 9.1)]
    # the board writer passes the net-class clearance: a larger one moves it further
    _, far = blocks.copper(d, parts, lambda a, b: 0.3)
    assert next(w for w in far if w["x"] == 15.75)["y"] < v["y"] - 0.09
    # no point of the track clears the pad: the via stays (KiCad DRC reports it)
    parts["X9"] = placer.Part("X9", 9.0, 9.0, 0.0, 0.0, {"1": (0.0, 0.0)}, x=15.75, y=12.5, pad_size={"1": (9.0, 9.0)})
    _, stuck = blocks.copper(d, parts)
    assert (15.75, 12.5) in {(w["x"], w["y"]) for w in stuck}
