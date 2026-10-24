"""The engine runs without hwharness; the hooks it exposes are the only coupling."""

import json
import shutil
from pathlib import Path

import pytest

from hwflow.core.design import Design
from hwflow.core.libs import Libs
from hwflow.pipeline import hooks, report, stages, status

EX = Path(__file__).resolve().parents[1] / "examples" / "sensor_preamp"


@pytest.fixture
def no_hooks(monkeypatch):
    """An empty registry that does not load entry points: the engine as if hwharness were not installed."""
    monkeypatch.setattr(hooks, "_R", hooks.Registry(loaded=True))
    return hooks._R


def _proj(tmp_path):
    proj = tmp_path / "p"
    shutil.copytree(EX, proj, ignore=shutil.ignore_patterns("build"))
    return proj


def test_engine_without_harness(tmp_path, no_hooks):
    from hwflow.cli import main
    proj = _proj(tmp_path)
    d = Design.load(proj)
    res = stages.stage_check(d, Libs())
    assert res["ok"] and "approval" not in res and not any(f["code"] == "approval" for f in res["findings"])
    status._save_status(d, "check", res)
    text = report.write_report(d, history=True)
    assert "Verdict" in text
    assert "score" not in status._status(d)
    line = json.loads((proj / "build" / "history.jsonl").read_text().splitlines()[-1])
    assert "check_errors" not in line and "verdict" in line
    assert main(["check", str(proj)]) in (0, 1)
    with pytest.raises(SystemExit):  # `approve` and `status` are hwharness commands
        main(["approve", str(proj)])


def test_hooks_run_when_registered(tmp_path, no_hooks):
    hooks.register_check_findings(lambda d: [{"sev": "warn", "code": "x", "msg": "m"}])
    hooks.register_check_state(lambda d, f: {"x": [i for i in f if i["code"] == "x"]})
    hooks.register_report_extra("extra", lambda d: {"a": 1, "b": 2}, ("a",))
    d = Design.load(_proj(tmp_path))
    res = stages.stage_check(d, Libs())
    assert res["warnings"] >= 1 and res["x"] == [{"sev": "warn", "code": "x", "msg": "m"}]
    report.write_report(d, history=True)
    assert status._status(d)["extra"] == {"a": 1, "b": 2}
    line = json.loads((d.dir / "build" / "history.jsonl").read_text().splitlines()[-1])
    assert line["a"] == 1 and "b" not in line


def test_harness_registers_through_the_entry_point(monkeypatch):
    monkeypatch.setattr(hooks, "_R", hooks.Registry())  # not loaded yet: registry() reads the entry points
    reg = hooks.registry()
    assert {"approve", "status"} <= set(reg.commands)
    assert [h.name for h in reg.report_extras] == ["score"]
    assert len(reg.check_findings) == 1 and len(reg.check_state) == 1
