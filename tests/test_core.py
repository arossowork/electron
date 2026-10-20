import math
from pathlib import Path

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
    # backlog 47 (a): the lumped transient network gives the same measures as the full R and C network
    from hwflow import spice
    a = next(x for x in d.raw["sim"]["analyses"] if x["name"] == "line_transient")
    lumped = spice.run_analysis(d, a, tmp_path / "l", postlayout.PostLayoutNodeMap(d, ex, lump=True))
    full = spice.run_analysis(d, a, tmp_path / "f", postlayout.PostLayoutNodeMap(d, ex, lump=False))
    assert lumped["status"] == full["status"] == "ok"
    for r1, r2 in zip(lumped["rows"], full["rows"]):
        assert r1["value"] == pytest.approx(r2["value"], rel=0.01, abs=1e-6), r1["name"]


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


def test_design_hash_ignores_comments_and_prose(tmp_path):
    """Backlog 58: comments, whitespace and requirement prose do not make results stale; values do."""
    from hwflow.cli import _design_hash
    proj = _copy_example(tmp_path)
    (proj / "requirements.yaml").write_text("requirements:\n  - {id: R1, text: a, evidence: x, verification: simulation}\n")
    d = Design.load(proj)
    h0 = _design_hash(d)
    d.raw.setdefault("board", {})["width_mm"] = 999          # in-memory layout resize is not a design change
    assert _design_hash(d) == h0
    y = proj / "design.yaml"
    y.write_text("# a new comment\n" + y.read_text().replace("components:", "components:   # parts", 1) + "\n\n")
    (proj / "requirements.yaml").write_text("requirements:\n  - {id: R1, text: b, evidence: y z, verification: simulation}\n")
    assert _design_hash(Design.load(proj)) == h0
    (proj / "requirements.yaml").write_text("requirements:\n  - {id: R1, text: b, evidence: y z, verification: test}\n")
    assert _design_hash(Design.load(proj)) != h0
    y.write_text(y.read_text().replace("value: 10k", "value: 12k", 1))
    assert "12k" in y.read_text() and _design_hash(Design.load(proj)) != h0


def test_stale_results_stay_in_report_and_history_is_kept(tmp_path, monkeypatch):
    """Backlog 43: a stale stage keeps its last result, marked, and the verdict says stale.
    Backlog 18: every build appends to build/history.jsonl and keeps the report in build/history/."""
    import json

    from hwflow import cli
    from hwflow.libs import Libs
    proj = _copy_example(tmp_path)
    (proj / "requirements.yaml").unlink(missing_ok=True)
    d = Design.load(proj)
    cli._save_status(d, "check", cli.stage_check(d, Libs()))
    text = cli.write_report(d, history=True)
    assert "Verdict: PASS" in text and "(stale)" not in text
    y = proj / "design.yaml"
    y.write_text(y.read_text().replace("value: 10k", "value: 12k", 1))
    d = Design.load(proj)
    monkeypatch.setattr(cli, "HISTORY_KEEP", 1)
    text = cli.write_report(d, history=True)
    assert "- check (stale): OK" in text and "Verdict: FAIL: stale: check" in text
    assert cli.score(d)["check_errors"] is None                      # score stays fresh-only
    lines = [json.loads(x) for x in (proj / "build" / "history.jsonl").read_text().splitlines()]
    assert [x["verdict"] for x in lines] == ["PASS", "FAIL: stale: check"]
    kept = list((proj / "build" / "history").glob("*.md"))
    assert len(kept) == 1 and "(stale)" in kept[0].read_text()


def test_trace_flags_missing_evidence_files(tmp_path):
    """Backlog 54: file paths cited in evidence must exist and not be empty; URLs and words are ignored."""
    from hwflow.cli import missing_evidence, trace
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "calc.py").write_text("x = 1\n")
    (tmp_path / "SUMMARY.md").write_text("")
    ev = ("notes/calc.py and SUMMARY.md, notes/compliance.md; https://x.com/ds.pdf; IEC 61000-4-2; 27.81 USD; "
          "architecture.md section 4")
    assert missing_evidence(tmp_path, ev) == ["SUMMARY.md", "notes/compliance.md", "architecture.md"]
    proj = _copy_example(tmp_path)
    (proj / "requirements.yaml").write_text(
        "requirements:\n  - {id: R1, verification: inspection, evidence: 'see notes/missing.md'}\n")
    t = trace(Design.load(proj))
    assert t["evidence_missing"] == ["R1"] and t["rows"][0]["evidence_missing"] == ["notes/missing.md"]


