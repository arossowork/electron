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


def test_monte_carlo_parallel_is_deterministic(tmp_path):
    from hwflow import spice
    d = Design.load(EX)
    a = spice.monte_carlo(d, tmp_path / "a", runs=12, workers=1)
    b = spice.monte_carlo(d, tmp_path / "b", runs=12, workers=6)
    assert a == b


def test_custom_pad_exports_full_outline():
    """SOT-89 tab is a custom pad: the router must see the whole tab, not just the anchor rectangle."""
    from hwflow.libs import Libs
    from hwflow.pcb import _padstack
    pad = next(p for p in Libs().footprint("Package_TO_SOT_SMD:SOT-89-3")["pads"] if p["number"] == "2")
    _, ps = _padstack(pad, ["F.Cu", "B.Cu"])
    shape = ps[2][1]
    assert shape[0] == "polygon"
    xs = shape[3::2]
    assert max(xs) - min(xs) > 4000  # um: anchor 1.475 mm plus the 3.1 mm tab


def test_post_layout_inductors_are_damped(tmp_path):
    import json
    import shutil
    from hwflow.cli import main
    from hwflow import postlayout
    proj = tmp_path / "p"
    shutil.copytree(EX, proj, ignore=shutil.ignore_patterns("build"))
    main(["build", str(proj), "--until", "layout"])
    d = Design.load(proj)
    d.raw["board"]["width_mm"], d.raw["board"]["height_mm"] = json.loads(
        (proj / "build" / "status.json").read_text())["layout"]["board_mm"]
    ex = postlayout.Extraction(d, proj / "build" / f"{d.name}.kicad_pcb")
    lines = postlayout.PostLayoutNodeMap(d, ex).extra()
    assert sum(ln.startswith("Lpx") for ln in lines) == sum(ln.startswith("Rdx") for ln in lines) > 0


def test_run_all_parallel_matches_serial(tmp_path):
    from hwflow import spice
    d = Design.load(EX)
    a = spice.run_all(d, tmp_path / "a", workers=1)
    b = spice.run_all(d, tmp_path / "b", workers=6)
    assert [(r["name"], [x["value"] for x in r["rows"]]) for r in a] == \
           [(r["name"], [x["value"] for x in r["rows"]]) for r in b]


def test_thermal_vias_land_inside_their_pad():
    from hwflow.libs import Libs
    from hwflow.pcb import thermal_vias
    from hwflow.placer import build_parts
    d = Design.load(EX)
    d.components["U1"].data["footprint"] = "Package_TO_SOT_SMD:SOT-89-3"
    d.components["U1"].data["pins"] = {"1": "GND", "2": "VBUS", "3": "+3V3"}
    d.components["U1"].data["thermal_vias"] = {"pad": "2", "count": 4}
    libs = Libs()
    parts = build_parts(d, libs)
    parts["U1"].x, parts["U1"].y, parts["U1"].r = 10.0, 10.0, 90.0
    vias = thermal_vias(d, libs, parts)
    assert len(vias) == 4 and all(v["net"] == "VBUS" for v in vias)
    # the tab spans about 4.6 x 1.7 mm; rotated 90 deg it is tall in y around x = 10
    assert all(abs(v["x"] - 10.0) < 1.0 and abs(v["y"] - 10.0) < 3.0 for v in vias)


def test_save_keeps_comments(tmp_path):
    import shutil
    proj = tmp_path / "p"
    shutil.copytree(EX, proj, ignore=shutil.ignore_patterns("build"))
    text = (proj / "design.yaml").read_text().replace("components:", "# the parts list\ncomponents:", 1)
    (proj / "design.yaml").write_text(text)
    d = Design.load(proj)
    d.components["R1"].data["value"] = "91k"
    d.save()
    out = (proj / "design.yaml").read_text()
    assert "# the parts list" in out and "91k" in out
    assert Design.load(proj).components["R1"].get("value") == "91k"


