import math

import pytest

from hwflow import sexpr
from hwflow.design import Design, check, parse_value, snap_e
from hwflow.pcb import ipc2221_width_mm
from hwflow.postlayout import microstrip

EX = "examples/sensor_preamp"


@pytest.mark.parametrize("s,v", [("4k7", 4700), ("100n", 1e-7), ("2.2meg", 2.2e6), ("1M", 1e6), ("10", 10),
                                 ("4R7", 4.7), ("90.9k", 90900), ("1uF", 1e-6), (3.3, 3.3)])
def test_parse_value(s, v):
    assert math.isclose(parse_value(s), v, rel_tol=1e-9)


def test_snap_e():
    assert math.isclose(snap_e(4800, "E24"), 4700)
    assert math.isclose(snap_e(9.4e-9, "E12"), 10e-9)


def test_sexpr_roundtrip():
    t = sexpr.loads('(a (b "x y" 1.5) (c d))')
    assert sexpr.loads(sexpr.dumps(t)) == t
    assert sexpr.find(t, "b")[1] == "x y"


def test_ipc2221_one_amp():
    # 1 A, 1 oz, 10 C rise, external layer: about 0.3 mm (12 mil)
    assert 0.25 < ipc2221_width_mm(1.0) < 0.35


def test_microstrip_50_ohm():
    z0, ee = microstrip(2.9, 1.6, 0.035, 4.3)
    assert 47 < z0 < 53 and 3.0 < ee < 3.6


def test_design_check_clean():
    from hwflow.libs import Libs
    d = Design.load(EX)
    assert [f for f in check(d, Libs()) if f["sev"] == "error"] == []


def test_spice_ldo_and_gain(tmp_path):
    from hwflow import spice
    d = Design.load(EX)
    res = {r["name"]: r for a in spice.run_all(d, tmp_path) for r in a["rows"]}
    assert abs(res["v3v3"]["value"] - 3.30) < 0.01
    assert abs(res["gain_db"]["value"] - 20.0) < 0.2


def test_optimizer_keeps_constraints(tmp_path):
    import shutil

    import yaml

    from hwflow import optimize
    proj = tmp_path / "p"
    shutil.copytree(EX, proj, ignore=shutil.ignore_patterns("build"))
    d = yaml.safe_load((proj / "design.yaml").read_text())
    for a in d["sim"]["analyses"]:
        a["measures"] = [m for m in a["measures"] if m["name"] not in ("vout_dc", "sig_pp")]
    d["optimize"] = {"budget": 16, "variables": [{"ref": "C4", "min": "100p", "max": "10n", "series": "E12"}],
                     "objectives": [{"measure": "out_noise_uv", "weight": 1}]}
    (proj / "design.yaml").write_text(yaml.safe_dump(d, sort_keys=False))
    res = optimize.run(Design.load(proj), tmp_path, log=lambda *_: None)
    assert res["fails"] == 0
    assert res["measures"]["out_noise_uv"] <= res["baseline"]["out_noise_uv"]
    assert 900 <= res["measures"]["f3db"] <= 1500


def test_aborted_transient_fails_measures(tmp_path, monkeypatch):
    """A transient that stops before its stop time must not pass on the truncated waveform."""
    from hwflow import spice
    d = Design.load(EX)
    a = next(x for x in d.raw["sim"]["analyses"] if x["name"] == "line_transient")
    fake = "doAnalyses: TRAN:  Timestep too small\nHWF __tend 0.001\n" + "".join(
        f"HWF {m['name']} {((m.get('min') or 0) + (m.get('max') or 0)) / 2 / float(m.get('scale', 1))}\n"
        for m in a["measures"])
    monkeypatch.setattr(spice, "run_ngspice", lambda *args, **kw: (fake, 1))
    r = spice.run_analysis(d, a, tmp_path)
    assert r["aborted"]
    assert r["rows"] and not any(row["pass"] for row in r["rows"])
    # same values with a complete run pass
    full = fake.replace("Timestep too small", "ok").replace("__tend 0.001", "__tend 0.04")
    monkeypatch.setattr(spice, "run_ngspice", lambda *args, **kw: (full, 0))
    r = spice.run_analysis(d, a, tmp_path)
    assert not r["aborted"] and all(row["pass"] for row in r["rows"])


def test_cpwg_matches_openems_reference():
    """Trace 0.25 mm wide, 0.2 mm from a same-layer pour on 1.6 mm FR-4: openEMS TDR gave 86.5 ohm."""
    from hwflow.postlayout import cpwg_z0, microstrip
    z, _ = cpwg_z0(0.25, 0.2, 1.6, 4.3)
    assert abs(z - 86.5) / 86.5 < 0.05
    assert microstrip(0.25, 1.6, 0.035, 4.3)[0] > 120  # the pour-free formula overestimates here
