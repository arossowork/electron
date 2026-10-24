"""A real build of examples/sensor_preamp through the platform (#9 done-when): API -> Temporal (dev server) ->
BuildWorkflow -> `hwplatform-worker` (a separate process) -> stage children -> S3 -> run store -> outbox -> SSE.

- The verdict and REPORT.md equal an inline `hwflow build` of the same folder (apart from the timestamp line).
- The run's SSE stream: run.queued, run.started, stage.* in seq order, run.completed.
- Kill test: SIGKILL the worker while `sch` runs, start a new one: the run completes, `check` is not run again,
  `sch` is retried (attempt 2) after its heartbeat timeout.

`--until sim` by default (CI: needs kicad-cli, the KiCad libraries and ngspice). HWPLATFORM_TEST_FULL_BUILD=1 builds
every stage (needs the router tools too) and prints the time.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest
from test_api_e2e import frames, start_live  # noqa: F401 - fixture reused with the real gateway below

from conftest import Account

pytestmark = pytest.mark.integration

V1 = "/api/v1"
ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples" / "sensor_preamp"
FULL = os.environ.get("HWPLATFORM_TEST_FULL_BUILD") == "1"


def _have_tools() -> str | None:
    from hwflow.core.settings import get_settings

    s = get_settings()
    if not shutil.which(s.tools.kicad_cli) or not shutil.which(s.tools.ngspice):
        return "needs kicad-cli and ngspice"
    if not (Path(s.paths.kicad_share) / "symbols").is_dir():
        return "needs the KiCad libraries"
    return None


@pytest.fixture(scope="module")
def temporal_dev():
    """A Temporal dev server (with the ADR's search attributes) in a thread of its own; yields its address."""
    if why := _have_tools():
        pytest.skip(why)
    from temporalio.common import SearchAttributeKey
    from temporalio.testing import WorkflowEnvironment

    ready, stop, box = threading.Event(), threading.Event(), {}

    def main():
        async def go():
            try:
                env = await WorkflowEnvironment.start_local(
                    search_attributes=[
                        SearchAttributeKey.for_keyword(k)
                        for k in ("HwTenantId", "HwProjectId", "HwRunId", "HwRunKind")
                    ]
                )
            except Exception as e:  # noqa: BLE001 - reported to the test
                box["error"] = e
                ready.set()
                return
            box["address"] = env.client.service_client.config.target_host
            ready.set()
            await asyncio.to_thread(stop.wait)
            await env.shutdown()

        asyncio.run(go())

    t = threading.Thread(target=main, daemon=True)
    t.start()
    ready.wait(120)
    if "address" not in box:
        pytest.skip(f"Temporal dev server unavailable: {box.get('error')!r}")
    yield box["address"]
    stop.set()
    t.join(30)


@pytest.fixture
def gateway(temporal_dev):
    from hwplatform.gateway import TemporalGateway
    from hwplatform.settings import Settings

    return TemporalGateway(Settings(temporal_address=temporal_dev))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WorkerProc:
    """`python -m hwplatform.workers.main` with the test stack's settings in its environment."""

    def __init__(self, settings, address: str, root: Path):
        self.root = root
        self.env = {
            **os.environ,
            "HWPLATFORM_DATABASE_URL": settings.database_url,
            "HWPLATFORM_S3_ENDPOINT_URL": settings.s3_endpoint_url,
            "HWPLATFORM_S3_ACCESS_KEY": settings.s3_access_key,
            "HWPLATFORM_S3_SECRET_KEY": settings.s3_secret_key.get_secret_value(),
            "HWPLATFORM_S3_BUCKET": settings.s3_bucket,
            "HWPLATFORM_TEMPORAL_ADDRESS": address,
            "HWPLATFORM_WORKER_DIR": str(root / "worker"),
            "HWPLATFORM_WORKER_SHUTDOWN_GRACE_S": "5",
        }
        self.procs: list[subprocess.Popen] = []

    def start(self) -> subprocess.Popen:
        port = _free_port()
        log = open(self.root / f"worker-{len(self.procs)}.log", "wb")  # noqa: SIM115 - closed with the process
        p = subprocess.Popen(
            [sys.executable, "-m", "hwplatform.workers.main"],
            env={**self.env, "HWPLATFORM_WORKER_HEALTH_PORT": str(port)},
            cwd=self.root,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.procs.append(p)
        import httpx

        for _ in range(300):  # /readyz: connected and polling
            try:
                if httpx.get(f"http://127.0.0.1:{port}/readyz", timeout=1).status_code == 200:
                    return p
            except httpx.HTTPError:
                pass
            if p.poll() is not None:
                break
            time.sleep(0.1)
        raise AssertionError("worker not ready: " + self.log(len(self.procs) - 1))

    def log(self, i: int) -> str:
        return (self.root / f"worker-{i}.log").read_text(errors="replace")[-3000:]

    def stop(self):
        for p in self.procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for p in self.procs:
            try:
                p.wait(30)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)


@pytest.fixture
def worker(api_settings, temporal_dev, tmp_path):
    w = WorkerProc(api_settings(), temporal_dev, tmp_path)
    yield w
    w.stop()


def _bundle(src: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted(src.rglob("*")):
            rel = p.relative_to(src)
            if p.is_file() and "build" not in rel.parts:
                tf.add(p, arcname=rel.as_posix())
    return buf.getvalue()


def _start(c, a: Account, options: dict) -> tuple[str, dict]:
    p = a.project("sensor_preamp")
    dv = c.post(
        f"{V1}/projects/{p['id']}/design-versions",
        files=[("bundle", ("project.tar.gz", _bundle(EXAMPLE), "application/gzip"))],
        headers=a.h,
    )
    assert dv.status_code == 201, dv.text
    r = c.post(
        f"{V1}/projects/{p['id']}/runs",
        json={"design_version_id": dv.json()["id"], "options": options},
        headers=a.h,
    )
    assert r.status_code == 202, r.text
    return r.json()["id"], p


def _inline_report(tmp: Path, options: list[str]) -> tuple[int, str]:
    shutil.copytree(EXAMPLE, tmp / "sensor_preamp", ignore=shutil.ignore_patterns("build"))
    env = {**os.environ, "HWFLOW_CAS_DIR": str(tmp / "cas")}
    t0 = time.time()
    r = subprocess.run(
        [sys.executable, "-m", "hwflow.cli", "build", str(tmp / "sensor_preamp"), *options],
        env=env,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    print(f"\ninline build {' '.join(options)}: rc {r.returncode}, {time.time() - t0:.0f} s")
    return r.returncode, (tmp / "sensor_preamp" / "build" / "REPORT.md").read_text()


def _norm(report: str) -> str:
    return re.sub(r"(?m)^Updated .*$", "Updated <time>.", report)


def test_build_through_the_platform_equals_inline(start_live, worker, tmp_path):  # noqa: F811
    worker.start()
    live = start_live()
    c = live.client()
    a = Account(c, "build-e2e")
    options = {} if FULL else {"until": "sim"}
    t0 = time.time()
    rid, _ = _start(c, a, options)
    with c.stream("GET", f"{V1}/runs/{rid}/events", headers=a.h, timeout=3600) as resp:
        got, _ = frames(resp)
    elapsed = time.time() - t0
    print(f"\nplatform build {options or 'full'}: {elapsed:.0f} s")
    types = [f["event"] for f in got]
    assert types[:2] == ["run.queued", "run.started"] and types[-1] == "run.completed", (
        types,
        worker.log(0),
    )
    assert [f["data"]["seq"] for f in got] == list(range(1, len(got) + 1))
    stages = [f["data"]["data"]["key"] for f in got if f["event"] == "stage.started"]
    want = (
        ["check", "sch", "sim"]
        if not FULL
        else ["check", "sch", "sim", "layout_attempt.1", "layout_select", "post", "fab"]
    )
    assert stages == want
    assert types.count("stage.completed") == len(want)
    run = c.get(f"{V1}/runs/{rid}", headers=a.h).json()
    assert run["status"] == "succeeded"
    report = c.get(f"{V1}/runs/{rid}/report", headers=a.h).text
    names = {x["name"] for x in c.get(f"{V1}/runs/{rid}/artifacts", headers=a.h).json()}
    assert {"REPORT.md", "status.json", "manifest.json"} <= names, names
    assert "sch/schematic" in names

    rc, inline = _inline_report(tmp_path / "inline", [] if FULL else ["--until", "sim"])
    assert _norm(report) == _norm(inline)
    assert run["verdict"] == ("PASS" if "\nVerdict: PASS" in "\n" + inline else "FAIL")
    assert got[-1]["data"]["data"]["verdict"] == run["verdict"]


def test_a_killed_worker_is_replaced_and_the_run_completes(start_live, worker):  # noqa: F811
    first = worker.start()
    live = start_live()
    c = live.client()
    a = Account(c, "build-kill")
    rid, _ = _start(c, a, {"until": "sim"})
    for _ in range(600):  # until sch runs
        st = {s["key"]: s["status"] for s in c.get(f"{V1}/runs/{rid}", headers=a.h).json()["stages"]}
        if st.get("sch") == "running":
            break
        time.sleep(0.1)
    else:
        raise AssertionError(f"sch never ran: {st}\n{worker.log(0)}")
    time.sleep(1.0)
    os.killpg(first.pid, signal.SIGKILL)  # the worker and its stage child, mid-stage
    first.wait(10)
    t_kill = time.time()
    worker.start()
    with c.stream("GET", f"{V1}/runs/{rid}/events", headers=a.h, timeout=600) as resp:
        got, _ = frames(resp)
    print(f"\nkill test: run completed {time.time() - t_kill:.0f} s after the kill")
    assert got[-1]["event"] == "run.completed", [f["event"] for f in got]
    run = c.get(f"{V1}/runs/{rid}", headers=a.h).json()
    by = {s["key"]: s for s in run["stages"]}
    assert run["status"] == "succeeded" and set(by) == {"check", "sch", "sim"}
    assert by["check"]["attempt"] == 1 and by["sch"]["attempt"] == 2, by
    started = [f["data"]["data"] for f in got if f["event"] == "stage.started"]
    assert [s["key"] for s in started] == ["check", "sch", "sim"]  # check ran once: its result was kept


def test_hwflow_build_remote_executor(start_live, worker, tmp_path, capsys):  # noqa: F811
    """`hwflow build --remote` (the `remote` entry point of hwflow.executors): upload, run, follow, check out."""
    from hwflow.commands.run import load_executor

    from hwplatform.client import RemoteExecutor

    worker.start()
    live = start_live()
    c = live.client()
    a = Account(c, "build-remote")
    assert isinstance(load_executor("remote"), RemoteExecutor)
    proj = tmp_path / "sensor_preamp"
    shutil.copytree(EXAMPLE, proj, ignore=shutil.ignore_patterns("build"))
    ex = RemoteExecutor(live.base_url, a.tokens["access_token"])
    res = ex.build(proj, {"until": "sim", "mc": 0, "seeds": 4})
    out = capsys.readouterr().out
    assert "[check] PASS" in out and "[sim] PASS" in out, out
    report = (proj / "build" / "REPORT.md").read_text()
    assert (proj / "build" / "status.json").is_file() and "# sensor_preamp: build report" in report
    assert res["rc"] == (0 if "\nVerdict: PASS" in "\n" + report else 1)
    assert "succeeded" in ex.status(proj, None)
    assert ex.wait(proj, res["run_id"], timeout=5) == res["rc"]  # a finished run: checked out again
