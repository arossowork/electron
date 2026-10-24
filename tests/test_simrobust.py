"""Convergence fallback, floating-node leaks and scoped analyses (src/hwflow/simrobust.py)."""
import re
import shutil
from pathlib import Path

import pytest
import yaml

from hwflow.sim import simrobust, spice
from hwflow.core.design import Design
from hwflow.verify.checks import check

EX = Path(__file__).resolve().parents[1] / "examples" / "sensor_preamp"

SYN = {
    "project": "syn",
    "nets": {"GND": {"class": "ground"}, "RAIL": {"class": "power"}, "VIN": {"class": "power"}},
    "components": {
        "U1": {"value": "LDO", "symbol": "x:y", "footprint": "x:y", "pins": {"1": "VIN", "2": "GND", "3": "RAIL"},
               "spice": {"subckt": "LDO_GENERIC", "ports": ["1", "3", "2"], "params": {"VOUT": 3.3}}},
        "C1": {"value": "1u", "symbol": "x:y", "footprint": "x:y", "pins": {"1": "RAIL", "2": "GND"}},
        "U2": {"value": "OPA", "symbol": "x:y", "footprint": "x:y",
               "pins": {"1": "OUT", "2": "GND", "3": "SIG", "4": "OUT", "5": "RAIL"},
               "spice": {"subckt": "OPAMP_GENERIC", "ports": ["3", "4", "1", "5", "2"]}},
        "R1": {"value": "10k", "symbol": "x:y", "footprint": "x:y", "pins": {"1": "SIG", "2": "GND"}},
        "R2": {"value": "1k", "symbol": "x:y", "footprint": "x:y", "pins": {"1": "VIN", "2": "LOADN"}},
        "R3": {"value": "1k", "symbol": "x:y", "footprint": "x:y", "pins": {"1": "LOADN", "2": "GND"}},
        "C2": {"value": "1n", "symbol": "x:y", "footprint": "x:y", "pins": {"1": "OUT", "2": "FLT"}},
        "C3": {"value": "1n", "symbol": "x:y", "footprint": "x:y", "pins": {"1": "FLT", "2": "GND"}},
    },
    "sim": {
        "sources": [{"name": "VIN_SRC", "p": "VIN", "n": "GND", "value": "DC 5"},
                    {"name": "VSIG", "p": "SIG", "n": "GND", "value": "DC 1 AC 1"}],
        "analyses": [
            {"name": "dc", "cmd": "op", "temps": [25, 40],
             "measures": [{"name": "vout", "expr": "v(OUT)", "min": 0.9, "max": 1.1}]},
            {"name": "noise", "cmd": "noise v(OUT) VSIG dec 10 10 100k",
             "measures": [{"name": "n_uv", "plot": "noise2", "expr": "onoise_total*1e6"}]},
        ],
    },
}


def _design(tmp_path, raw=None, **sim):
    raw = yaml.safe_load(yaml.safe_dump(raw or SYN))
    raw["sim"].update(sim)
    (tmp_path / "p").mkdir(exist_ok=True)
    (tmp_path / "p" / "design.yaml").write_text(yaml.safe_dump(raw))
    return Design.load(tmp_path / "p")


# ------------------------------------------------------------------ floating nodes

def test_floating_node_detection_through_models():
    files = [str(spice.MODELS)]
    body = ["R1 a 0 1k", "C1 a b 1n", "C2 b c 1n", "R2 c d 1k",   # b alone floats; c-d float together
            "M1 e g 0 0 NMOS_LOGIC", "C3 g 0 1n",                 # a MOSFET gate is no DC path
            "XU1 in fbk out vcc 0 OPAMP_GENERIC", "C4 in 0 1n", "R3 out fbk 1k", "V1 vcc 0 3",
            "XU2 q 0 UNKNOWN_SUB", "C5 q 0 1n"]                   # unknown model: never a leak on a guess
    isl = simrobust.floating_islands(body, files)
    assert ["b"] in isl and ["c", "d"] in isl and ["g"] in isl
    assert not any("q" in i for i in isl)
    # the op-amp model ties its input to the inverting input (1 Tohm), and its output to ground through ROUT and a
    # behavioural source: the input has a DC path through the feedback resistor
    assert not any("in" in i or "e" in i for i in isl)


def test_leak_is_added_reported_and_a_check_warning(tmp_path):
    d = _design(tmp_path)
    net, _ = spice.build_netlist(d, d.raw["sim"]["analyses"][0])
    assert re.search(r"^Rhwf_leak_flt flt 0 1e\+09 noisy=0$", net, re.M)
    out, rc = spice.run_ngspice(net.replace("quit\n.endc", "print v(out)\nquit\n.endc"), tmp_path / "w", "leak")
    assert "singular matrix" not in out.lower() and rc == 0
    w = [f for f in check(d) if f["code"] == "floating-node"]
    assert w and "FLT" in w[0]["msg"] and w[0]["sev"] == "warn"
    rows = simrobust.leak_report(d, tmp_path / "w", None)
    assert rows[0]["net"] == "FLT" and abs(rows[0]["i_a"]) < 1e-9 and rows[0]["f_corner_hz"] == pytest.approx(
        1 / (2 * 3.14159265 * 1e9 * 2e-9), rel=1e-3)
    off = _design(tmp_path, leaks=False)
    assert "Rhwf_leak" not in spice.build_netlist(off, off.raw["sim"]["analyses"][0])[0]