def test_check_fails_on_mos_model_under_q_reference(tmp_path):
    """Backlog 17: Q with a MOS model (or M with a BJT model) is a check error, not an ngspice abort."""
    proj = _copy_example(tmp_path)
    (proj / "models").mkdir()
    (proj / "models" / "p.lib").write_text(".model MYP VDMOS(pchan Vto=-1 Kp=1)\n.model MYN nmos(LEVEL=1)\n")
    d = Design.load(proj)
    base = {"symbol": "Device:Q_PMOS_GSD", "footprint": "Package_TO_SOT_SMD:SOT-23", "value": "x",
            "pins": {"1": "GND", "2": "+3V3", "3": "FB"}}
    d.components["Q2"] = type(d.components["R1"])("Q2", dict(base, spice={"model": "PMOS_LOGIC", "ports": [3, 1, 2]}))
    d.components["Q3"] = type(d.components["R1"])("Q3", dict(base, spice={"model": "MYP", "lib": "models/p.lib"}))
    d.components["M4"] = type(d.components["R1"])("M4", dict(base, spice={"model": "NPN_GENERIC"}))
    d.components["M5"] = type(d.components["R1"])("M5", dict(base, spice={"model": "MYN", "lib": "models/p.lib"}))
    d.components["Q6"] = type(d.components["R1"])("Q6", dict(base, spice={"model": "NPN_GENERIC"}))
    errs = {f["msg"].split()[0]: f["msg"] for f in check(d) if f["code"] == "spice-element"}
    assert sorted(errs) == ["M4", "Q2", "Q3"]
    assert "PMOS model PMOS_LOGIC" in errs["Q2"] and "rename it M2" in errs["Q2"]


def test_check_compares_package_with_footprint():
    """Backlog 56: a SOD-882 part on a chip 0402 land pattern is flagged; pad count and pitch are compared."""
    from hwflow.design import Component, package_findings
    from hwflow.libs import Libs
    libs = Libs()
    chip = libs.footprint("Diode_SMD:D_0402_1005Metric")
    c = Component("D4", {"footprint": "Diode_SMD:D_0402_1005Metric", "package": "SOD-882", "package_pads": 2,
                         "package_pitch_mm": 0.65})
    f = package_findings(c, chip)
    assert [s for s, _ in f] == ["warn", "error"] and "pitch 0.97 mm" in f[1][1]
    ok = Component("D4", {"footprint": "Diode_SMD:D_SOD-882", "package": "DFN1006-2", "package_pads": 2})
    assert package_findings(ok, libs.footprint("Diode_SMD:D_SOD-882")) == []
    sot = Component("U1", {"footprint": "Package_TO_SOT_SMD:SOT-23-5", "package": "SOT-23-5", "package_pads": 6})
    assert [s for s, _ in package_findings(sot, libs.footprint("Package_TO_SOT_SMD:SOT-23-5"))] == ["error"]


def test_measure_on_missing_node_says_so(tmp_path):
    """Backlog 46: a measure on a node that is not in the netlist fails with the ngspice reason, not a silent n/a."""
    from hwflow import spice
    d = Design.load(EX)
    a = dict(next(x for x in d.raw["sim"]["analyses"] if x["name"] == "dc_op"))
    a["measures"] = a["measures"] + [{"name": "ghost", "expr": "v(x_bt1_2,x_bt1_1)"}]
    r = spice.run_analysis(d, a, tmp_path)
    ghost = next(x for x in r["rows"] if x["name"] == "ghost")
    assert r["status"] == "error" and not ghost["pass"] and "node not in the netlist: x_bt1_2" in ghost["na_reason"]


def test_build_skips_unchanged_stages_and_brief_prints_changes(tmp_path, capsys):
    """Backlog 27: a stage that passed with the same inputs is skipped. Backlog 8: --brief prints only changes."""
    from hwflow.cli import main
    proj = _copy_example(tmp_path)
    main(["build", str(proj), "--until", "sim"])
    capsys.readouterr()
    main(["build", str(proj), "--until", "sim", "--brief"])
    out = capsys.readouterr().out
    assert out.count("skipped: passed last time with the same inputs") == 3
    assert "no change since the last report" in out and "- SPICE" not in out
    y = proj / "design.yaml"
    y.write_text(y.read_text().replace("value: 10k", "value: 12k", 1))
    main(["build", str(proj), "--until", "sim", "--brief"])
    out = capsys.readouterr().out
    assert "skipped" not in out and "changed since the last report" in out
    main(["build", str(proj), "--until", "sim", "--rebuild"])
    assert "skipped" not in capsys.readouterr().out


