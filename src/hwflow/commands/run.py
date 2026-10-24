"""Runs from the command line: `hwflow build --detach` (an inline build in the background), `hwflow run status` and
`hwflow run wait` (ADR 0001, 4.9: they replace polling a build's PID), and the executor plug-ins of
`hwflow build --remote`.

A detached build is an ordinary inline build started in its own session with HWFLOW_RUN_ID set, so its run id is known
before it starts; its output goes to build/runs/<run_id>.log and its process id to build/runs/<run_id>.pid. `run wait`
reads the run's manifest (build/runs/<run_id>/manifest.json) until the run is finished, for at most --timeout seconds
per call, and notices a build process that died without finishing its run.

Remote executors are entry points of the group `hwflow.executors` (hwplatform registers `remote`); the engine never
imports the platform. An executor is a class with `build(project_dir, options, *, detach) -> {"rc", "text", "run_id"}`,
`status(project_dir, run_id) -> str` and `wait(project_dir, run_id, timeout) -> int`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from ..ids import uuid7
from ..runs import TERMINAL, FileRunStore, RunNotFound

EXECUTOR_GROUP = "hwflow.executors"


def load_executor(name: str):
    """The executor class registered as `name` in the entry-point group hwflow.executors."""
    from importlib.metadata import entry_points
    eps = [e for e in entry_points(group=EXECUTOR_GROUP) if e.name == name]
    if not eps:
        raise SystemExit(f"no hwflow executor {name!r} installed (entry point group {EXECUTOR_GROUP}); "
                         "`uv sync --all-packages` installs the platform client (hwplatform)")
    return eps[0].load()()


def wants_remote(a) -> bool:
    return bool(getattr(a, "remote", False)) or os.environ.get("HWFLOW_EXECUTOR", "inline") == "remote"


def _runs(project: str) -> FileRunStore:
    p = Path(project)
    if p.is_file():
        p = p.parent
    return FileRunStore(p / "build" / "runs")


def detach(project: str, argv: list[str]) -> int:
    """Start `hwflow <argv without --detach>` in the background as run <new id>; print how to wait for it."""
    runs = _runs(project)
    runs.root.mkdir(parents=True, exist_ok=True)
    rid = str(uuid7())
    args = [a for a in argv if a != "--detach"]
    log = runs.root / f"{rid}.log"
    env = {**os.environ, "HWFLOW_RUN_ID": rid}
    with open(log, "ab") as fh:
        proc = subprocess.Popen([sys.executable, "-m", "hwflow.cli", *args], stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    (runs.root / f"{rid}.pid").write_text(str(proc.pid))
    print(f"run {rid} started in the background (log {log})")
    print(f"wait for it: uv run hwflow run wait {project} {rid}")
    return 0


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie of this shell's own child counts as gone
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def _pid(runs: FileRunStore, rid: str) -> int | None:
    try:
        return int((runs.root / f"{rid}.pid").read_text().strip())
    except (OSError, ValueError):
        return None


def _tail(runs: FileRunStore, rid: str, n: int = 3) -> list[str]:
    try:
        return (runs.root / f"{rid}.log").read_text(errors="replace").splitlines()[-n:]
    except OSError:
        return []


def _describe(runs: FileRunStore, rid: str) -> tuple[str, str | None]:
    """(text, status) of a run; status None when the run has no manifest yet."""
    try:
        run = runs.get(rid)
    except RunNotFound:
        return f"run {rid}: not started yet", None
    lines = [f"run {rid}: {run.status.value}" + (f", verdict {run.verdict}" if run.verdict else "")]
    for s in sorted(run.stages, key=lambda s: s.seq):
        extra = f" {s.verdict}" if s.verdict else ""
        extra += " (cached)" if s.cached else ""
        extra += f" {s.seconds:.0f} s" if s.seconds else ""
        extra += f" ({s.summary.get('reason')})" if s.summary.get("reason") else ""
        lines.append(f"  {s.key:<18} {s.status.value}{extra}")
    if run.error:
        lines.append(f"  error: {run.error.get('type')}: {run.error.get('message')}")
    return "\n".join(lines), run.status.value


def cmd_run(a) -> int:
    if wants_remote(a):
        ex = load_executor("remote")
        if a.what == "status":
            print(ex.status(Path(a.project), a.run_id))
            return 0
        return ex.wait(Path(a.project), a.run_id, a.timeout)
    runs = _runs(a.project)
    detached = sorted(p.stem for p in runs.root.glob("*.pid")) if runs.root.is_dir() else []
    rid = a.run_id or max([x for x in (runs.latest(), *detached) if x], default=None)  # UUIDv7: newest sorts last
    if not rid:
        print(f"no run in {runs.root}")
        return 1
    if a.what == "status":
        text, status = _describe(runs, rid)
        print(text)
        if status not in TERMINAL:
            print("\n".join(f"  | {ln}" for ln in _tail(runs, rid)))
        return 0
    t_end = time.time() + a.timeout
    while True:
        text, status = _describe(runs, rid)
        if status in TERMINAL:
            print(text)
            if status == "succeeded":
                print(f"report: {runs.root.parent / 'REPORT.md'}")
            return 0 if status == "succeeded" and _verdict(runs, rid) == "PASS" else 1
        pid = _pid(runs, rid)
        if pid is not None and not _alive(pid):
            time.sleep(1)
            text, status = _describe(runs, rid)
            if status not in TERMINAL:
                print(text)
                print("the build process ended without finishing its run (killed?); its last output:")
                print("\n".join(f"  | {ln}" for ln in _tail(runs, rid, 10)))
                return 1
            continue
        if time.time() >= t_end:
            print(text)
            print("\n".join(f"  | {ln}" for ln in _tail(runs, rid)))
            print(f"still running after {a.timeout:g} s: call `hwflow run wait {a.project} {rid}` again")
            return 2
        time.sleep(min(2.0, max(0.1, t_end - time.time())))


def _verdict(runs: FileRunStore, rid: str) -> str | None:
    try:
        return runs.get(rid).verdict
    except RunNotFound:
        return None


def remote_build(a, options: dict) -> int:
    res = load_executor("remote").build(Path(a.project), options, detach=bool(getattr(a, "detach", False)))
    if res.get("text"):
        print(res["text"])
    return int(res.get("rc", 0))


REMOTE_OPTIONS = ("until", "mc", "reuse", "force", "rebuild", "auto", "attempts", "retry_layout")


def remote_options(o) -> dict:
    """The BuildOptions a remote build takes (the platform's allowlist; seeds/iters/passes keep their defaults)."""
    return {k: getattr(o, k) for k in REMOTE_OPTIONS}