# ------------------------------------------------------------------ convergence fallback

def _fake_ngspice(rail_by_case):
    """ngspice stand-in: the OP depends on the temperature and on what the fallback added to the netlist."""
    calls = []

    def run(netlist, workdir=None, name="sim", timeout=0, retry=True):
        temp = float(re.search(r"^\.options temp=(\S+)", netlist, re.M).group(1))
        case = "nodeset" if ".nodeset" in netlist else "plain"
        if "option gminsteps=100" in netlist:
            case = "step"
        calls.append((case, temp, "HWFOP_BEGIN" in netlist))
        rail, fails = rail_by_case(case, temp)
        out = "Warning: source stepping failed\n" if fails else ""
        if "HWFOP_BEGIN" in netlist:
            out += f"HWFOP_BEGIN\nrail = {rail}\nvin = 5.0\nout = 1.0\nHWFOP_END\n"
        else:
            out += "HWF vout 1.0\n"
        return out, 0
    return run, calls


def test_fallback_order_nodeset_then_stepping(tmp_path, monkeypatch):
    d = _design(tmp_path)
    a = d.raw["sim"]["analyses"][0]

    def rail(case, temp):  # 25 C fails plainly and with a nodeset; gmin/source stepping converges
        if temp == 40 or case == "step":
            return 3.3, False
        return 0.3, True

    run, calls = _fake_ngspice(rail)
    monkeypatch.setattr(spice, "run_ngspice", run)
    r = spice.run_analysis(d, a, tmp_path / "w", temp=25)
    fb = r["fallback"]
    assert r["status"] == "ok" and r["rows"][0]["pass"]
    assert fb["used"] == "gmin+source stepping" and [t["rung"] for t in fb["tried"]] == ["none", "nodeset"]
    assert fb["op_check"]["ref_temp"] == 40.0 and fb["op_check"]["rejected_on"] == []
    # the cheap OP probe decides before the analysis is rerun: one analysis rerun only
    assert sum(1 for c in calls if not c[2]) == 2
    # a run at 40 C converges cleanly: no fallback at all
    r40 = spice.run_analysis(d, a, tmp_path / "w", temp=40)
    assert "fallback" not in r40


def test_fallback_with_implausible_op_is_rejected(tmp_path, monkeypatch):
    d = _design(tmp_path)
    a = d.raw["sim"]["analyses"][0]

    def rail(case, temp):  # stepping converges, but to a state with the supply rail off
        if temp == 40:
            return 3.3, False
        return 0.3, case != "step"

    run, _ = _fake_ngspice(rail)
    monkeypatch.setattr(spice, "run_ngspice", run)
    r = spice.run_analysis(d, a, tmp_path / "w", temp=25)
    assert r["status"] == "op-failed" and r["fallback"]["used"] is None
    row = r["rows"][0]
    assert row["value"] is None and not row["pass"] and "not trustworthy" in row["na_reason"]
    assert any("RAIL 0.3 vs 3.3 V" in t["why"] for t in r["fallback"]["tried"])


def test_recovered_singular_matrix_needs_no_fallback(tmp_path, monkeypatch):
    d = _design(tmp_path)
    a = d.raw["sim"]["analyses"][0]
    monkeypatch.setattr(spice, "run_ngspice", lambda *x, **k: (
        "Warning: singular matrix: check node x\nNote: Starting dynamic gmin stepping\nHWF vout 1.0\n", 0))
    r = spice.run_analysis(d, a, tmp_path / "w", temp=25)
    assert r["status"] == "ok" and "fallback" not in r


# ------------------------------------------------------------------ scope

def test_scope_extraction_stops_at_boundary_and_keeps_the_path(tmp_path):
    d = _design(tmp_path)
    a = dict(d.raw["sim"]["analyses"][1], scope={"nets": ["OUT"], "boundary": ["RAIL"]})
    sc = simrobust.extract_scope(d, a)
    assert {"U2", "R1", "C2", "C3", "vsig"} <= sc["keep"] and not {"U1", "R2", "R3", "vin_src"} & sc["keep"]
    assert sc["boundary"] == ["rail"]
    # boundary upstream of the LDO: the LDO is on the path and stays in (its noise is in the result)
    a2 = dict(a, scope={"nets": ["OUT"], "boundary": ["VIN"]})
    sc2 = simrobust.extract_scope(d, a2)
    assert "U1" in sc2["keep"] and "C1" in sc2["keep"] and not {"R2", "R3", "vin_src"} & sc2["keep"]


