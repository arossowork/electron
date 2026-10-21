"""Requirement budgets (design.yaml budgets:): validation, track widths before routing, copper measured after."""
import json
from types import SimpleNamespace

import pytest
import yaml

from hwflow import budgets
from hwflow.design import Design, check
from hwflow.postlayout import RHO_CU, Extraction

GUARD = {"name": "guard", "req": "R4", "kind": "copper_r", "path": ["J1.1 -> Q1.3, Q2.3", "Q1.2, Q2.2 -> C1.1"],
         "current_a": 0.7, "max_mohm": 20, "source": "R4 50 mV - 36 mV pre-layout at 0.7 A = 20 mOhm"}


def _design(tmp_path, bud, comps=None):
    comps = comps or {"J1": {"pins": {"1": "VIN", "2": "GND"}}, "Q1": {"pins": {"1": "G", "2": "VP", "3": "VIN"}},
                      "Q2": {"pins": {"1": "G", "2": "VP", "3": "VIN"}}, "C1": {"pins": {"1": "VP", "2": "GND"}}}
    raw = {"project": "bud", "board": {"layers": 2, "gnd_pour": False}, "budgets": bud, "components": comps}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    return Design.load(tmp_path)


def test_budget_fields_are_validated(tmp_path):
    d = _design(tmp_path, [GUARD])
    assert not [x for x in check(d) if x["code"] == "budget"]
    (lg, bad) = budgets.legs(d, GUARD)
    assert not bad and [(x["net"], x["a"], x["b"]) for x in lg] == [
        ("VIN", [("J1", "1")], [("Q1", "3"), ("Q2", "3")]), ("VP", [("Q1", "2"), ("Q2", "2")], [("C1", "1")])]
    bad = [dict(GUARD, path=["J1.1 -> C1.1"]),                     # across a part: two nets
           dict(GUARD, path=["J1.9 -> Q1.3"]),                     # no such pin
           dict(GUARD, source=""), dict(GUARD, max_mohm=None), dict(GUARD, current_a=None),
           dict(GUARD, kind="copper_x"), dict(GUARD, path=["NOPE"])]
    d = _design(tmp_path, bad)
    msgs = [x["msg"] for x in check(d) if x["code"] == "budget"]
    assert len(msgs) == len(bad)
    assert "spans nets" in msgs[0] and "J1.9" in msgs[1] and "source" in msgs[2] and "max_mohm" in msgs[3]
    assert "current_a" in msgs[4] and "copper_x" in msgs[5] and "neither" in msgs[6]


def _part(x, y, pads):
    return SimpleNamespace(x=x, y=y, pads=pads, pad=lambda n, x=x, y=y, pads=pads: (x + pads[n][0], y + pads[n][1]))


def test_width_from_budget_length_and_copper(tmp_path):
    """w = rho L / (t (R_max - vias R_via)), L = detour x Manhattan, one width for the path, rounded up to 0.05 mm."""
    d = _design(tmp_path, [GUARD])
    parts = {"J1": _part(0, 0, {"1": (0, 0)}), "Q1": _part(10, 0, {"2": (0, 5), "3": (0, 0)}),
             "Q2": _part(10, 20, {"2": (0, 5), "3": (0, 0)}), "C1": _part(30, 10, {"1": (0, 0)})}
    w, (row,) = budgets.widths(d, parts)
    L = (10 + (20 + 5)) * budgets.DETOUR                    # J1.1 -> Q1.3 10 mm; Q1.2 (10, 5) -> C1.1 (30, 10) 25 mm
    left = 20e-3 - 4 * budgets.via_r(d.board)
    need = RHO_CU * L * 1e-3 / (0.035e-3 * left) * 1e3
    assert row["need_mm"] == pytest.approx(need, abs=0.01)
    assert w == {"VIN": row["width_mm"], "VP": row["width_mm"]} and row["width_mm"] >= need > row["width_mm"] - 0.05
    assert row["ok"] and row["est_mohm"] <= 20
    # a budget that no width under the cap meets says so; the net gets the cap
    d.raw["budgets"] = [dict(GUARD, max_mohm=5)]
    w, (row,) = budgets.widths(d, parts)
    assert not row["ok"] and w["VP"] == budgets.MAX_WIDTH_MM and "width alone cannot meet it" in row["msg"]
    d.raw["budgets"] = [dict(GUARD, max_mohm=1)]                 # less than the vias alone
    w, (row,) = budgets.widths(d, parts)
    assert not row["ok"] and row["need_mm"] is None and w["VP"] == budgets.MAX_WIDTH_MM
    # net_classes keeps the wider of the net's own width and the budget's, never narrower
    from hwflow.pcb import net_classes
    d.raw["nets"] = {"VIN": {"width_mm": 4.0}}
    d.budget_widths = {"VIN": 1.0, "VP": 1.0}
    nc = net_classes(d)
    assert nc["VIN"]["width"] == 4.0 and nc["VP"]["width"] == 1.0 and nc["G"]["width"] == d.board["track_mm"]


PCB = """(kicad_pcb (version 20241229) (net 0 "") (net 1 "VIN") (net 2 "VP")
{fps}
{copper})"""


def _fp(ref, x, y, pads):
    body = " ".join(f'(pad "{n}" smd rect (at {px} {py}) (size 1 1) (layers "F.Cu") (net {k} "{net}"))'
                    for n, px, py, k, net in pads)
    return f'(footprint "x:y" (at {x} {y}) (property "Reference" "{ref}") {body})'