def test_recoverable_singular_matrix_warning_is_not_an_abort(tmp_path, monkeypatch):
    from hwflow import spice
    d = Design.load(EX)
    a = next(x for x in d.raw["sim"]["analyses"] if x["name"] == "dc_op")
    out = "Warning: singular matrix: check node x\nNote: Starting dynamic gmin stepping\n" + "".join(
        f"HWF {m['name']} {((m.get('min') or 0) + (m.get('max') or 0)) / 2 / float(m.get('scale', 1))}\n"
        for m in a["measures"])
    monkeypatch.setattr(spice, "run_ngspice", lambda *args, **kw: (out, 0))
    r = spice.run_analysis(d, a, tmp_path)
    assert not r["aborted"] and all(row["pass"] for row in r["rows"])


def _fake_out(a, extra=""):
    return extra + "".join(f"HWF {m['name']} {((m.get('min') or 0) + (m.get('max') or 0)) / 2 / float(m.get('scale', 1))}\n"
                           for m in a["measures"])


def test_measure_out_of_interval_is_a_warning_when_rows_pass(tmp_path, monkeypatch):
    """Backlog 20: 'Error: measure ... out of interval' with every measure passing is not a FAIL."""
    from hwflow import spice
    from hwflow.cli import _sim_verdict
    d = Design.load(EX)
    a = next(x for x in d.raw["sim"]["analyses"] if x["name"] == "dc_op")
    msg = "Error: measure  probe  find(AT) : out of interval\n"
    monkeypatch.setattr(spice, "run_ngspice", lambda *args, **kw: (_fake_out(a, msg), 0))
    r = spice.run_analysis(d, a, tmp_path)
    assert r["errors"] == [] and len(r["warnings"]) == 1 and all(x["pass"] for x in r["rows"])
    # a failing measure keeps the message as an error, and the verdict names what failed
    bad = msg + "".join(f"HWF {m['name']} 1e9\n" for m in a["measures"])
    monkeypatch.setattr(spice, "run_ngspice", lambda *args, **kw: (bad, 0))
    r = spice.run_analysis(d, a, tmp_path)
    assert r["errors"] and not r["warnings"]
    c = {"total": 3, "passed": 3, "fails": [], "errors": ["tran1: Error: bad thing"]}
    assert _sim_verdict(c) == "ngspice errors in tran1 (Error: bad thing)"
    c = {"total": 3, "passed": 2, "fails": [{"analysis": "dc_op", "name": "v3v3"}], "errors": []}
    assert _sim_verdict(c) == "1 sim measures fail (dc_op/v3v3)"


def _copy_example(tmp_path):
    import shutil
    proj = tmp_path / "p"
    shutil.copytree(EX, proj, ignore=shutil.ignore_patterns("build"))
    return proj


def test_debug_measures_do_not_count(tmp_path):
    """Backlog 19: a failing dbg_* probe is listed apart and leaves pass/total, verdict and trace alone."""
    import yaml

    from hwflow.cli import stage_sim, trace, _save_status
    proj = _copy_example(tmp_path)
    y = yaml.safe_load((proj / "design.yaml").read_text())
    a = next(x for x in y["sim"]["analyses"] if x["name"] == "dc_op")
    a["measures"] += [{"name": "dbg_v3v3", "expr": "v(+3V3)", "max": -1, "req": "R1"},
                      {"name": "probe2", "expr": "v(+3V3)", "max": -1, "debug": True}]
    (proj / "design.yaml").write_text(yaml.safe_dump(y, sort_keys=False))
    d = Design.load(proj)
    r = stage_sim(d)
    assert r["ok"] and r["passed"] == r["total"]
    assert sorted(x["name"] for x in r["debug"]) == ["dbg_v3v3", "probe2"]
    _save_status(d, "sim", r)
    assert all("dbg_v3v3" not in m for row in trace(d)["rows"] for m in row["measures"])


