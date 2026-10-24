"""Verification must not promote plans, stale files, or simulation into bench proof."""

import pytest
import yaml

from hwflow.cli import _evidence_hash, _save_status, trace
from hwflow.design import Design


def project(tmp_path, method="test", evidence=None):
    (tmp_path / "design.yaml").write_text("project: trace_case\nboard: {width_mm: 40, height_mm: 30}\n")
    q = {"id": "R1", "verification": method, "text": "Physical test required", "evidence": evidence}
    (tmp_path / "requirements.yaml").write_text(yaml.safe_dump({"requirements": [q]}))
    return Design.load(tmp_path)


def record(d, state="pass", **overrides):
    (d.dir / "result.md").write_text("Measured output at the jack under both-channel load.\n")
    ev = {"status": state, "artifact": "result.md", "design_hash": _evidence_hash(d)} | overrides
    p = d.dir / "requirements.yaml"
    reqs = yaml.safe_load(p.read_text())
    reqs["requirements"][0]["evidence"] = ev
    p.write_text(yaml.safe_dump(reqs))
    return ev


@pytest.mark.parametrize("method", ["test", "inspection", "datasheet", "analysis", "layout"])
@pytest.mark.parametrize("evidence", ["NOT CHECKED: awaiting prototype", "result.md"])
def test_prose_is_not_verification(tmp_path, method, evidence):
    d = project(tmp_path, method, evidence)
    (tmp_path / "result.md").write_text("A plan to perform a check.\n")
    t = trace(d)
    assert t["covered"] == 0 and t["unverified"] == 1


@pytest.mark.parametrize("method", ["test", "inspection", "datasheet", "analysis"])
def test_current_explicit_pass_is_verified(tmp_path, method):
    d = project(tmp_path, method)
    record(d)
    t = trace(d)
    assert t["covered"] == 1 and t["failing"] == 0 and t["unverified"] == 0


def test_explicit_failure_is_not_a_verified_pass(tmp_path):
    d = project(tmp_path)
    record(d, "fail")
    t = trace(d)
    assert t["failing"] == 1 and t["covered"] == 0 and t["unverified"] == 0


@pytest.mark.parametrize("state", ["not_checked", "pending", "", True])
def test_unperformed_or_invalid_status_is_unverified(tmp_path, state):
    d = project(tmp_path)
    record(d, state)
    t = trace(d)
    assert t["unverified"] == 1 and t["covered"] == 0


@pytest.mark.parametrize("problem", ["missing", "empty", "directory", "outside", "stale", "no_hash", "no_artifact"])
def test_invalid_evidence_cannot_verify(tmp_path, problem):
    d = project(tmp_path)
    record(d)
    p = tmp_path / "requirements.yaml"
    reqs = yaml.safe_load(p.read_text())
    ev = reqs["requirements"][0]["evidence"]
    if problem == "missing":
        (tmp_path / "result.md").unlink()
    elif problem == "empty":
        (tmp_path / "result.md").write_text("")
    elif problem == "directory":
        ev["artifact"] = "."
    elif problem == "outside":
        ev["artifact"] = "../external.md"
        (tmp_path.parent / "external.md").write_text("unrelated\n")
    elif problem == "stale":
        d.path.write_text(d.path.read_text().replace("40", "45"))
    elif problem == "no_hash":
        del ev["design_hash"]
    elif problem == "no_artifact":
        del ev["artifact"]
    p.write_text(yaml.safe_dump(reqs))
    t = trace(Design.load(tmp_path))
    assert t["covered"] == 0 and t["unverified"] == 1
    assert t["rows"][0].get("evidence_reason")


def test_simulation_does_not_complete_a_bench_test(tmp_path):
    d = project(tmp_path)
    _save_status(d, "sim", {"analyses": [{"name": "gain", "rows": [
        {"name": "voltage", "req": "R1", "pass": True}]}]})
    t = trace(d)
    assert t["covered"] == 0 and t["unverified"] == 1
    assert t["rows"][0]["measures"] == ["voltage:ok"]


def test_manual_pass_cannot_override_failing_linked_measure(tmp_path):
    d = project(tmp_path)
    record(d)
    _save_status(d, "sim", {"analyses": [{"name": "gain", "rows": [
        {"name": "voltage", "req": "R1", "pass": False}]}]})
    t = trace(d)
    assert t["failing"] == 1 and t["covered"] == 0


def test_simulation_requirement_uses_current_measure(tmp_path):
    d = project(tmp_path, "simulation")
    _save_status(d, "sim", {"analyses": [{"name": "gain", "rows": [
        {"name": "voltage", "req": "R1", "pass": True}]}]})
    assert trace(d)["covered"] == 1
    d.path.write_text(d.path.read_text().replace("40", "45"))
    assert trace(Design.load(tmp_path))["unverified"] == 1


def test_layout_requires_board_checks_not_manual_prose(tmp_path):
    d = project(tmp_path, "layout")
    record(d)
    assert trace(d)["covered"] == 0
    _save_status(d, "layout", {"isolation": [
        {"req": "R1", "rule": "spacing", "mm": 6.5, "limit": 5, "ok": True}]})
    assert trace(d)["covered"] == 1


def test_changed_requirement_text_invalidates_manual_pass(tmp_path):
    d = project(tmp_path)
    record(d)
    p = tmp_path / "requirements.yaml"
    reqs = yaml.safe_load(p.read_text())
    reqs["requirements"][0]["text"] = "Output must now reach 6.0 Vrms, not the previous 4.0 Vrms"
    p.write_text(yaml.safe_dump(reqs))
    t = trace(d)
    assert t["covered"] == 0 and t["unverified"] == 1
