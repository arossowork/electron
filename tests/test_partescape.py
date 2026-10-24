"""Escape proofs of a single part (partescape): fine-pitch pads of wide nets, walled-in pads without via room, and
the perimeter of a part, from library footprints, pin nets and net widths, before any placement."""
import pytest
from types import SimpleNamespace

import yaml

from hwflow.core import design as design_mod
from hwflow import diagnose, feasibility, partescape
from hwflow.core.design import Design


def _pad(n, x, y, w, h, shape="rect", kind="smd"):
    layers = ["*.Cu", "*.Mask"] if kind == "thru_hole" else ["F.Cu", "F.Mask", "F.Paste"]
    return {"number": n, "kind": kind, "shape": shape, "x": x, "y": y, "rot": 0.0, "w": w, "h": h, "layers": layers,
            "drill": None, "rratio": 0.25, "hull": None}


def _libs(fps: dict):
    def footprint(fp_id):
        return {"id": fp_id, "pads": fps[fp_id], "bbox": (-5, -5, 5, 5), "sides": {"front": (-5, -5, 5, 5)}}
    return SimpleNamespace(footprint=footprint)


def _design(tmp_path, comps: dict, nets=None, **board):
    b = {"layers": 4, "width_mm": 40, "height_mm": 30, "track_mm": 0.2, "clearance_mm": 0.2, **board}
    for r, c in list(comps.items()):  # every net also goes to a capacitor, so it has to leave the part
        c.setdefault("value", "x")
    others = {}
    for c in comps.values():
        for net in set(c["pins"].values()):
            others[f"C{len(others) + 1}"] = {"footprint": "C", "pins": {"1": net, "2": "GND" if net != "GND" else "X"}}
    raw = {"project": "pe", "board": b, "nets": nets or {}, "components": {**comps, **others}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    return Design.load(tmp_path)


C_PADS = [_pad("1", -0.5, 0, 0.5, 0.5), _pad("2", 0.5, 0, 0.5, 0.5)]


def _sot563():
    """SOT-563 as in the KiCad library: two columns of 3 pads, 0.5 mm pitch, 0.7 x 0.34 mm."""
    return [_pad(str(i + 1), -0.75, -0.5 + 0.5 * i, 0.7, 0.34) for i in range(3)] + \
           [_pad(str(6 - i), 0.75, -0.5 + 0.5 * i, 0.7, 0.34) for i in range(3)]


def _qfn16(ep="17"):
    """QFN-16 3 x 3 mm, 0.5 mm pitch, 0.8 x 0.25 mm pads, 1.7 mm exposed pad."""
    pads, n = [], 1
    for side in range(4):
        for k in range(4):
            t = -0.75 + 0.5 * k
            x, y, w, h = [(-1.45, t, 0.8, 0.25), (t, 1.45, 0.25, 0.8), (1.45, -t, 0.8, 0.25), (-t, -1.45, 0.25, 0.8)][side]
            pads.append(_pad(str(n), x, y, w, h))
            n += 1
    return pads + [_pad(ep, 0, 0, 1.7, 1.7)]


def _bga(pitch, ball, n=4):
    rows = "ABCDEFGH"
    return [_pad(f"{rows[i]}{j + 1}", (j - (n - 1) / 2) * pitch, (i - (n - 1) / 2) * pitch, ball, ball, "circle")
            for i in range(n) for j in range(n)]


def test_wide_nets_at_a_fine_pitch_pad_row_are_a_package_problem(tmp_path):
    pins = {"1": "VIN", "2": "SW", "3": "VIN", "4": "GND", "5": "VOUT", "6": "GND"}
    libs = _libs({"SOT563": _sot563(), "C": C_PADS})
    d = _design(tmp_path, {"U2": {"footprint": "SOT563", "pins": pins}},
                nets={"SW": {"width_mm": 0.6}, "VOUT": {"width_mm": 0.4}})
    f = partescape.findings(d, libs)
    assert [(x["check"], x["refs"], x["nets"]) for x in f] == [("pad_escape", ["U2"], ["SW", "VOUT"])]
    assert f[0]["need_mm"] == 0.6 and f[0]["have_mm"] == 0.26          # 2 x (0.5 - 0.17 - 0.2)
    assert "pitch >= 0.67 mm" in f[0]["msg"] and f[0]["back_to"] == "parts"
    # the same numbers as the layout diagnosis on a board
    board_fp = {"pads": [{**p, "net": pins[p["number"]], "w": p["w"], "h": p["h"]} for p in _sot563()]}
    assert [e["pad"] for e in diagnose.escape(board_fp, {"SW": {"width": 0.6}, "VOUT": {"width": 0.4}}, 0.2, 0.2)] == ["2", "5"]
    # one wide net needs one neck: allowed
    d = _design(tmp_path, {"U2": {"footprint": "SOT563", "pins": pins}}, nets={"SW": {"width_mm": 0.6}})
    assert partescape.findings(d, libs) == []
    # a DRC rule that lowers the clearance at U2 to 0.1 mm lets 0.46 mm out: VOUT 0.4 mm fits, SW alone necks
    d = _design(tmp_path, {"U2": {"footprint": "SOT563", "pins": pins}},
                nets={"SW": {"width_mm": 0.6}, "VOUT": {"width_mm": 0.4}},
                drc_rules=["(rule fp (condition \"A.memberOfFootprint('U2')\") (constraint clearance (min 0.1mm)))"])
    assert partescape.findings(d, libs) == []


def test_a_walled_in_thermal_pad_needs_a_via_or_a_same_net_ring_pin(tmp_path):
    pins = {str(i): f"S{i}" for i in range(1, 17)} | {"17": "GND"}
    libs = _libs({"QFN": _qfn16(), "C": C_PADS})
    d = _design(tmp_path, {"U1": {"footprint": "QFN", "pins": pins}})
    f = partescape.findings(d, libs)
    assert [(x["check"], x["nets"]) for x in f] == [("part_escape", ["GND"])]
    assert "room for 0 vias where 1 is needed" in f[0]["msg"] and f[0]["have_mm"] < f[0]["need_mm"] == 0.6
    # declared thermal vias in the exposed pad
    d = _design(tmp_path, {"U1": {"footprint": "QFN", "pins": pins, "thermal_vias": {"pad": "17", "count": 4}}})
    assert partescape.findings(d, libs) == []
    # a GND ring pin: the exposed pad joins it on the top layer
    d = _design(tmp_path, {"U1": {"footprint": "QFN", "pins": pins | {"6": "GND"}}})
    assert partescape.findings(d, libs) == []


def test_inner_balls_of_a_fine_pitch_bga(tmp_path):
    libs = _libs({"BGA08": _bga(0.8, 0.4), "BGA10": _bga(1.0, 0.4), "C": C_PADS})
    pins = {f"{r}{j}": f"N{r}{j}" for r in "ABCD" for j in range(1, 5)}
    # 0.8 mm pitch, 0.4 mm balls: 0.4 mm between balls, a 0.15 mm track needs 0.55; a 0.6 mm via no room at all
    d = _design(tmp_path, {"U1": {"footprint": "BGA08", "pins": pins}})
    f = partescape.findings(d, libs)
    assert [(x["check"], x["nets"]) for x in f] == [("part_escape", ["NB2", "NB3", "NC2", "NC3"])]
    # 1.0 mm pitch: a 0.15 mm track passes between two balls (0.6 >= 0.55)
    d = _design(tmp_path, {"U1": {"footprint": "BGA10", "pins": pins}})
    assert partescape.findings(d, libs) == []
    # 0.8 mm pitch with a 0.1 mm clearance: 0.35 mm tracks pass
    d = _design(tmp_path, {"U1": {"footprint": "BGA08", "pins": pins}}, clearance_mm=0.1)
    assert partescape.findings(d, libs) == []


def _strip(n, pitch, pad_w):
    """Two staggered rows on each long side of a thermal pad: the inner row leaves between the outer pads."""
    pads, k = [], 1
    for sy in (1, -1):
        for i in range(n):
            pads.append(_pad(str(k), i * pitch, sy * 1.5, pad_w, 0.3))
            pads.append(_pad(str(k + 1), i * pitch + pitch / 2, sy * 1.0, pad_w, 0.3))
            k += 2
    return pads + [_pad("EP", (n - 0.5) * pitch / 2, 0, n * pitch, 1.4)]


@pytest.mark.slow
def test_the_perimeter_of_a_part_bounds_the_nets_that_leave_it(tmp_path):
    n = 60
    libs = _libs({"STRIP": _strip(n, 0.32, 0.01), "WIDE": _strip(n, 0.5, 0.01), "C": C_PADS})
    pins = {str(i): f"S{i}" for i in range(1, 4 * n + 1)} | {"EP": "GND"}
    kw = {"clearance_mm": 0.1, "min_track_mm": 0.1}
    comp = {"footprint": "STRIP", "pins": pins, "thermal_vias": {"pad": "EP"}}
    d = _design(tmp_path, {"U1": comp}, **kw)
    f = partescape.findings(d, libs)
    assert [x["check"] for x in f] == ["part_escape"] and "of the" in f[0]["msg"]
    assert f[0]["need_mm"] == round(4 * n * 0.2, 3) and f[0]["have_mm"] < f[0]["need_mm"]
    d = _design(tmp_path, {"U1": {**comp, "footprint": "WIDE"}}, **kw)
    assert partescape.findings(d, libs) == []


def test_hwflow_check_reports_escape_findings_as_errors(tmp_path, monkeypatch):
    pins = {"1": "VIN", "2": "SW", "3": "VIN", "4": "GND", "5": "VOUT", "6": "GND"}
    d = _design(tmp_path, {"U2": {"footprint": "SOT563", "pins": pins}},
                nets={"SW": {"width_mm": 0.6}, "VOUT": {"width_mm": 0.4}})
    libs = _libs({"SOT563": _sot563(), "C": C_PADS})
    errs = [e for e in feasibility.check(d, libs) if e["code"] == "escape"]  # the other checks need the full libs
    assert [(e["sev"], e["code"]) for e in errs] == [("error", "escape")] and errs[0]["msg"].startswith("U2 ")
    assert ("escape", "hwflow.partescape") in feasibility.FEASIBILITY_CHECKS
    monkeypatch.setattr(feasibility, "FEASIBILITY_CHECKS", [x for x in feasibility.FEASIBILITY_CHECKS if x[0] == "escape"])
    assert [f["code"] for f in feasibility.findings(d, libs)] == ["escape"]
    assert any(f["code"] == "escape" for f in design_mod.check(d, libs=SimpleNamespace(
        footprint=libs.footprint, symbol=lambda s: (_ for _ in ()).throw(KeyError(s)))))
