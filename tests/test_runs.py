"""hwflow.runs (ADR 0001, section 3): UUIDv7 ids, the run and stage state machines, the hwflow.run/1 manifest
(validated against docs/schemas/run-1.json), FileRunStore, the build/ projection, and two concurrent builds of one
project."""
import json
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

from hwflow.artifacts import LocalCAS
from hwflow.core import settings as settings_mod
from hwflow.ids import uuid7
from hwflow.runs import (STAGE_TERMINAL, TERMINAL, TRANSITIONS, DesignVersionRef, FileRunStore, InvalidTransition, Run,
                         RunNotFound, RunStatus, StageRecord, StageStatus, allowed_from, checkout, run_from_manifest,
                         run_to_manifest)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "docs" / "schemas" / "run-1.json").read_text())
EXAMPLE = ROOT / "examples" / "sensor_preamp"
S = settings_mod.get_settings()
HAVE_TOOLS = bool(S.tools.kicad_cli) and bool(S.paths.kicad_share) and shutil.which(S.tools.ngspice) is not None
STATES = [s.value for s in RunStatus]


def _run(cas, rid: str | None = None, project="p") -> Run:
    d = cas.put_bytes(b"project: p\n", name="design.yaml")
    t = cas.put_bytes(b'{"files":{},"schema":"hwflow.tree/1"}', media_type="application/vnd.hwflow.tree+json")
    return Run(run_id=rid or str(uuid7()), kind="build", tenant_id=None, project_id=project,
               design_version=DesignVersionRef(t.sha256, t.sha256, d, None, t), status=RunStatus.queued, verdict=None,
               options={"until": "sim"}, options_hash="0" * 12, settings={"layout": {}}, code={"hwflow": "0"},
               tools={"ngspice": "x"}, stages=[], artifacts={}, created_at=datetime.now(timezone.utc))


def test_uuid7_is_time_ordered_v7():
    ids = [uuid7() for _ in range(2000)]
    assert all(u.version == 7 and u.variant == uuid.RFC_4122 for u in ids)
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert [str(u) for u in ids] == sorted(str(u) for u in ids)  # string order is time order (LATEST, list)


@pytest.mark.parametrize("frm", STATES)
@pytest.mark.parametrize("to", STATES)
def test_every_transition_follows_the_table(tmp_path, frm, to):
    """All 36 (from, to) pairs, as hwplatform's PgRunStore tests them: the same table decides both stores."""
    cas = LocalCAS(tmp_path / "cas")
    rs = FileRunStore(tmp_path / "build" / "runs")
    run = rs.create(_run(cas))
    path = {"queued": [], "running": ["running"], "waiting": ["running", "waiting"], "succeeded": ["running", "succeeded"],
            "failed": ["failed"], "cancelled": ["cancelled"]}[frm]
    for st in path:
        rs.transition(run.run_id, RunStatus(st))
    ok = to in TRANSITIONS.get(frm, set())
    assert ok == (frm in allowed_from(to))
    if ok:
        assert rs.transition(run.run_id, RunStatus(to)).status.value == to
    else:
        with pytest.raises(InvalidTransition):
            rs.transition(run.run_id, RunStatus(to))
        assert rs.get(run.run_id).status.value == frm
    assert not TRANSITIONS.keys() & TERMINAL


def test_run_lifecycle_events_and_manifest(tmp_path):
    cas = LocalCAS(tmp_path / "cas")
    rs = FileRunStore(tmp_path / "build" / "runs")
    run = rs.create(_run(cas))
    rid = run.run_id
    assert rs.create(run).run_id == rid  # idempotent
    rs.transition(rid, RunStatus.running)
    res = cas.put_bytes(b'{"ok": true}', media_type="application/json", name="check.result.json")
    rs.upsert_stage(rid, StageRecord("check", "check", 1, StageStatus.running))
    done = StageRecord("check", "check", 1, StageStatus.succeeded, verdict="pass", cache_key="a" * 64, seconds=1.0,
                       started_at=datetime.now(timezone.utc), ended_at=datetime.now(timezone.utc), result=res)
    rs.upsert_stage(rid, done)
    rs.upsert_stage(rid, done)  # idempotent: no second event
    rs.upsert_stage(rid, StageRecord("check", "check", 1, StageStatus.running))  # a late replay is ignored
    rs.upsert_stage(rid, StageRecord("sim", "sim", 2, StageStatus.skipped, summary={"reason": "hard_failure:check"}))
    rs.set_artifacts(rid, {"REPORT.md": cas.put_bytes(b"# r\n", media_type="text/markdown")})
    with pytest.raises(ValueError):
        rs.transition(rid, RunStatus.succeeded, verdict="MAYBE")
    final = rs.transition(rid, RunStatus.succeeded, verdict="PASS")
    assert final.started_at and final.ended_at and final.verdict == "PASS"
    assert [s.status for s in final.stages] == [StageStatus.succeeded, StageStatus.skipped]
    ev = rs.events(rid)
    assert [e["seq"] for e in ev] == list(range(1, len(ev) + 1))
    assert [e["type"] for e in ev] == ["run.queued", "run.started", "stage.started", "stage.completed",
                                       "stage.skipped", "artifact.created", "run.completed"]
    assert all(e["specversion"] == "1.0" and e["runid"] == rid and e["dataschema"].startswith("hwflow.events/")
               for e in ev)
    m = json.loads((rs.run_dir(rid) / "manifest.json").read_text())
    jsonschema.validate(m, SCHEMA)
    assert m == run_to_manifest(final)
    assert run_to_manifest(run_from_manifest(m)) == m
    assert rs.list("p")[0].run_id == rid and rs.list("other") == []
    with pytest.raises(RunNotFound):
        rs.get(str(uuid7()))
    assert rs.latest() == rid
    assert STAGE_TERMINAL == {"succeeded", "failed", "skipped", "cancelled"}


