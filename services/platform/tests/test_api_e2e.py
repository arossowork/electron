"""End to end through a real HTTP server (uvicorn), Postgres, SeaweedFS, NATS JetStream and the outbox relay.

Only Temporal is faked: the test plays the worker by driving PgRunStore / QuestionStore, whose transactions write the
outbox events the relay publishes. Create project -> upload a design version -> start a run -> follow its SSE
stream (replayed events, then live ones, in seq order) -> download an artifact through a presigned URL -> answer a
question -> the gateway got the signal.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest

from hwplatform.contracts import RunStatus, StageRecord, StageStatus
from hwplatform.db import Database
from hwplatform.questions import QuestionStore
from hwplatform.runs import PgRunStore
from hwplatform.storage import S3ArtifactStore

from conftest import S3_BUCKET, Account

pytestmark = pytest.mark.integration

V1 = "/api/v1"
DESIGN = b"name: demo\nparts: []\n"


class Live:
    def __init__(self, base_url: str, app):
        self.base_url, self.app = base_url, app

    def client(self, **kw) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(20.0, connect=5.0), **kw)


@pytest.fixture
def start_live(api_settings, gateway, validator, relay_ready):
    import uvicorn

    from hwplatform.api.app import create_app
    from hwplatform.mail import InMemoryEmailSender

    stops = []

    def start(**overrides) -> Live:
        s = api_settings(
            **{
                "relay_enabled": True,
                "relay_poll_s": 0.2,
                "s3_public_url": api_settings().s3_endpoint_url,
                "sse_keepalive_s": 1.0,
                "sse_poll_s": 120.0,  # a late event can only come from NATS: the test proves the live path
                **overrides,
            }
        )
        app = create_app(s, key_validator=validator, email_sender=InMemoryEmailSender(), gateway=gateway)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("uvicorn did not start")
        stops.append((server, t))
        return Live(f"http://127.0.0.1:{port}", app)

    yield start
    for server, t in stops:
        server.should_exit = True
        t.join(timeout=15)


@pytest.fixture
def live(start_live):
    return start_live()


def frames(resp: httpx.Response, *, stop=lambda fs: False):
    """Parse an SSE response into frames {id, event, data}; keep-alive comments are counted in `comments`."""
    out, cur, comments = [], {}, 0
    for line in resp.iter_lines():
        if line == "":
            if cur:
                out.append(cur)
                cur = {}
                if stop(out):
                    break
            continue
        if line.startswith(":"):
            comments += 1
            continue
        k, _, v = line.partition(": ")
        cur[k] = json.loads(v) if k == "data" else v
    return out, comments


def test_end_to_end_with_sse_replay_and_live(live, gateway, api_settings):
    c = live.client()
    a = Account(c, "e2e")
    p = a.project("demo")
    other = a.project("other")  # a second project of the same tenant: must not show in p's event stream
    dv = c.post(
        f"{V1}/projects/{p['id']}/design-versions",
        files=[("design", ("design.yaml", DESIGN, "application/yaml"))],
        headers=a.h,
    )
    assert dv.status_code == 201, dv.text
    dv = dv.json()
    started = c.post(
        f"{V1}/projects/{p['id']}/runs",
        json={"design_version_id": dv["id"], "options": {"until": "sim"}},
        headers=a.h,
    )
    assert started.status_code == 202, started.text
    run = started.json()
    rid = run["id"]
    assert len(gateway.started) == 1 and gateway.started[0][1].ctx.run_id == rid
    c.post(
        f"{V1}/projects/{other['id']}/design-versions",
        files=[("design", ("design.yaml", DESIGN + b"#o\n", "application/yaml"))],
        headers=a.h,
    )

    def worker(fn):
        """Play the worker: one transaction-sized step on its own event loop."""

        async def main():
            db = Database.from_settings(api_settings())
            try:
                return await fn(db, PgRunStore(db, a.tenant_id))
            finally:
                await db.dispose()

        return asyncio.run(main())

    async def s1(db, st):
        await st.transition(rid, RunStatus.running)
        await st.upsert_stage(rid, StageRecord("check", "check", 0, StageStatus.running))
        await st.upsert_stage(
            rid, StageRecord("check", "check", 0, StageStatus.succeeded, verdict="PASS", seconds=0.5)
        )

    worker(s1)  # seq 1 run.queued (API), 2 run.started, 3 stage.started, 4 stage.completed

    report = b"# REPORT\nPASS\n"
    qid = {}

    def drive():
        try:
            time.sleep(2.6)  # let the stream connect, replay, and idle past a keep-alive

            async def s2(db, st):
                await st.upsert_stage(rid, StageRecord("sim", "sim", 1, StageStatus.running))
                await st.upsert_stage(
                    rid, StageRecord("sim", "sim", 1, StageStatus.succeeded, verdict="PASS", seconds=1.0)
                )
                store = S3ArtifactStore(live.app.state.s3, S3_BUCKET, a.tenant_id)
                ref = store.put_bytes(report, media_type="text/markdown", name="REPORT.md")
                await st.set_artifacts(rid, {"REPORT.md": ref})
                q = await QuestionStore(db, a.tenant_id).ask(
                    rid, "agent.requirements", [{"id": "q1", "text": "Voltage?"}]
                )
                qid["id"] = q.id
                await st.transition(
                    rid, RunStatus.waiting, event_data={"reason": "question", "question_id": q.id}
                )

            worker(
                s2
            )  # 5 stage.started, 6 stage.completed, 7 artifact.created, 8 question.asked, 9 run.waiting
            time.sleep(0.5)
            r = c2.post(f"{V1}/questions/{qid['id']}/answer", json={"answers": {"q1": "3.3"}}, headers=a.h)
            assert r.status_code == 200, r.text  # 10 question.answered

            async def s3(db, st):
                await st.transition(
                    rid, RunStatus.running, event_data={"reason": "question"}
                )  # 11 run.resumed
                await st.transition(rid, RunStatus.succeeded, verdict="PASS")  # 12 run.completed

            worker(s3)
        except BaseException as e:
            errors.append(e)

    errors: list[BaseException] = []
    c2 = live.client()
    t0 = time.monotonic()
    th = threading.Thread(target=drive)
    th.start()
    with c.stream("GET", f"{V1}/runs/{rid}/events", headers=a.h) as resp:
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        got, comments = frames(resp)  # ends by itself after run.completed
    th.join()
    assert not errors, errors
    assert time.monotonic() - t0 < 60  # sse_poll_s is 120: the late events were live (NATS), not polled

    types = [f["event"] for f in got]
    assert types == [
        "run.queued",
        "run.started",
        "stage.started",
        "stage.completed",
        "stage.started",
        "stage.completed",
        "artifact.created",
        "question.asked",
        "run.waiting",
        "question.answered",
        "run.resumed",
        "run.completed",
    ], types
    assert [int(f["id"]) for f in got] == list(range(1, 13))  # ordered by per-run seq, no gap, no duplicate
    assert [f["data"]["seq"] for f in got] == list(range(1, 13))
    assert all(f["data"]["runid"] == rid and f["data"]["tenantid"] == a.tenant_id for f in got)
    assert all(f["data"]["specversion"] == "1.0" and f["data"]["type"] == f["event"] for f in got)
    assert got[-1]["data"]["data"]["verdict"] == "PASS"
    assert comments >= 1  # keep-alive comments while idle

    # Last-Event-ID: resume after seq 10 gets 11 and 12 only, then closes
    with c.stream("GET", f"{V1}/runs/{rid}/events", headers={**a.h, "Last-Event-ID": "10"}) as resp:
        again, _ = frames(resp)
    assert [int(f["id"]) for f in again] == [11, 12]
    # ... and a finished run replays completely and closes
    with c.stream("GET", f"{V1}/runs/{rid}/events", headers=a.h) as resp:
        full, _ = frames(resp)
    assert [f["event"] for f in full] == types

    # the tenant stream replays from an outbox id; the project stream keeps only that project's events
    with c.stream("GET", f"{V1}/events", headers={**a.h, "Last-Event-ID": "0"}) as resp:
        tenant_frames, _ = frames(resp, stop=lambda fs: fs[-1]["event"] == "run.completed")
    ids = [int(f["id"]) for f in tenant_frames]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    kinds = [f["event"] for f in tenant_frames]
    assert kinds.count("design_version.created") == 2 and "run.completed" in kinds
    with c.stream(
        "GET", f"{V1}/projects/{other['id']}/events", headers={**a.h, "Last-Event-ID": "0"}
    ) as resp:
        other_frames, _ = frames(resp, stop=lambda fs: len(fs) >= 1)
    assert [f["event"] for f in other_frames] == ["design_version.created"]
    assert other_frames[0]["data"]["subject"].startswith(f"projects/{other['id']}/")
    with c.stream("GET", f"{V1}/projects/{p['id']}/events", headers={**a.h, "Last-Event-ID": "0"}) as resp:
        p_frames, _ = frames(resp, stop=lambda fs: fs[-1]["event"] == "run.completed")
    assert {f["data"].get("runid") for f in p_frames} <= {None, rid}
    assert not any(f["data"]["subject"].startswith(f"projects/{other['id']}/") for f in p_frames)

    # a live event on the tenant stream (no replay): a new design version arrives after we connect
    with c.stream("GET", f"{V1}/events", headers=a.h) as resp:

        def later():
            time.sleep(1.0)
            c2.post(
                f"{V1}/projects/{p['id']}/design-versions",
                files=[("design", ("design.yaml", DESIGN + b"#live\n", "application/yaml"))],
                headers=a.h,
            )

        threading.Thread(target=later).start()
        live_frames, _ = frames(resp, stop=lambda fs: True)
    assert live_frames[0]["event"] == "design_version.created"

    # the report: listed, and downloaded through a presigned URL (the bytes bypass the API)
    arts = c.get(f"{V1}/runs/{rid}/artifacts", headers=a.h).json()
    assert [x["name"] for x in arts] == ["REPORT.md"]
    r = c.get(f"{V1}/runs/{rid}/artifacts/REPORT.md", headers=a.h, follow_redirects=False)
    assert r.status_code == 302 and "X-Amz-Signature" in r.headers["location"]
    assert httpx.get(r.headers["location"], timeout=10).content == report
    assert c.get(f"{V1}/runs/{rid}/report", headers=a.h).content == report

    # the question: answered, and the (fake) Temporal got the signal for this run's workflow
    assert len(gateway.signals) == 1
    wf, sig = gateway.signals[0]
    assert wf == run["workflow_id"] and sig.question_id == qid["id"] and sig.answers == {"q1": "3.3"}
    final = c.get(f"{V1}/runs/{rid}", headers=a.h).json()
    assert final["status"] == "succeeded" and final["verdict"] == "PASS"
    assert [(s["key"], s["status"]) for s in final["stages"]] == [
        ("check", "succeeded"),
        ("sim", "succeeded"),
    ]

    # another tenant sees none of it, on any stream
    b = Account(c, "e2e-other")
    assert c.get(f"{V1}/runs/{rid}/events", headers=b.h).status_code == 404
    pb = b.project("b")
    c.post(
        f"{V1}/projects/{pb['id']}/design-versions",
        files=[("design", ("design.yaml", DESIGN + b"#b\n", "application/yaml"))],
        headers=b.h,
    )
    with c.stream("GET", f"{V1}/events", headers={**b.h, "Last-Event-ID": "0"}) as resp:
        theirs, _ = frames(resp, stop=lambda fs: len(fs) >= 1)
    assert all(f["data"]["tenantid"] == b.tenant_id for f in theirs)
    c2.close()
    c.close()


def test_stream_still_works_without_nats_by_polling_the_outbox(start_live, api_settings):
    """NATS unreachable and no relay: the outbox is the source of truth, so the stream polls it."""
    lv = start_live(relay_enabled=False, nats_url="nats://127.0.0.1:1", sse_poll_s=0.5)
    c = lv.client()
    a = Account(c, "nonats")
    p = a.project("demo")
    dv = c.post(
        f"{V1}/projects/{p['id']}/design-versions",
        files=[("design", ("design.yaml", DESIGN, "application/yaml"))],
        headers=a.h,
    ).json()
    rid = c.post(f"{V1}/projects/{p['id']}/runs", json={"design_version_id": dv["id"]}, headers=a.h).json()[
        "id"
    ]

    def later():
        time.sleep(1.5)

        async def main():
            db = Database.from_settings(api_settings())
            try:
                st = PgRunStore(db, a.tenant_id)
                await st.transition(rid, RunStatus.running)
                await st.transition(rid, RunStatus.failed, error={"type": "X", "message": "boom"})
            finally:
                await db.dispose()

        asyncio.run(main())

    th = threading.Thread(target=later)
    th.start()
    with c.stream("GET", f"{V1}/runs/{rid}/events", headers=a.h) as resp:
        got, _ = frames(resp)
    th.join()
    assert [f["event"] for f in got] == ["run.queued", "run.started", "run.failed"]
    assert [int(f["id"]) for f in got] == [1, 2, 3]
    c.close()