def test_analysis_timeout_is_reported(tmp_path):
    """Backlog 22/42: per-analysis timeout_s; the analysis is marked timeout and its rows say why they are n/a."""
    from hwflow import spice
    d = Design.load(EX)
    a = dict(next(x for x in d.raw["sim"]["analyses"] if x["name"] == "line_transient"), timeout_s=0.001)
    r = spice.run_analysis(d, a, tmp_path)
    assert r["status"] == "timeout" and r["seconds"] < 5
    assert all(row["value"] is None and "timeout after 0.001 s" in row["na_reason"] for row in r["rows"])
    ok = spice.run_analysis(d, next(x for x in d.raw["sim"]["analyses"] if x["name"] == "dc_op"), tmp_path)
    assert ok["status"] == "ok" and "reason" not in ok


def test_ngspice_crash_is_retried_once(tmp_path, monkeypatch):
    """Backlog 41: a signal exit is retried once and the run is marked flaky, not reported as a design result."""
    import subprocess
    from types import SimpleNamespace
    from hwflow import spice
    d = Design.load(EX)
    a = next(x for x in d.raw["sim"]["analyses"] if x["name"] == "dc_op")
    calls = []

    def fake_run(*args, **kw):
        calls.append(1)
        if len(calls) == 1:
            return SimpleNamespace(stdout="", stderr="free(): invalid next size (normal)", returncode=-6)
        return SimpleNamespace(stdout=_fake_out(a), stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = spice.run_analysis(d, a, tmp_path)
    assert len(calls) == 2 and r["status"] == "ok" and "free(): invalid" in r["flaky"]
    assert all(x["pass"] for x in r["rows"])
    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kw: SimpleNamespace(stdout="", stderr="", returncode=-11))
    r = spice.run_analysis(d, a, tmp_path)
    assert r["status"] == "crash" and all(x["na_reason"].startswith("ngspice crashed") for x in r["rows"])


def test_report_names_the_analysis_that_timed_out(tmp_path):
    from hwflow.cli import _save_status, stage_sim, write_report
    import yaml
    proj = _copy_example(tmp_path)
    y = yaml.safe_load((proj / "design.yaml").read_text())
    next(x for x in y["sim"]["analyses"] if x["name"] == "line_transient")["timeout_s"] = 0.001
    (proj / "design.yaml").write_text(yaml.safe_dump(y, sort_keys=False))
    d = Design.load(proj)
    _save_status(d, "sim", stage_sim(d))
    text = write_report(d)
    assert "analysis line_transient: timeout after" in text and "n/a: timeout after 0.001 s" in text


def test_monte_carlo_keeps_failing_runs(tmp_path, monkeypatch):
    """Backlog 55: a failing run is saved with seed, values and log; an aborted run is not in the spread."""
    import json
    from hwflow import spice
    d = Design.load(EX)
    real = spice.run_analysis

    def fake(design, analysis, workdir, nm=None, values=None, temp=None, tag=""):
        r = real(design, analysis, workdir, nm, values, temp, tag)
        if tag == "_mc3":
            for row in r["rows"]:
                row.update(value=2.198e6, **{"pass": False})
            r.update(status="aborted", reason="aborted: timestep too small")
        return r

    monkeypatch.setattr(spice, "run_analysis", fake)
    res = spice.monte_carlo(d, tmp_path / "sim", runs=6, workers=2)
    for m in res:
        assert m["yield"] == 5 / 6 and [f["run"] for f in m["failures"]] == [3]
        assert all(s["max"] < 1e6 for s in m["stats"].values())     # the aborted run is not in the spread
        rec = json.loads((tmp_path / m["failures"][0]["file"]).read_text())
        assert rec["seed"] == 1 and rec["run"] == 3 and rec["values"] and rec["status"] == "aborted"
        assert rec["log"]                                           # the ngspice log of that run