def _seg(x1, y1, x2, y2, w, layer="F.Cu", net=1):
    return f'(segment (start {x1} {y1}) (end {x2} {y2}) (width {w}) (layer "{layer}") (net {net}))'


def test_path_resistance_on_routed_copper(tmp_path):
    """A straight 50 mm x 1 mm track on 1 oz copper is rho L / (w t) = 24.6 mOhm; each via adds its barrel; two
    parallel parts share the current equally; the worst stretch names layer, width and length."""
    fps = [_fp("J1", 0, 0, [("1", 0, 0, 1, "VIN")]),
           _fp("Q1", 50, 0, [("3", 0, 0, 1, "VIN"), ("2", 0, 2, 2, "VP")]),
           _fp("Q2", 50, 10, [("3", 0, 0, 1, "VIN"), ("2", 0, 2, 2, "VP")]),
           _fp("C1", 70, 7, [("1", 0, 0, 2, "VP")])]
    copper = [_seg(0, 0, 25, 0, 1.0), _seg(25, 0, 45, 0, 1.0, "B.Cu"), _seg(45, 0, 50, 0, 1.0),
              '(via (at 25 0) (net 1))', '(via (at 45 0) (net 1))',                 # down to B.Cu and back
              _seg(50, 0, 50, 10, 1.0),                                   # J1 feeds Q1.3 and, 10 mm on, Q2.3
              _seg(50, 2, 70, 2, 0.5, net=2), _seg(50, 12, 70, 12, 0.5, net=2),
              _seg(70, 2, 70, 7, 0.5, net=2), _seg(70, 12, 70, 7, 0.5, net=2)]
    (tmp_path / "b.kicad_pcb").write_text(PCB.format(fps="\n".join(fps), copper="\n".join(copper)))
    comps = {"J1": {"pins": {"1": "VIN"}}, "Q1": {"pins": {"2": "VP", "3": "VIN"}},
             "Q2": {"pins": {"2": "VP", "3": "VIN"}}, "C1": {"pins": {"1": "VP"}}}
    one = dict(GUARD, name="trace", path=["J1.1 -> Q1.3"], max_mohm=30)
    d = _design(tmp_path, [one, dict(GUARD, max_mohm=30)], comps)
    ex = Extraction(d, tmp_path / "b.kicad_pcb")
    r1, r2 = budgets.measure(d, ex)
    t = 0.035e-3
    track = RHO_CU * 0.050 / (1e-3 * t)
    assert track == pytest.approx(24.57e-3, rel=1e-3)
    assert r1["value"] == pytest.approx((track + 2 * budgets.via_r(d.board)) * 1e3, rel=0.02) and r1["ok"]
    # J1 -> {Q1.3, Q2.3} at 0.5 A each: track + vias, plus 10 mm x 1 mm at 0.5 A (x 0.25 of its R)
    vin = track + 2 * budgets.via_r(d.board) + 0.25 * RHO_CU * 0.010 / (1e-3 * t)
    # {Q1.2, Q2.2} -> C1.1, 0.5 A each: Q1 branch 20 + 5 mm, Q2 branch 20 + 5 mm of 0.5 mm track (symmetric)
    vp = 2 * 0.25 * RHO_CU * 0.025 / (0.5e-3 * t)
    assert r2["legs"][0]["mohm"] == pytest.approx(vin * 1e3, rel=0.02)
    assert r2["legs"][1]["mohm"] == pytest.approx(vp * 1e3, rel=0.02)
    assert r2["value"] == pytest.approx((vin + vp) * 1e3, rel=0.02) and not r2["ok"]
    assert r2["drop_mv"] == pytest.approx(r2["value"] * 0.7, rel=1e-3)
    assert "VIN F.Cu 1.00 mm wide" in r2["worst"] and r2["msg"].endswith("FAIL")
    # the layout check of the post stage reports the same budgets
    from hwflow.postlayout import layout_checks
    got = [x for x in layout_checks(d, ex) if x["code"] == "budget"]
    assert [x["sev"] for x in got] == ["info", "error"]
    # copper length of a leg (kind copper_len): the shortest path, J1.1 -> Q1.3 is 50 mm
    d.raw["budgets"] = [{"name": "len", "kind": "copper_len", "path": ["J1.1 -> Q1.3"], "max_mm": 40, "source": "x"}]
    (r,) = budgets.measure(d, ex)
    assert r["value"] == pytest.approx(50.0) and not r["ok"]
    # an unrouted end is not measured, and is a failure
    d.raw["budgets"] = [dict(GUARD, path=["Q1.2 -> C1.1"])]
    ex.elements = [e for e in ex.elements if e["net"] != "VP"]
    (r,) = budgets.measure(d, ex)
    assert r["value"] is None and not r["ok"] and "not connected" in r["msg"]


def test_budget_with_req_verifies_the_requirement_in_trace(tmp_path):
    from hwflow.cli import _design_hash, trace
    d = _design(tmp_path, [GUARD])
    (tmp_path / "requirements.yaml").write_text(yaml.safe_dump({"requirements": [
        {"id": "R4", "text": "guard drop", "verification": "layout"}]}))
    (tmp_path / "build").mkdir()
    row = {"name": "guard", "req": "R4", "value": 25.0, "limit": 20.0, "unit": "mOhm", "ok": False}
    (tmp_path / "build" / "status.json").write_text(json.dumps({"layout": {
        "ok": False, "budgets": [row], "design_hash": _design_hash(d)}}))
    (r,) = trace(d)["rows"]
    assert r["status"] == "FAIL" and r["measures"] == ["budget guard 25.0/20 mOhm:FAIL"]
