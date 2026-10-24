"""`hwflow run status|wait` (ADR 0001 4.9): they replace polling a build's PID. Exit codes of wait: 0 succeeded and
PASS, 1 failed / FAIL / the build process died, 2 still running after --timeout."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime

from hwflow.artifacts import ArtifactRef
from hwflow.commands.run import cmd_run
from hwflow.ids import uuid7
from hwflow.runs import DesignVersionRef, FileRunStore, Run, RunStatus

REF = ArtifactRef("a" * 64, 1, "application/yaml", "design.yaml")


def _run(runs: FileRunStore) -> str:
    rid = str(uuid7())
    runs.create(Run(run_id=rid, kind="build", tenant_id=None, project_id="p",
                    design_version=DesignVersionRef(id="x", content_sha256="x", design=REF, requirements=None,
                                                    project=REF),
                    status=RunStatus.queued, verdict=None, options={}, options_hash="0" * 12, settings={}, code={},
                    tools={}, stages=[], artifacts={}, created_at=datetime.now(UTC)))
    return rid


def _args(proj, what="wait", run_id=None, timeout=1.0):
    return argparse.Namespace(what=what, project=str(proj), run_id=run_id, timeout=timeout, remote=False)


def test_wait_exit_codes(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("HWFLOW_EXECUTOR", raising=False)
    runs = FileRunStore(tmp_path / "build" / "runs")
    rid = _run(runs)
    runs.transition(rid, RunStatus.running)
    assert cmd_run(_args(tmp_path, timeout=0.3)) == 2  # still running, no pid file: timeout
    assert "still running" in capsys.readouterr().out
    runs.transition(rid, RunStatus.succeeded, verdict="PASS")
    assert cmd_run(_args(tmp_path)) == 0
    assert cmd_run(_args(tmp_path, what="status")) == 0
    assert "succeeded, verdict PASS" in capsys.readouterr().out

    fail = _run(runs)
    runs.transition(fail, RunStatus.running)
    runs.transition(fail, RunStatus.succeeded, verdict="FAIL")
    assert cmd_run(_args(tmp_path, run_id=fail)) == 1


def test_wait_notices_a_dead_build_process(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("HWFLOW_EXECUTOR", raising=False)
    runs = FileRunStore(tmp_path / "build" / "runs")
    rid = _run(runs)
    runs.transition(rid, RunStatus.running)
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    (runs.root / f"{rid}.pid").write_text(str(p.pid))
    (runs.root / f"{rid}.log").write_text("[sim] OK\nKilled\n")
    assert cmd_run(_args(tmp_path, timeout=30)) == 1  # the newest run by default
    out = capsys.readouterr().out
    assert "ended without finishing" in out and "Killed" in out


def test_no_run(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("HWFLOW_EXECUTOR", raising=False)
    assert cmd_run(_args(tmp_path)) == 1
    assert "no run" in capsys.readouterr().out
