"""The early layout feasibility gate (`hwflow floorplan` in stages 2-4): connector edges and envelopes, isolation
groups, footprints on placeholder items, the legality gate's fill before any placement, fixed-rule severities."""
import yaml

from hwflow.core import floorplan as corefp
from hwflow.core.design import Design
from hwflow.core.libs import Libs
from hwflow.layout.place import floorplan

RS = "Resistor_SMD:R_0603_1608Metric"
HDR = "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"


def make(tmp_path, fp, board=None, comps=None, nets=None, extra=None):
    raw = {"project": "fe", "board": {"width_mm": 40, "height_mm": 30, "layers": 2, "gnd_pour": False, **(board or {})},
           "floorplan": fp, **(extra or {})}
    if comps:
        raw["components"] = comps
    if nets:
        raw["nets"] = nets
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    return Design.load(tmp_path)


def run(d, **kw):
    return floorplan.check(d, Libs(design=d), place=False, **kw)


def names(res, ok=False):
    return [(c["name"], c["msg"]) for c in res["checks"] if c["ok"] is ok]


def test_item_footprint_gives_the_placeholder_its_courtyard(tmp_path):
    d = make(tmp_path, {"construction": "single", "items": [{"name": "conn", "footprint": HDR}]})
    res = run(d)
    p = res["_parts"]["conn"]
    w, h = sorted((p.w, p.h))
    assert 2.5 < w < 4.5 and 10 < h < 12.5  # 1x04 header courtyard, plus the placer margin
    bad = make(tmp_path, {"construction": "single", "items": [{"name": "conn", "footprint": "no-colon"}]})
    assert any("footprint must be a library id" in e for e in corefp.validate(bad))


def test_edge_stretch_too_short_for_the_connector(tmp_path):
    # a 12 x 20 mm part on a 30 mm left edge with a 16 mm notch in the middle (plus the 1 mm margin): 6 mm stretches
    fp = {"construction": "single",
          "regions": [{"name": "notch", "kind": "keepout", "rect": [0, 7, 3, 23], "cut": True}],
          "items": [{"name": "plug", "size_mm": [12, 20], "edge": "left"}]}
    res = run(make(tmp_path, fp, board={"width_mm": 40, "height_mm": 30}))
    fails = names(res)
    assert any(n == "edge" and "plug" in m and "left edge" in m for n, m in fails), fails
    # two connectors sharing one edge: their short sides add up beyond the edge
    fp2 = {"construction": "single", "items": [{"name": "a", "size_mm": [20, 18], "edge": "top"},
                                                {"name": "b", "size_mm": [22, 18], "edge": "top"}]}
    res = run(make(tmp_path, fp2, board={"width_mm": 30, "height_mm": 60}))
    assert any(n == "edge" and "side by side" in m for n, m in names(res)), names(res)
    ok = run(make(tmp_path, dict(fp2, items=fp2["items"][:1]), board={"width_mm": 30, "height_mm": 60}))
    assert not any(n == "edge" for n, _ in names(ok))


def test_envelope_against_wall_cut_and_fixed_part(tmp_path):
    comps = {"J1": {"value": "conn", "symbol": "Device:R", "footprint": RS, "pins": {"1": "A", "2": "B"},
                    "placement": {"x": 6, "y": 15, "rot": 0, "fixed": True}},
             "R1": {"value": "1k", "symbol": "Device:R", "footprint": RS, "pins": {"1": "A", "2": "B"},
                    "placement": {"x": 1.8, "y": 15, "rot": 0, "fixed": True}}}
    fp = {"construction": "single", "wall_mm": {"left": 2},
          "regions": [{"name": "boss", "kind": "keepout", "circle": [0, 25, 3], "cut": True}],
          "items": [{"name": "conn", "refs": ["J1"], "edge": "left", "envelope_mm": 12}]}
    res = run(make(tmp_path, fp, comps=comps))
    fails = names(res)
    assert any(n == "envelope" and "enclosure wall is 2 mm" in m for n, m in fails), fails
    assert any(n == "envelope" and "overlaps R1" in m for n, m in fails), fails
    # a 1 mm envelope stays on the board and clear of R1 and the boss
    fp["items"][0]["envelope_mm"] = 1.0
    assert not any(n == "envelope" for n, _ in names(run(make(tmp_path, fp, comps=comps))))
    fp["items"][0]["edge"] = "bottom"  # downwards, across the boss below the connector
    fp["regions"][0]["circle"] = [6, 20, 3]
    fp["items"][0]["envelope_mm"] = 6.0
    assert any(n == "envelope" and "crosses cut boss" in m for n, m in names(run(make(tmp_path, fp, comps=comps))))


