"""A design run through the platform with a scripted agent session (#15): API -> TemporalGateway -> Temporal dev
server -> DesignWorkflow -> the real activities (record_run/stage, record_question, design versions, agent_stage
with BYOK from the database, S3 snapshots) -> a scripted AgentRunner in place of the Claude Agent SDK.

- POST /runs kind=design starts the real DesignWorkflow; the session asks a question, the run waits; the answer
  through the API signals it and the stage goes on with the answers and the saved transcript.
- The next stage's session hits an invalid key: the key is marked invalid, the run waits; PUT of a new key
  signals `retry`, and the stage runs again with the new key.
- Usage is recorded per model; each stage end and the question saved a design version (source agent).
- Security: the key is in no workflow history, outbox event, log line, S3 object (versions, snapshots,
  transcripts) or API answer.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import tarfile
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import Account

pytestmark = pytest.mark.integration

V1 = "/api/v1"
KEY1 = "sk-ant-api03-" + "FIRSTSECRETKEY" * 6
KEY2 = "sk-ant-api03-" + "SECONDSECRETKY" * 6


class ScriptedRunner:
    """Stands in for SdkRunner: per stage a list of steps (spec, box, hooks) -> SessionResult."""

    def __init__(self):
        self.calls: list[tuple[str, str, str, str | None]] = []  # (stage, api_key, prompt, resume)
        self.steps = {
            "requirements": [self.req_ask, self.req_answered],
            "partition": [self.key_rejected, self.partition_done],
        }
        self.n: dict[str, int] = {}
        self.sids: list[str] = []

    async def run(self, spec, box, hooks):
        from hwplatform.agent.runner import SessionResult

        stage = box.stage
        n = self.n.get(stage, 0)
        self.n[stage] = n + 1
        self.calls.append((stage, spec.api_key, spec.prompt, spec.resume))
        assert not any(k.startswith("HWPLATFORM_") for k in spec.env())
        sid = spec.resume or str(uuid.uuid4())
        hooks.on_session(sid)
        self.sids.append(sid)
        t = spec.config_dir / "projects" / "-ws" / f"{sid}.jsonl"
        t.parent.mkdir(parents=True, exist_ok=True)
        with t.open("a") as f:
            f.write(json.dumps({"stage": stage, "n": n, "prompt": spec.prompt[:80]}) + "\n")
        await hooks.on_usage("claude-opus-5-5", {"input_tokens": 1000, "output_tokens": 200})
        res = await self.steps[stage][min(n, len(self.steps[stage]) - 1)](spec, box)
        return res if res is not None else SessionResult(sid, text=f"{stage} done")

    async def req_ask(self, spec, box):
        (spec.ws / "notes").mkdir(exist_ok=True)
        (spec.ws / "notes" / "brief.md").write_text("first notes\n")
        await box.call("log_decision", {"text": "units are SI", "source": "CLAUDE.md"})
        r = await box.call(
            "ask_product_person",
            {
                "questions": [
                    {"id": "q1", "text": "Battery?", "options": ["CR2032", "AA"], "default": "CR2032"}
                ]
            },
        )
        assert not r.get("is_error")

    async def req_answered(self, spec, box):
        assert "Answers of the product person, round 1" in spec.prompt and "q1 (Battery?): AA" in spec.prompt
        assert spec.resume and (spec.config_dir / "projects" / "-ws" / f"{spec.resume}.jsonl").is_file()
        (spec.ws / "requirements.yaml").write_text("requirements:\n  - id: R1\n    battery: AA\n")

    async def key_rejected(self, spec, box):
        from hwplatform.agent.runner import KEY_INVALID, SessionResult

        (spec.ws / "interface.yaml").write_text("draft: true\n")
        return SessionResult(spec.resume, error=KEY_INVALID, text="authentication_failed")

    async def partition_done(self, spec, box):
        assert (spec.ws / "interface.yaml").read_text() == "draft: true\n"  # from the snapshot
        (spec.ws / "interface.yaml").write_text("functions: []\n")


class Stack:
    """The Temporal dev server and the in-process workers, on a loop in a thread of their own."""

    def __init__(self, worker_settings, runner, root: Path):
        self.settings, self.runner, self.root = worker_settings, runner, root
        self.ready, self.stop = threading.Event(), threading.Event()
        self.box: dict = {}
        self.t = threading.Thread(target=self._main, daemon=True)

    def _main(self):
        async def go():
            from temporalio.common import SearchAttributeKey
            from temporalio.testing import WorkflowEnvironment
            from wf_fakes import Workers

            from hwplatform.activities import Deps
            from hwplatform.contracts import QUEUES
            from hwplatform.workers.main import build_workers

            try:
                env = await WorkflowEnvironment.start_local(
                    search_attributes=[
                        SearchAttributeKey.for_keyword(k)
                        for k in ("HwTenantId", "HwProjectId", "HwRunId", "HwRunKind")
                    ]
                )
            except Exception as e:  # noqa: BLE001 - reported to the test
                self.box["error"] = e
                self.ready.set()
                return
            deps = Deps(self.settings, temporal=env.client, runner=self.runner)
            self.box["address"] = env.client.service_client.config.target_host
            try:
                async with Workers(
                    build_workers(
                        env.client,
                        deps,
                        QUEUES,
                        max_heartbeat_throttle_interval=timedelta(milliseconds=200),
                        default_heartbeat_throttle_interval=timedelta(milliseconds=200),
                    )
                ):
                    self.ready.set()
                    await asyncio.to_thread(self.stop.wait)
            finally:
                await deps.close()
                await env.shutdown()

        asyncio.run(go())

    def __enter__(self):
        self.t.start()
        self.ready.wait(180)
        if "address" not in self.box:
            pytest.skip(f"Temporal dev server unavailable: {self.box.get('error')!r}")
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.t.join(60)


def _bundle() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in {
            "design.yaml": b"name: probe\ncomponents: {}\nnets: {}\n",
            "requirements.yaml": b"requirements: []\n",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def _wait(pred, what: str, timeout: float = 60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


def test_design_run_question_key_retry_and_no_key_leak(api_settings, validator, tmp_path, caplog, s3_client):
    from fastapi.testclient import TestClient
    from temporalio.client import Client

    from hwplatform.api.app import create_app
    from hwplatform.gateway import TemporalGateway
    from hwplatform.mail import InMemoryEmailSender
    from hwplatform.settings import Settings

    caplog.set_level(logging.DEBUG)
    runner = ScriptedRunner()
    wset = api_settings(worker_dir=str(tmp_path / "worker"))
    with Stack(wset, runner, tmp_path) as stack:
        addr = stack.box["address"]
        app = create_app(
            api_settings(),
            key_validator=validator,
            email_sender=InMemoryEmailSender(),
            gateway=TemporalGateway(Settings(temporal_address=addr)),
        )
        with TestClient(app, client=("10.9.8.7", 5000)) as c:
            a = Account(c, "design-e2e")
            r = c.put(f"{V1}/tenant/llm-keys/anthropic", json={"api_key": KEY1}, headers=a.h)
            assert r.status_code == 200, r.text
            p = a.project("probe")
            dv = c.post(
                f"{V1}/projects/{p['id']}/design-versions",
                files=[("bundle", ("p.tar.gz", _bundle(), "application/gzip"))],
                headers=a.h,
            )
            assert dv.status_code == 201, dv.text
            r = c.post(
                f"{V1}/projects/{p['id']}/runs",
                json={
                    "kind": "design",
                    "design_version_id": dv.json()["id"],
                    "options": {"brief": "A probe.", "stages": ["requirements", "partition"]},
                },
                headers=a.h,
            )
            assert r.status_code == 202, r.text
            run = r.json()
            rid = run["id"]

            def get_run():
                return c.get(f"{V1}/runs/{rid}", headers=a.h).json()

            # 1. the question: the run waits; the answer through the API signals the workflow
            qs = _wait(
                lambda: [
                    q
                    for q in c.get(f"{V1}/runs/{rid}/questions", headers=a.h).json()
                    if q["status"] == "open"
                ],
                "the question",
            )
            assert get_run()["status"] == "waiting" and len(runner.calls) == 1
            q = qs[0]
            assert q["questions"][0]["id"] == "q1" and q["stage"] == "requirements"
            r = c.post(f"{V1}/questions/{q['id']}/answer", json={"answers": {"q1": "AA"}}, headers=a.h)
            assert r.status_code == 200, r.text

            # 2. the invalid key: marked invalid, the run waits; a new key signals `retry`
            _wait(lambda: runner.n.get("partition") == 1 and get_run()["status"] == "waiting", "the key wait")
            st = c.get(f"{V1}/tenant/llm-keys/anthropic", headers=a.h).json()
            assert st["status"] == "invalid", st
            time.sleep(1.0)
            assert runner.n["partition"] == 1  # no busy loop while it waits
            r = c.put(f"{V1}/tenant/llm-keys/anthropic", json={"api_key": KEY2}, headers=a.h)
            assert r.status_code == 200, r.text

            final = _wait(
                lambda: (g := get_run())["status"] in ("succeeded", "failed", "cancelled") and g, "the end"
            )
            assert final["status"] == "succeeded", final
            assert [(s, k) for s, k, _, _ in runner.calls] == [
                ("requirements", KEY1),
                ("requirements", KEY1),
                ("partition", KEY1),
                ("partition", KEY2),
            ]
            assert (
                runner.calls[0][3] is None and runner.calls[1][3] == runner.sids[0]
            )  # resumed with the answers
            assert runner.calls[3][3] == runner.sids[2]  # the retry resumed the rejected session
            stages = {s["stage"]: s for s in final["stages"]}
            assert (
                stages["requirements"]["status"] == "succeeded"
                and stages["partition"]["status"] == "succeeded"
            )

            versions = c.get(f"{V1}/projects/{p['id']}/design-versions", headers=a.h).json()["items"]
            agent_versions = [v for v in versions if v.get("source") == "agent"]
            assert len(agent_versions) >= 3, versions  # the question round, requirements, partition
            latest = agent_versions[0]
            files = c.get(f"{V1}/design-versions/{latest['id']}", headers=a.h).json()
            api_text = json.dumps([versions, files, final, stages, qs])

            # usage per model, from every session
            from sqlalchemy import text as sql

            from hwplatform.db import Database

            async def usage_rows():
                db = Database.from_settings(api_settings())
                try:
                    async with db.tx(tenant_id=uuid.UUID(a.tenant_id)) as s:
                        rows = (
                            await s.execute(
                                sql(
                                    "SELECT model, input_tokens, output_tokens FROM llm_usage WHERE run_id = :r"
                                ),
                                {"r": uuid.UUID(rid)},
                            )
                        ).all()
                        events = (
                            await s.execute(
                                sql(
                                    "SELECT type, envelope::text FROM outbox WHERE tenant_id = :t ORDER BY id"
                                ),
                                {"t": uuid.UUID(a.tenant_id)},
                            )
                        ).all()
                    return rows, events
                finally:
                    await db.dispose()

            rows, events = asyncio.run(usage_rows())
            assert len(rows) == 4 and {r.model for r in rows} == {"claude-opus-5-5"}
            assert sum(r.input_tokens for r in rows) == 4000
            types = [e.type for e in events]
            assert "question.asked" in types and "question.answered" in types and "run.completed" in types

            # 3. the key is nowhere: workflow histories, events, logs, S3 objects, API answers
            async def histories() -> str:
                cl = await Client.connect(addr)
                out = []
                async for w in cl.list_workflows():
                    h = await cl.get_workflow_handle(w.id, run_id=w.run_id).fetch_history()
                    out.append(h.to_json())
                return "\n".join(out)

            hist = asyncio.run(histories())
            assert "DesignWorkflow" in hist and "EngineToolWorkflow" not in hist
            prefix = f"tenants/{a.tenant_id}/"
            blobs = []
            for page in s3_client.get_paginator("list_objects_v2").paginate(
                Bucket=wset.s3_bucket, Prefix=prefix
            ):
                for o in page.get("Contents", []):
                    body = s3_client.get_object(Bucket=wset.s3_bucket, Key=o["Key"])["Body"].read()
                    if o["Key"].endswith(".tar.gz") or body[:2] == b"\x1f\x8b":
                        import gzip

                        body = body + gzip.decompress(body)
                    blobs.append(body)
            assert blobs, "S3 holds the snapshots and versions"
            assert any(b"Answers of the product person" in b for b in blobs)  # the transcript was saved
            # the capture saw the worker side (DEBUG, every logger), so the log check below means something
            assert any(r.name.startswith("temporalio.activity") for r in caplog.records)
            assert any(r.name.startswith("botocore") for r in caplog.records)
            for key in (KEY1, KEY2):
                assert key not in hist
                assert not any(key in e.envelope for e in events)
                assert key not in caplog.text
                assert not any(key.encode() in b for b in blobs)
                assert key not in api_text
                for kpart in (key[:30], key[-30:]):
                    assert kpart not in hist and kpart not in caplog.text