def test_thevenin_fit_recovers_rl_and_rc():
    import math
    f = [10 ** (1 + i / 10) for i in range(41)]
    zrl = [0.05 + 2j * math.pi * x * 1e-6 for x in f]
    fit = simrobust.fit_z(f, zrl)
    assert fit["model"] == "R+L" and fit["R"] == pytest.approx(0.05, rel=1e-3) and fit["L"] == pytest.approx(1e-6, rel=1e-3)
    zrc = [1 / (1 / 10.0 + 2j * math.pi * x * 1e-6) for x in f]
    fit = simrobust.fit_z(f, zrc)
    assert fit["model"] == "R||C" and fit["R"] == pytest.approx(10, rel=1e-3) and fit["err"] < 1e-6
    lines = simrobust.thevenin_lines("rail", 3.3, fit)
    assert lines[0].startswith("Vhwfth_rail hwfth_rail_s 0 DC 3.3") and "noisy=0" in lines[1] and lines[2].startswith("C")


def _ex(tmp_path):
    proj = tmp_path / "sp"
    shutil.copytree(EX, proj, ignore=shutil.ignore_patterns("build"))
    return Design.load(proj)


def test_scope_validation_gate_passes_on_a_good_boundary(tmp_path):
    d = _ex(tmp_path)
    a = dict(next(x for x in d.raw["sim"]["analyses"] if x["name"] == "noise"),
             scope={"nets": ["AMP_OUT"], "boundary": ["+3V3"]}, temps=[0, 40])
    rs = simrobust.run_scoped(d, a, tmp_path / "w", [0, 40], cache=tmp_path / "c")
    for r in rs:
        s = r["scope"]
        assert s["validated"] and s["validated_temps"] == [0.0, 40.0] and s["kept"] < s["total"]
        assert all(c["ok"] and c["rel"] < 0.02 for c in s["compare"])
        assert s["boundary_model"]["+3V3"]["v_op"] == pytest.approx(3.3, abs=0.01)
        assert r["rows"][0]["pass"]


def test_scope_validation_gate_rejects_a_wrong_boundary(tmp_path):
    """A boundary on the feedback node drops R2's thermal noise (the Thevenin resistor is noiseless): the scoped
    noise misses the full-board one, so the scope is rejected and the full-board value stands."""
    d = _ex(tmp_path)
    a = dict(next(x for x in d.raw["sim"]["analyses"] if x["name"] == "noise"),
             scope={"nets": ["AMP_OUT"], "boundary": ["FB"]})
    r = simrobust.run_scoped(d, a, tmp_path / "w", [None], cache=tmp_path / "c")[0]
    s = r["scope"]
    assert not s["validated"] and "beyond max(2 %" in s["reason"] and "full-board result" in s["note"]
    full = s["compare"][0]["full"]
    assert r["rows"][0]["value"] == full and s["compare"][0]["scoped"] != pytest.approx(full, rel=0.02)


def test_scope_is_rejected_when_the_full_board_never_converges(tmp_path, monkeypatch):
    d = _ex(tmp_path)
    a = dict(next(x for x in d.raw["sim"]["analyses"] if x["name"] == "noise"),
             scope={"nets": ["AMP_OUT"], "boundary": ["+3V3"]})
    real = spice.run_once

    def fake(design, analysis, workdir, nm=None, values=None, temp=None, tag="", cache=None, mod=None):
        r = real(design, analysis, workdir, nm, values, temp, tag, cache, mod)
        if "_full" in tag:  # every full-board run (and its fallbacks) fails
            r = dict(r, status="error", reason="error: no convergence", conv=["Warning: source stepping failed"])
        return r

    monkeypatch.setattr(spice, "run_once", fake)
    r = simrobust.run_scoped(d, a, tmp_path / "w", [None], cache=None)[0]
    assert r["status"] == "scope-rejected" and r["rows"][0]["value"] is None and not r["rows"][0]["pass"]
    assert r["rows"][0]["na_reason"].startswith("scope not validated (the full board converges at no temperature")


def test_scoped_example_build_reports_the_validation(tmp_path):
    from hwflow.cli import main
    proj = tmp_path / "sp"
    shutil.copytree(EX, proj, ignore=shutil.ignore_patterns("build"))
    main(["build", str(proj), "--until", "sim"])
    import json
    st = json.loads((proj / "build" / "status.json").read_text())["sim"]
    assert st["scopes"] and st["scopes"][0]["validated"] and st["passed"] == st["total"]
    rep = (proj / "build" / "REPORT.md").read_text()
    assert "scope noise: VALIDATED" in rep and "out_noise_uv @ 25C: full" in rep


def test_report_hint_names_the_nets_and_suggests_scope():
    c = {"convergence_hints": [{"analysis": "hp_noise @ 40C", "nets": ["AUD_P4V5", "EFUSE_OUT"]}]}
    line = simrobust.report_lines(c)[0]
    assert "AUD_P4V5, EFUSE_OUT" in line and "scope:" in line and "docs/design-yaml.md" in line