def test_project_state_older_run_never_overwrites(tmp_path):
    cas = LocalCAS(tmp_path / "cas")
    rs = FileRunStore(tmp_path / "runs")
    old, new = str(uuid7()), str(uuid7())
    a, b = cas.put_bytes(b"a"), cas.put_bytes(b"b")
    rs.set_project_state("p", {"monte_carlo": b}, new)
    rs.set_project_state("p", {"monte_carlo": a}, old)
    assert rs.project_state("p")["monte_carlo"].sha256 == b.sha256


def test_checkout_only_from_the_latest_run(tmp_path):
    cas = LocalCAS(tmp_path / "cas")
    proj = tmp_path / "proj"
    rs = FileRunStore(proj / "build" / "runs")
    older, newer = rs.create(_run(cas)), rs.create(_run(cas))
    rs.transition(newer.run_id, RunStatus.running)
    rs.transition(older.run_id, RunStatus.running)  # started later, but older id: LATEST stays the newer run
    assert rs.latest() == newer.run_id
    assert checkout(rs.get(older.run_id), proj, cas, runs=rs) is False
    assert not (proj / "build" / "status.json").exists()
    assert checkout(rs.get(newer.run_id), proj, cas, runs=rs) is True
    assert json.loads((proj / "build" / "status.json").read_text())["settings"] == {"layout": {}}


@pytest.mark.skipif(not HAVE_TOOLS, reason="needs kicad-cli, the KiCad libraries and ngspice")
def test_two_concurrent_builds_of_one_project_do_not_mix(tmp_path):
    """#7: two builds of one project at once (different Monte Carlo counts) each keep their own run; build/ shows the
    newest run only, consistently (every status.json stage entry comes from it)."""
    proj = tmp_path / "sensor_preamp"
    shutil.copytree(EXAMPLE, proj, ignore=shutil.ignore_patterns("build"))
    code = "import sys; from hwflow.cli import main; sys.exit(main(sys.argv[1:]))"
    procs = [subprocess.Popen([sys.executable, "-c", code, "build", str(proj), "--until", "sim", "--mc", str(mc)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for mc in (0, 3)]
    outs = [p.communicate(timeout=600)[0] for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    rs = FileRunStore(proj / "build" / "runs")
    runs = rs.list("sensor_preamp")
    # Until sim leaves R7's board requirement unverified: its prose cannot substitute for a layout check.
    assert len(runs) == 2 and all(r.status == RunStatus.succeeded and r.verdict == "FAIL" for r in runs)
    cas = LocalCAS(settings_mod.get_settings().paths.cas_dir)
    for r in runs:
        assert [s.key for s in r.stages] == ["check", "sch", "sim"]
        assert all(s.status == StageStatus.succeeded and s.verdict == "pass" for s in r.stages)
        with cas.open(r.artifacts["REPORT.md"].sha256) as fh:
            report = fh.read().decode()
        assert "R7 UNVERIFIED" in report and "no current layout check names this requirement" in report
        jsonschema.validate(json.loads((rs.run_dir(r.run_id) / "manifest.json").read_text()), SCHEMA)
    latest = rs.get(rs.latest())
    assert latest.run_id == max(r.run_id for r in runs)
    status = json.loads((proj / "build" / "status.json").read_text())
    for s in latest.stages:
        assert status[s.stage]["inputs_hash"] == s.cache_key[:12], s.stage
    assert bool(status["sim"].get("mc_runs")) == (latest.options["mc"] == 3)
    assert [p.name for p in (proj / "build" / "runs").iterdir() if p.is_dir()
            and any(x.is_dir() for x in p.iterdir())] == []  # no scratch dir left
