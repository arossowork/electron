"""Light observability (#18): JSON log shape and context, redaction on the JSON output, request ids, /metrics,
the activity interceptor (a failed stage logs run_id, stage and the error class), the stage child's context."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import subprocess
import sys
import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from hwplatform.observability import bind, bound, context, metrics
from hwplatform.observability import http as obs_http
from hwplatform.observability.logs import JsonFormatter, configure_logging

KEY = "sk-ant-api03-" + "Q7r6T5u4" * 4


@contextlib.contextmanager
def capture(level=logging.INFO):
    """Root logger -> list of parsed JSON lines, for the duration of the block."""
    from hwplatform.logredact import install_log_redaction

    install_log_redaction()
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(JsonFormatter("test"))
    root = logging.getLogger()
    old_level, old_handlers = root.level, root.handlers[:]
    root.handlers[:] = [h]
    root.setLevel(level)
    lines: list[dict] = []
    try:
        yield lines
    finally:
        root.handlers[:] = old_handlers
        root.setLevel(old_level)
        lines.extend(json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip())


def sample(name: str, **labels) -> float:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(name, labels) or 0.0


# --- log shape, context, redaction ------------------------------------------------------------------------


def test_json_shape_and_context_fields():
    with capture() as lines:
        with bound(tenant_id="t1", run_id="r1", workflow_id="w1", stage="sim", attempt=2, activity="a"):
            logging.getLogger("hwplatform.x").info("hello %s", "world", extra={"k": 5})
        logging.getLogger("hwplatform.x").info("outside")
    first, second = lines
    assert first["level"] == "info" and first["logger"] == "hwplatform.x" and first["msg"] == "hello world"
    assert first["ts"].endswith("Z") and first["service"] == "test" and first["k"] == 5
    assert (first["tenant_id"], first["run_id"], first["workflow_id"], first["stage"], first["attempt"]) == (
        "t1",
        "r1",
        "w1",
        "sim",
        2,
    )
    assert first["activity"] == "a"
    assert "tenant_id" not in second and "run_id" not in second  # the context ends with the block


def test_exception_gets_error_class():
    with capture() as lines:
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("t").exception("it failed")
    assert lines[0]["error_class"] == "ValueError" and "Traceback" in lines[0]["exc"]


def test_context_follows_asyncio_tasks():
    async def go():
        with bound(run_id="r9"):
            await asyncio.create_task(asyncio.sleep(0))
            return context.current()

    assert asyncio.run(go()) == {"run_id": "r9"}


def test_redaction_on_json_output():
    log = logging.getLogger("t.redact")
    with capture() as lines:
        with bound(request_id=f"id-{KEY}"):
            log.info("key %s", KEY)
            log.info(f"fstring {KEY}")
            log.info("extra", extra={"header": f"Bearer {KEY}", "nested": {"k": KEY}})
            try:
                raise RuntimeError(f"bad key {KEY}")
            except RuntimeError:
                log.exception("failed")
    raw = json.dumps(lines)
    assert KEY not in raw and "sk-ant-api03-Q7r6" not in raw
    assert "sk-ant-***" in lines[0]["msg"] and "sk-ant-***" in lines[2]["header"]


def test_configure_logging_replaces_root_handlers_and_text_format():
    buf = io.StringIO()
    root = logging.getLogger()
    old_level, old = root.level, root.handlers[:]
    try:
        configure_logging("svc", fmt="json", stream=buf)
        with bound(run_id="r2"):
            logging.getLogger("uvicorn.error").warning("x")
        doc = json.loads(buf.getvalue())
        assert doc["service"] == "svc" and doc["run_id"] == "r2" and doc["level"] == "warning"
        buf.truncate(0)
        buf.seek(0)
        configure_logging("svc", fmt="text", stream=buf)
        with bound(run_id="r3"):
            logging.getLogger("a").error("plain %s", KEY)
        assert "run_id=r3" in buf.getvalue() and KEY not in buf.getvalue()
    finally:
        root.handlers[:] = old
        root.setLevel(old_level)
        logging.getLogger("uvicorn.access").disabled = False


# --- HTTP: request id, access line, route-template metrics -----------------------------------------------


def mini_app() -> FastAPI:
    app = FastAPI()

    async def who():
        bind(tenant_id="tenant-7", user_id="user-7")  # what api.deps.tenant_context does

    @app.get("/things/{thing_id}")
    async def thing(thing_id: str, _=Depends(who)):
        logging.getLogger("hwplatform.test").info("inside handler")
        return {"id": thing_id}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaput")

    obs_http.install(app)
    return app


def test_request_id_is_accepted_generated_echoed_and_logged():
    c = TestClient(mini_app(), raise_server_exceptions=False)
    with capture() as lines:
        r1 = c.get("/things/a", headers={"X-Request-ID": "req-abc.123"})
        r2 = c.get("/things/b")
        r3 = c.get("/things/c", headers={"X-Request-ID": "bad id with spaces\t"})
    assert r1.headers["x-request-id"] == "req-abc.123"
    gen = r2.headers["x-request-id"]
    assert len(gen) == 32 and gen != r3.headers["x-request-id"] != "bad id with spaces\t"
    inside = [ln for ln in lines if ln["msg"] == "inside handler"]
    assert inside[0]["request_id"] == "req-abc.123" and inside[0]["tenant_id"] == "tenant-7"
    access = [ln for ln in lines if ln["logger"] == "hwplatform.access"]
    assert [a["status"] for a in access] == [200, 200, 200]
    assert access[0]["request_id"] == "req-abc.123" and access[0]["tenant_id"] == "tenant-7"
    assert access[0]["route"] == "/things/{thing_id}" and access[0]["path"] == "/things/a"
    assert access[0]["duration_ms"] >= 0 and access[1]["request_id"] == gen


def test_http_metrics_use_the_route_template_and_count_errors():
    c = TestClient(mini_app(), raise_server_exceptions=False)
    before = sample("hwp_http_requests_total", method="GET", route="/things/{thing_id}", status="200")
    for _ in range(3):
        c.get(f"/things/{uuid.uuid4()}")
    with capture() as lines:
        r = c.get("/boom")
    assert r.status_code == 500
    assert (
        sample("hwp_http_requests_total", method="GET", route="/things/{thing_id}", status="200")
        == before + 3
    )
    assert sample("hwp_http_requests_total", method="GET", route="/boom", status="500") >= 1
    assert sample("hwp_http_request_duration_seconds_count", method="GET", route="/things/{thing_id}") >= 3
    fail = [ln for ln in lines if ln["msg"] == "request failed"][0]
    assert fail["error_class"] == "RuntimeError" and fail["route"] == "/boom"


def test_api_metrics_endpoint_exposes_the_named_series():
    from hwplatform.api.app import create_app
    from hwplatform.settings import Settings

    c = TestClient(create_app(Settings()))  # no lifespan: /healthz and /metrics need no database
    assert c.get("/healthz").json() == {"ok": True}
    metrics.SSE_CONNECTIONS.inc()
    metrics.SSE_CONNECTIONS.dec()
    metrics.RELAY_BACKLOG.set(0)
    metrics.OUTBOX_UNPUBLISHED.set(0)
    metrics.RELAY_LAG.set(0)
    r = c.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    for name in (
        "hwp_http_requests_total",
        "hwp_http_request_duration_seconds_bucket",
        "hwp_sse_connections",
        "hwp_relay_backlog",
        "hwp_relay_lag_seconds",
        "hwp_outbox_unpublished_rows",
        "hwp_relay_leader",
        "process_resident_memory_bytes",
    ):
        assert name in r.text, name
    assert 'route="/healthz"' in r.text
    off = TestClient(create_app(Settings(metrics_enabled=False)))
    assert off.get("/metrics").status_code == 404


# --- worker: /metrics on the health port -----------------------------------------------------------------


def test_worker_health_port_serves_metrics():
    from hwplatform.workers.main import Health

    async def get(port, path):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"GET {path} HTTP/1.0\r\n\r\n".encode())
        await w.drain()
        data = await r.read()
        w.close()
        return data.decode()

    async def go():
        h = Health()
        server = await h.serve("127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            return await get(port, "/metrics"), await get(port, "/livez")
        finally:
            server.close()

    text, live = asyncio.run(go())
    assert text.startswith("HTTP/1.0 200") and "Content-Type: text/plain" in text
    for name in (
        "hwp_activities_started_total",
        "hwp_activities_completed_total",
        "hwp_activities_failed_total",
        "hwp_stage_duration_seconds",
        "hwp_stage_child_peak_rss_bytes",
        "hwp_activity_slots_in_use",
        "hwp_activity_slots_max",
    ):
        assert f"# TYPE {name}" in text, name
    assert live.startswith("HTTP/1.0 200") and live.endswith("ok")


# --- worker: the activity interceptor ---------------------------------------------------------------------

temporalio = pytest.importorskip("temporalio")


def run_build(fakes, a):
    from datetime import timedelta

    from temporalio.testing import WorkflowEnvironment
    from wf_fakes import Workers

    from hwplatform.contracts import Q_ENGINE, QUEUES
    from hwplatform.workers.main import build_workers
    from hwplatform.workflows import BuildWorkflow

    async def go():
        env = await WorkflowEnvironment.start_time_skipping()
        try:
            slots = {q: {"activities": 2, "workflows": 2} for q in QUEUES}
            workers = build_workers(
                env.client,
                None,
                QUEUES,
                slots=slots,
                workflows=[BuildWorkflow],
                activities=fakes.acts,
                max_heartbeat_throttle_interval=timedelta(milliseconds=100),
                default_heartbeat_throttle_interval=timedelta(milliseconds=100),
            )
            async with Workers(workers):
                h = await env.client.start_workflow(
                    BuildWorkflow.run, a, id=a.ctx.workflow_id, task_queue=Q_ENGINE
                )
                return await h.result()
        finally:
            await env.shutdown()

    return asyncio.run(go())


def test_activity_context_metrics_and_failed_stage_log():
    from temporalio.exceptions import ApplicationError
    from wf_fakes import Fakes, arg

    from hwplatform.contracts import errors

    async def missing(inp):
        raise ApplicationError(
            "tools missing on this worker: kicad-cli", type=errors.ToolMissing, non_retryable=True
        )

    f = Fakes({"check": missing})
    a = arg({"until": "check"})
    failed_before = sum(
        s.value
        for m in metrics.ACT_FAILED.collect()
        for s in m.samples
        if s.name.endswith("_total") and s.labels.get("error_class") == errors.ToolMissing
    )
    with capture() as lines:
        with contextlib.suppress(
            Exception
        ):  # a non-retryable stage error ends the workflow; the logs are the point
            run_build(f, a)
    run_lines = [ln for ln in lines if ln.get("run_id") == a.ctx.run_id]
    assert run_lines, "no log line carries the run id"
    started = [
        ln
        for ln in run_lines
        if ln["msg"] == "activity started" and ln["activity"].endswith("stage_activity")
    ]
    assert started and started[0]["tenant_id"] == "t" and started[0]["workflow_id"] == a.ctx.workflow_id
    assert started[0]["stage"] == "check" and started[0]["attempt"] == 1
    failed = [ln for ln in run_lines if ln["msg"].startswith("activity failed")]
    assert failed, [ln["msg"] for ln in run_lines]
    assert failed[0]["error_class"] == errors.ToolMissing and failed[0]["level"] == "error"
    assert failed[0]["stage"] == "check" and failed[0]["run_id"] == a.ctx.run_id
    ok = [ln for ln in run_lines if ln["msg"] == "activity completed" and "duration_ms" in ln]
    assert ok  # record_run / resolve_run completed
    failed_after = sum(
        s.value
        for m in metrics.ACT_FAILED.collect()
        for s in m.samples
        if s.name.endswith("_total") and s.labels.get("error_class") == errors.ToolMissing
    )
    assert failed_after == failed_before + 1
    assert (
        sum(s.value for m in metrics.ACT_STARTED.collect() for s in m.samples if s.name.endswith("_total"))
        >= 1
    )
    assert sample("hwp_activity_slots_in_use", queue="hwflow-sim") == 0  # every slot is released
    assert sample("hwp_activity_slots_max", queue="hwflow-sim") == 2


# --- the stage child ---------------------------------------------------------------------------------------


def test_child_binds_the_exported_context_and_writes_rusage(tmp_path):
    from hwplatform.observability.child import read_rusage

    job = {
        "tenant_id": "tenant-c",
        "input": {"run_id": "run-c", "stage": "sim"},
        "log_context": {"workflow_id": "wf-c", "attempt": 3, "request_id": "rq"},
    }
    code = (
        "import json,logging,sys\n"
        "from pathlib import Path\n"
        "from hwplatform.observability.child import setup_child, write_rusage_at_exit\n"
        "job=json.loads(sys.argv[1]); setup_child(job)\n"
        "write_rusage_at_exit(Path(sys.argv[2]))\n"
        "logging.getLogger('hwflow.engine').info('stage child up with %s', 'sk-ant-api03-ABCDEFGH1234')\n"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code, json.dumps(job), str(tmp_path / "rusage.json")],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    doc = json.loads(out.stdout.strip().splitlines()[-1])
    assert doc["service"] == "stage-child" and doc["logger"] == "hwflow.engine"
    assert (doc["tenant_id"], doc["run_id"], doc["stage"]) == ("tenant-c", "run-c", "sim")
    assert (doc["workflow_id"], doc["attempt"], doc["request_id"]) == ("wf-c", 3, "rq")
    assert "ABCDEFGH1234" not in out.stdout
    assert read_rusage(tmp_path / "rusage.json") > 10 * 1024 * 1024  # a Python process is more than 10 MiB


def test_parent_forwards_child_lines_with_its_own_context(capsys):
    from hwplatform.observability.child import forward_line

    with capture() as lines:
        with bound(run_id="r-parent", stage="layout_attempt"):
            forward_line("KRT routed 12 nets")
    assert lines[0]["logger"] == "hwplatform.stage.child" and lines[0]["run_id"] == "r-parent"
    assert lines[0]["msg"] == "KRT routed 12 nets"
    forward_line('{"ts":"x","msg":"from child","run_id":"r-child"}')
    assert json.loads(capsys.readouterr().out)["run_id"] == "r-child"


def test_job_doc_exports_the_log_context():
    from pathlib import Path

    from wf_fakes import REF

    from hwplatform.activities.stage import job_doc
    from hwplatform.contracts import RunCtx, StageActivityInput

    inp = StageActivityInput(
        ctx=RunCtx("t", "p", "r", "w"), stage="check", key="check", design=REF, project=REF
    )
    with bound(tenant_id="t", run_id="r", workflow_id="w", attempt=2):
        doc = job_doc(inp, Path("/tmp/s"), Path("/tmp/c"))  # noqa: S108
    assert doc["log_context"] == {"tenant_id": "t", "run_id": "r", "workflow_id": "w", "attempt": 2}
