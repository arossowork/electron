"""Sign-off evidence: `auto:` sources in the trace, Monte Carlo yield and firmware host-test checks per requirement,
and the `hwflow evidence` plan and writes."""

import pytest
import yaml

from hwflow.core.design import Design
from hwflow.fw import hosttest
from hwflow.pipeline import evidence
from hwflow.pipeline.status import _save_status, _status
from hwflow.pipeline.trace import STUB_MARK, _evidence_hash, trace

DESIGN = """project: ev_case
board: {width_mm: 40, height_mm: 30}
sim:
  analyses:
    - name: sleep
      cmd: op
      measures:
        - {name: i_sleep, meas: "x", max: 10, req: R1}
"""


def project(tmp_path, reqs, design=DESIGN):
    (tmp_path / "design.yaml").write_text(design)
    (tmp_path / "requirements.yaml").write_text(
        "# requirements\nrequirements:\n" + "".join(f"  - {yaml.safe_dump(q, default_flow_style=True).strip()}"
                                                    f"  # keep me\n" for q in reqs))
    return Design.load(tmp_path)


def sim(d, ok=True, mc_yield=None):
    s = {"analyses": [{"name": "sleep", "rows": [{"name": "i_sleep", "req": "R1", "pass": ok}]}]}
    if mc_yield is not None:
        s["monte_carlo"] = [{"analysis": "sleep", "runs": 100, "yield": mc_yield, "stats": {}}]
        s["mc_runs"] = 100
    _save_status(d, "sim", s)


def row(d, rid="R1"):
    return next(r for r in trace(d)["rows"] if r["id"] == rid)


def test_auto_sim_verifies_a_manual_requirement_only_when_named(tmp_path):
    d = project(tmp_path, [{"id": "R1", "verification": "test", "text": "sleep current", "evidence": {"auto": ["sim"]}}])
    assert row(d)["status"] == "UNVERIFIED" and "auto: no current sim" in row(d)["evidence_reason"]
    sim(d)
    r = row(d)
    assert r["status"] == "pass" and r["auto"] == ["sim"]
    sim(d, ok=False)
    assert row(d)["status"] == "FAIL"


def test_without_auto_the_results_are_offered_not_counted(tmp_path):
    d = project(tmp_path, [{"id": "R1", "verification": "datasheet", "text": "t", "evidence": {"status": "not_checked"}}])
    sim(d)
    r = row(d)
    assert r["status"] == "UNVERIFIED" and "auto: [sim]" in r["evidence_reason"]
    p = evidence.plan(d)
    assert [x["id"] for x in p["computed"]] == ["R1"] and p["computed"][0]["sources"] == ["sim"]


def test_monte_carlo_yield_is_a_measure_of_the_tagged_requirement(tmp_path):
    d = project(tmp_path, [{"id": "R1", "verification": "simulation", "text": "t"}])
    sim(d, mc_yield=0.94)
    r = row(d)
    assert r["status"] == "FAIL" and "mc sleep yield 94/100:FAIL" in r["measures"]
    sim(d, mc_yield=1.0)
    assert row(d)["status"] == "pass"
    d.path.write_text(DESIGN.replace("sim:\n", "sim:\n  mc_min_yield: 0.9\n"))
    d = Design.load(tmp_path)
    sim(d, mc_yield=0.94)
    assert row(d)["status"] == "pass"
    assert evidence.mc_state(d, {"sim": {"monte_carlo": [{"analysis": "sleep", "yield": 0.94}]}})["below"] == []


def test_auto_with_a_record_needs_both(tmp_path):
    d = project(tmp_path, [{"id": "R1", "verification": "datasheet", "text": "t",
                            "evidence": {"auto": ["sim"], "status": "pass", "artifact": "notes/R1.md"}}])
    sim(d)
    assert row(d)["status"] == "UNVERIFIED" and "record next to them" in row(d)["evidence_reason"]
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "R1.md").write_text("R1: crystal 20 ppm, datasheet p. 3\n")
    evidence.set_records(d, "R1=pass:notes/R1.md")
    assert row(Design.load(tmp_path))["status"] == "pass"


def test_cost_source(tmp_path):
    design = DESIGN + "components:\n  R9: {kind: resistor, value: 1k, price_usd: 0.5}\n"
    d = project(tmp_path, [{"id": "R2", "verification": "analysis", "text": "cost",
                            "evidence": {"auto": "cost", "max_usd": 1.0}}], design)
    assert d.cost() == (0.5, [])
    assert row(d, "R2")["status"] == "pass" and row(d, "R2")["measures"] == ["cost BOM 0.50/1 USD:ok"]
    (tmp_path / "requirements.yaml").write_text((tmp_path / "requirements.yaml").read_text()
                                                .replace("max_usd: 1.0", "max_usd: 1.0, extra_usd: 0.6"))
    assert row(Design.load(tmp_path), "R2")["status"] == "FAIL"