def test_isolation_group_lint_and_bubble(tmp_path):
    comps = {f"R{i}": {"value": "1k", "symbol": "Device:R", "footprint": RS, "pins": {"1": "AC_L", "2": "AC_N"}}
             for i in range(3)}
    comps["R9"] = {"value": "1k", "symbol": "Device:R", "footprint": RS, "pins": {"1": "LV", "2": "GND"}}
    iso = [{"name": "mains", "nets": ["AC_L", "AC_N"], "clearance_mm": 6.0, "creepage_mm": 6.0}]
    fp = {"construction": "single"}
    d = make(tmp_path, fp, board={"layers": 4, "isolation": iso}, comps=comps)
    risks = names(run(d), ok=None)
    text = " | ".join(m for _, m in risks)
    assert "no between_mm" in text and "inner layers" in text and "no floorplan region" in text
    # a group confined by a region with between_mm and layers per net: no lint
    iso[0]["between_mm"] = 1.5
    fp = {"construction": "single", "regions": [{"name": "hv", "kind": "mains", "rect": [0, 0, 12, 30],
                                                 "nets": ["AC_*"], "isolation_mm": 6}]}
    nets = {"AC_L": {"layers": ["F.Cu"]}, "AC_N": {"layers": ["B.Cu"]}}
    d = make(tmp_path, fp, board={"layers": 4, "isolation": iso}, comps=comps, nets=nets)
    assert not [m for n, m in names(run(d), ok=None) if n == "isolation"]
    # the bubble: the group's parts with a 6 mm band leave no room for the others on a small board
    big = {f"U{i}": {"value": "x", "symbol": "Device:R", "footprint": HDR,
                     "pins": {"1": "AC_L", "2": "AC_N", "3": "AC_L", "4": "AC_N"}} for i in range(6)}
    big.update({f"V{i}": {"value": "x", "symbol": "Device:R", "footprint": HDR,
                          "pins": {"1": "S1", "2": "S2", "3": "S3", "4": "S4"}} for i in range(6)})
    d = make(tmp_path, {"construction": "single"}, board={"width_mm": 26, "height_mm": 18, "isolation": iso},
             comps=big)
    assert any(n == "isolation" and "isolation bubble" in m for n, m in names(run(d))), names(run(d))


def test_placer_fill_fails_before_any_placement(tmp_path):
    comps = {f"J{i}": {"value": "x", "symbol": "Device:R", "footprint": HDR,
                       "pins": {"1": f"N{i}", "2": f"M{i}", "3": f"N{i}", "4": f"M{i}"}} for i in range(12)}
    d = make(tmp_path, {"construction": "single"}, board={"width_mm": 20, "height_mm": 22}, comps=comps)
    res = floorplan.check(d, Libs(design=d), iters=500, seeds=1)
    fails = names(res)
    assert any(n == "area" and "no legal placement exists" in m and "a board of at least" in m for n, m in fails)
    assert res["placement"] is None  # skipped: the area proof already sends it back, in seconds
    assert any(n == "placement" and "skipped" in m for n, m in names(res, ok=None))


def test_fixed_rule_severity():
    fp = {"regions": [{"name": "notch", "cut": True, "poly": __import__("shapely").geometry.box(0, 0, 2, 2)}],
          "margin": 1.0}
    iso = ("isolation J1.3/K2.A2: 0.32 mm short (AC / X: isolation g 6 mm + 0.5 mm placer margin between a member "
           "and another net, pad bounding boxes)")
    assert floorplan._fixed_severity({}, fp, iso, 0.25)[0] is None
    assert floorplan._fixed_severity({}, fp, iso.replace("0.32 mm short", "0.80 mm short"), 0.25)[0] is False


def test_block_holding_an_allowed_part_is_allowed(tmp_path):
    d = make(tmp_path, {"construction": "single", "regions": [
        {"name": "ant", "kind": "antenna", "rect": [0, 0, 10, 10], "allow": ["mod"]}],
        "items": [{"name": "mod", "refs": ["U1"]}]},
        comps={"U1": {"value": "x", "symbol": "Device:R", "footprint": RS, "pins": {"1": "A", "2": "B"}}},
        extra={"blocks": {"mcu": {"use": "x", "parts": {"U": "U1"}}}})
    from hwflow.layout.place import placer
    parts = placer.build_parts(d, Libs(design=d))
    rules, _ = corefp.placer_regions(d, parts)
    assert "block:mcu" in rules[0]["allow"] and "U1" in rules[0]["allow"]


# ---------------------------------------------------------------- stage timing (`hwflow time`)


def test_flow_marks_count_rework(tmp_path):
    import json

    from hwflow.pipeline import timing
    h, t0 = 3600.0, 1_700_000_000.0
    for at, st in ((0, "1-requirements"), (1 * h, "2-architecture"), (2 * h, "3-parts"), (3 * h, "4-circuit"),
                   (5 * h, "7-layout"), (8 * h, "2-architecture"), (9 * h, "7-layout"), (10 * h, "done")):
        timing.mark(tmp_path, st, at=t0 + at)
    f = timing.flow(tmp_path)
    assert f["total_h"] == 10.0 and not f["open"]
    assert f["stages"]["2-architecture"] == {"h": 2.0, "entries": 2, "rework_h": 1.0}
    assert f["stages"]["7-layout"]["rework_h"] == 0.0 and f["rework_h"] == 1.0
    (tmp_path / "build" / "history.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"ts": "a", "verdict": "FAIL: x", "timing": {"wall_s": 100, "stages": {"sim": 40, "layout": 0.0},
                                                      "steps": {"sim.mc_s": 30}}},
        {"ts": "b", "verdict": "PASS", "timing": {"wall_s": 900, "stages": {"sim": 0.0, "layout": 800},
                                                   "steps": {"layout.route_s": 600}}},
        {"ts": "c", "verdict": "PASS"}]) + "\n")
    b = timing.builds(tmp_path)
    assert b["n"] == 2 and b["layout_builds"] == 1 and b["stages"] == {"sim": 40.0, "layout": 800.0}
    text = timing.summary(tmp_path)
    assert "rework 1.00 h" in text and "| layout | 13.3 min | 95% |" in text and "layout.route_s" in text


def test_build_records_stage_timing(tmp_path):
    import json
    import shutil

    from hwflow.cli import main
    proj = tmp_path / "p"
    shutil.copytree("examples/sensor_preamp", proj, ignore=shutil.ignore_patterns("build"))
    main(["build", str(proj), "--until", "check"])
    st = json.loads((proj / "build" / "status.json").read_text())
    assert st["timing"]["stages"]["check"]["s"] >= 0 and st["timing"]["wall_s"] > 0
    line = json.loads((proj / "build" / "history.jsonl").read_text().splitlines()[-1])
    assert "check" in line["timing"]["stages"] and line["timing"]["run_id"] == st["timing"]["run_id"]