def test_monte_carlo_runs_once_per_design(tmp_path, capsys):
    from hwflow.cli import main
    proj = _copy_example(tmp_path)
    main(["sim", str(proj), "--mc", "4"])
    assert "reused" not in capsys.readouterr().out
    main(["sim", str(proj), "--mc", "4"])
    assert "Monte Carlo 4 runs reused" in capsys.readouterr().out
    main(["sim", str(proj), "--mc", "5"])
    assert "reused" not in capsys.readouterr().out


def test_router_retries_a_class_in_another_net_order(tmp_path):
    """Backlog 40: a width class that leaves a net unrouted is routed again in another order; the best is kept."""
    import json
    from hwflow.pcb import route_class
    calls = []

    def run(args):
        calls.append(args)
        out = Path(args[args.index("--nets") - 1])
        out.write_text("board")
        failed = 1 if len(calls) < 3 else 0
        return f"JSON_SUMMARY_MIN:{json.dumps({'failed': failed})}\n", 0

    best, tried, _ = route_class(["py", "route.py", "in.kicad_pcb", None, "--nets", "A", "B"], tmp_path / "x_krt0", 3, run)
    assert len(calls) == 3 and best[2] == 0 and best[0].name == "x_krt0_r2.kicad_pcb"
    assert calls[1][-2:] == ["--ordering", "inside_out"] and tried[0] == ("default order", "1 unrouted")
    calls.clear()
    best, tried, _ = route_class(["py", "r", "in", None, "--nets", "A"], tmp_path / "y", 0, run)
    assert len(calls) == 1 and best[2] == 1                      # router_retries: 0 keeps one attempt


def test_layout_auto_retries_placement_and_keeps_the_clean_board(tmp_path, monkeypatch):
    """Backlog 23/25: one call places, routes, checks DRC and retries with new placer seeds until the board is clean."""
    from hwflow import cli
    proj = _copy_example(tmp_path)
    d = Design.load(proj)
    seen = []

    def fake(d, libs, reuse=False, seeds=4, iters=0, passes=0, seed0=1):
        seen.append((reuse, seed0))
        n = len(seen)
        (cli._out(d) / f"{d.name}.kicad_pcb").write_text(f"board {n}")
        (cli._out(d) / "placement.json").write_text("{}")
        bad = n < 3
        return {"ok": not bad, "board_mm": [50, 40], "unrouted": 0, "drc_errors": 2 if bad else 0, "drc_warnings": 0,
                "placement_violations": [], "vias": 10, "segments": 5, "router": "", "drc": []}

    monkeypatch.setattr(cli, "stage_layout", fake)
    (cli._out(d) / "placement.json").write_text("{}")
    r = cli.layout_auto(d, None, reuse=True, attempts=4, seeds=4)
    assert seen == [(True, 1), (False, 5), (False, 9)]
    assert r["ok"] and r["auto"]["kept"] == 3 and len(r["auto"]["attempts"]) == 3
    assert (cli._out(d) / f"{d.name}.kicad_pcb").read_text() == "board 3"
    assert "kept attempt 3 of 3" in cli._auto_summary(r)


def test_post_is_stale_when_layout_ran_after_it(tmp_path):
    """Backlog 43: a post-layout result from an older board is marked stale even if design.yaml did not change."""
    import time

    from hwflow import cli
    proj = _copy_example(tmp_path)
    (proj / "requirements.yaml").unlink(missing_ok=True)
    d = Design.load(proj)
    post = {"ok": True, "checks": [], "ir_drop": [], "deltas": [], "regressions": [],
            "metrics": {"area_mm2": 1, "layers": 2, "track_len_mm": 1, "vias": 0}}
    cli._save_status(d, "post", post)
    assert cli._stale_stages(cli._status(d), cli._design_hash(d)) == []
    time.sleep(0.01)
    cli._save_status(d, "layout", {"ok": True, "board_mm": [50, 40], "router": "", "segments": 0, "vias": 0,
                                   "drc_errors": 0, "drc_warnings": 0, "unrouted": 0, "drc": [],
                                   "placement_violations": []})
    assert cli._stale_stages(cli._status(d), cli._design_hash(d)) == ["post"]
    text = cli.write_report(d)
    assert "- post-layout (stale)" in text and "stale: post" in text and "post_pass_ratio" in cli.score(d)
    assert cli.score(d)["post_pass_ratio"] is None