TEST_C = r'''
#include "x.h"
void t(void) {
    CHECK(a == 1, "offline schedule after a cold boot (R9)");
    CHECK(b <= 2,
          "average %d uA <= 90 (R5 + R6)", b);
    CHECK(c, "pins %s", "(R50)");
    CHECK(f(x, ")"), "R10: RTC set");
}
'''


def test_host_test_checks_per_requirement(tmp_path):
    f = tmp_path / "test_app.c"
    f.write_text(TEST_C)
    out = f"FAIL {f}:6: average 120 uA <= 90 (R5 + R6)\n4 checks, 1 failed\n"
    got = hosttest.req_checks([f], ["R5", "R6", "R9", "R10", "R1"], out)
    assert got["R9"]["checks"] == 1 and got["R9"]["failed"] == 0
    assert got["R5"]["failed"] == 1 and got["R6"]["failed"] == 1
    assert got["R10"]["checks"] == 1 and "R1" not in got and "R50" not in got
    assert got["R9"]["msgs"] == ["offline schedule after a cold boot (R9)"]


def test_fw_test_result_counts_only_while_the_firmware_is_unchanged(tmp_path):
    d = project(tmp_path, [{"id": "R9", "verification": "test", "text": "offline", "evidence": {"auto": ["fw_test"]}}])
    (tmp_path / "fw" / "test").mkdir(parents=True)
    (tmp_path / "fw" / "test" / "test_app.c").write_text(TEST_C)
    _save_status(d, "fw_test", {"ran": True, "rc": 0, "checks": 4, "failed": 0, "src_hash": hosttest.source_hash(d),
                                "reqs": {"R9": {"checks": 1, "failed": 0}}})
    assert row(d, "R9")["status"] == "pass"
    assert evidence.fw_state(d, _status(d))["state"] == "current"
    (tmp_path / "fw" / "test" / "test_app.c").write_text(TEST_C + "\n")
    r = row(d, "R9")
    assert r["status"] == "UNVERIFIED" and "fw_test" in r["evidence_reason"]


def test_accept_writes_auto_and_keeps_the_file_layout(tmp_path):
    d = project(tmp_path, [{"id": "R1", "verification": "test", "text": "t", "evidence": {"status": "not_checked"}},
                           {"id": "R2", "verification": "analysis", "text": "u"}])
    sim(d)
    before = (tmp_path / "requirements.yaml").read_text()
    assert evidence.accept(d, ["all"]) == {"R1": ["sim"]}
    after = (tmp_path / "requirements.yaml").read_text()
    assert "evidence: {auto: [sim]}" in after and "not_checked" not in after
    assert after.count("# keep me") == 2 and after.splitlines()[0] == "# requirements"
    assert len(after.splitlines()) == len(before.splitlines())
    assert row(Design.load(tmp_path))["status"] == "pass"
    with pytest.raises(SystemExit):
        evidence.accept(Design.load(tmp_path), ["R2"])     # nothing computed names R2


def test_stub_set_and_staleness(tmp_path):
    d = project(tmp_path, [{"id": "R3", "verification": "datasheet", "text": "relay isolation 4 kV"}])
    p = evidence.plan(d)
    assert [x["id"] for x in p["needs_record"]] == ["R3"]
    made = evidence.write_stubs(d)
    assert [f.name for f in made] == ["R3.md"] and STUB_MARK in made[0].read_text()
    with pytest.raises(SystemExit, match="unfilled"):
        evidence.set_records(d, "R3=pass")
    made[0].write_text("# R3\nRelay datasheet p. 2: 4 kV coil-contact.\n")
    evidence.set_records(d, "R3=pass")
    d = Design.load(tmp_path)
    ev = yaml.safe_load((tmp_path / "requirements.yaml").read_text())["requirements"][0]["evidence"]
    assert ev == {"status": "pass", "artifact": "notes/evidence/R3.md", "design_hash": _evidence_hash(d)}
    assert row(d, "R3")["status"] == "datasheet"
    (tmp_path / "other.md").write_text("no id here\n")
    with pytest.raises(SystemExit, match="does not name"):
        evidence.set_records(d, "R3=pass:other.md")
    d.path.write_text(d.path.read_text().replace("40", "45"))
    assert row(Design.load(tmp_path), "R3")["status"] == "UNVERIFIED"


def test_should_requirements_do_not_fail_the_verdict(tmp_path):
    from hwflow.pipeline.report import write_report
    d = project(tmp_path, [{"id": "R1", "priority": "must", "verification": "analysis", "text": "t"},
                           {"id": "R2", "priority": "should", "verification": "analysis", "text": "u"}])
    assert "requirements not all verified" in write_report(d)
    (tmp_path / "calc.md").write_text("R1: 3 x 2 = 6 <= 7\n")
    evidence.set_records(d, "R1=pass:calc.md")
    text = write_report(Design.load(tmp_path))
    assert "requirements not all verified" not in text and "should/could requirements unverified" in text
