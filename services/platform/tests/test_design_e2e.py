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
                    {
                        "id": "q1",
                        "text": "Battery?",
                        "options": ["CR2032", "AA"],
                        "recommended": "CR2032",
                        "critical": True,
                    }
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


OAT = "sk-ant-oat01-" + "OPERATORTOKEN1" * 6 + "OTOK"


class SubscriptionRunner:
    """The operator tenant's session with a subscription token: the usage window is hit once, then done."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []  # (oauth_token, api_key, env)

    async def run(self, spec, box, hooks):
        from hwplatform.agent.runner import RATE_LIMITED, SessionResult

        self.calls.append((spec.oauth_token, spec.api_key, spec.env()))
        sid = spec.resume or str(uuid.uuid4())
        hooks.on_session(sid)
        t = spec.config_dir / "projects" / "-ws" / f"{sid}.jsonl"
        t.parent.mkdir(parents=True, exist_ok=True)
        with t.open("a") as f:
            f.write(json.dumps({"prompt": spec.prompt[:80]}) + "\n")
        await hooks.on_usage("claude-opus-5-5", {"input_tokens": 10, "output_tokens": 2})
        if len(self.calls) == 1:
            reset = int(time.time()) + 3 * 3600
            return SessionResult(sid, error=RATE_LIMITED, text=f"Claude AI usage limit reached|{reset}")
        (spec.ws / "requirements.yaml").write_text("requirements: []\n")
        return SessionResult(sid, text="done")


def test_operator_subscription_token_limit_wait_and_no_token_leak(
    api_settings, validator, tmp_path, caplog, s3_client
):
    """An operator tenant (HWPLATFORM_SUBSCRIPTION_TENANT_IDS) stores a subscription token; the session gets it
    as CLAUDE_CODE_OAUTH_TOKEN and no API key; the usage-limit message becomes a workflow timer of the window's
    reset (3 h), ended here by a `retry` signal; the token is in no history, event, log line, S3 object or API
    answer."""
    from fastapi.testclient import TestClient
    from temporalio.client import Client

    from hwplatform.api.app import create_app
    from hwplatform.gateway import TemporalGateway
    from hwplatform.mail import InMemoryEmailSender
    from hwplatform.settings import Settings

    caplog.set_level(logging.DEBUG)
    runner = SubscriptionRunner()
    wset = api_settings(worker_dir=str(tmp_path / "worker"))
    aset = api_settings()
    with Stack(wset, runner, tmp_path) as stack:
        addr = stack.box["address"]
        app = create_app(
            aset,
            key_validator=validator,
            email_sender=InMemoryEmailSender(),
            gateway=TemporalGateway(Settings(temporal_address=addr)),
        )
        with TestClient(app, client=("10.9.8.6", 5000)) as c:
            a = Account(c, "design-oat")
            url = f"{V1}/tenant/llm-keys/anthropic"
            body = {"api_key": OAT, "credential_type": "subscription_token"}
            assert c.put(url, json=body, headers=a.h).status_code == 403  # not an operator tenant yet
            wset.subscription_tenant_ids = aset.subscription_tenant_ids = a.tenant_id
            r = c.put(url, json=body, headers=a.h)
            assert r.status_code == 200 and r.json()["credential_type"] == "subscription_token", r.text
            p = a.project("oat")
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
                    "options": {"brief": "A probe.", "stages": ["requirements"]},
                },
                headers=a.h,
            )
            assert r.status_code == 202, r.text
            rid, wf = r.json()["id"], r.json()["workflow_id"]

            async def state_then_retry():
                cl = await Client.connect(addr)
                h = cl.get_workflow_handle(wf)
                st: dict = {}
                for _ in range(300):
                    st = await h.query("state")
                    if (st.get("waiting") or {}).get("reason") == "rate_limited":
                        break
                    await asyncio.sleep(0.2)
                await asyncio.sleep(1.0)
                assert len(runner.calls) == 1  # a timer, no busy loop
                await h.signal("retry")
                return st

            st = asyncio.run(state_then_retry())
            assert 3 * 3600 <= st["waiting"]["seconds"] <= 3 * 3600 + 120, st
            final = _wait(
                lambda: (
                    (g := c.get(f"{V1}/runs/{rid}", headers=a.h).json())["status"]
                    in ("succeeded", "failed", "cancelled")
                    and g
                ),
                "the end",
            )
            assert final["status"] == "succeeded", final
            assert len(runner.calls) == 2
            for tok, key, env in runner.calls:
                assert tok == OAT and key == "" and env["CLAUDE_CODE_OAUTH_TOKEN"] == OAT
                assert env["ANTHROPIC_API_KEY"] == ""  # blank: the launcher drops it
            api_text = json.dumps([final, c.get(url, headers=a.h).json()])

            from sqlalchemy import text as sql

            from hwplatform.db import Database

            async def events():
                db = Database.from_settings(aset)
                try:
                    async with db.tx(tenant_id=uuid.UUID(a.tenant_id)) as s:
                        q = sql("SELECT envelope::text FROM outbox WHERE tenant_id = :t")
                        return (await s.execute(q, {"t": uuid.UUID(a.tenant_id)})).scalars().all()
                finally:
                    await db.dispose()

            async def history() -> str:
                cl = await Client.connect(addr)
                return (await cl.get_workflow_handle(wf).fetch_history()).to_json()

            env_rows, hist = asyncio.run(events()), asyncio.run(history())
            assert env_rows and "LlmRateLimited" in hist
            blobs = []
            for page in s3_client.get_paginator("list_objects_v2").paginate(
                Bucket=wset.s3_bucket, Prefix=f"tenants/{a.tenant_id}/"
            ):
                for o in page.get("Contents", []):
                    b = s3_client.get_object(Bucket=wset.s3_bucket, Key=o["Key"])["Body"].read()
                    if b[:2] == b"\x1f\x8b":
                        import gzip

                        b = b + gzip.decompress(b)
                    blobs.append(b)
            assert blobs and any(r.name.startswith("temporalio.activity") for r in caplog.records)
            for part in (OAT, OAT[:30], OAT[-30:]):
                assert part not in hist and part not in caplog.text and part not in api_text
                assert not any(part in e for e in env_rows)
                assert not any(part.encode() in b for b in blobs)


class ChatRunner:
    """One `requirements` stage in three sessions: (0) works until the test says the chat was sent, takes it at
    the turn boundary, answers it, then asks a NON-critical question; (1) resumes with the defaulted answer and
    works until the test has overridden it; (2) resumes with the override."""

    def __init__(self):
        self.calls: list[tuple[str | None, str]] = []  # (resume, prompt)
        self.chat_sent, self.override_sent = threading.Event(), threading.Event()
        self.in_tool, self.second_started = threading.Event(), threading.Event()
        self.seen_chat: list[list[str]] = []
        self.decisions_file = ""

    async def run(self, spec, box, hooks):
        from hwplatform.agent.runner import SessionResult

        n = len(self.calls)
        self.calls.append((spec.resume, spec.prompt))
        sid = spec.resume or str(uuid.uuid4())
        hooks.on_session(sid)
        t = spec.config_dir / "projects" / "-ws" / f"{sid}.jsonl"
        t.parent.mkdir(parents=True, exist_ok=True)
        with t.open("a") as f:
            f.write(json.dumps({"n": n}) + "\n")
        await hooks.on_usage("claude-opus-5-5", {"input_tokens": 10, "output_tokens": 2})
        if n == 0:
            self.seen_chat.append(await hooks.take_chat())  # the start of the stage: nothing yet
            self.in_tool.set()  # a long tool call: the chat message arrives meanwhile ...
            await asyncio.to_thread(self.chat_sent.wait, 60)
            self.seen_chat.append(await hooks.take_chat())  # ... and is taken at the turn boundary
            await hooks.on_reply("5 V rail noted.")
            question = {
                "id": "q1",
                "text": "Battery?",
                "options": ["CR2032", "AA"],
                "recommended": "AA",
                "critical": False,
            }
            r = await box.call("ask_product_person", {"questions": [question]})
            assert not r.get("is_error")
        elif n == 1:
            assert "q1 (Battery?): AA [DEFAULT" in spec.prompt
            self.decisions_file = (spec.ws / "notes" / "decisions.md").read_text()
            self.second_started.set()
            await asyncio.to_thread(self.override_sent.wait, 60)
            (spec.ws / "requirements.yaml").write_text("requirements:\n  - id: R1\n    battery: AA\n")
        else:
            assert "was `AA`" in spec.prompt and "is now `CR2032`" in spec.prompt
            (spec.ws / "requirements.yaml").write_text("requirements:\n  - id: R1\n    battery: CR2032\n")
        return SessionResult(sid, text=f"stage pass {n}")


def test_design_run_chat_default_after_timeout_and_override(api_settings, validator, tmp_path, s3_client):
    """The whole path through API, Temporal, activities and Postgres: a chat message sent during a tool call
    reaches the agent at the turn boundary and its reply is an `agent.message`; a non-critical question nobody
    answers within the timeout (2 s here) is answered with the recommended option and logged in the design notes;
    the person's late answer overrides it and the stage runs again with the change."""
    from fastapi.testclient import TestClient
    from sqlalchemy import text as sql

    from hwplatform.api.app import create_app
    from hwplatform.db import Database
    from hwplatform.gateway import TemporalGateway
    from hwplatform.mail import InMemoryEmailSender
    from hwplatform.settings import Settings

    runner = ChatRunner()
    wset = api_settings(worker_dir=str(tmp_path / "worker"))
    with Stack(wset, runner, tmp_path) as stack:
        app = create_app(
            api_settings(agent_question_timeout_s=2),
            key_validator=validator,
            email_sender=InMemoryEmailSender(),
            gateway=TemporalGateway(Settings(temporal_address=stack.box["address"])),
        )
        with TestClient(app, client=("10.9.8.8", 5000)) as c:
            a = Account(c, "design-chat")
            r = c.put(f"{V1}/tenant/llm-keys/anthropic", json={"api_key": KEY1}, headers=a.h)
            assert r.status_code == 200, r.text
            p = a.project("chatty")
            dv = c.post(
                f"{V1}/projects/{p['id']}/design-versions",
                files=[("bundle", ("p.tar.gz", _bundle(), "application/gzip"))],
                headers=a.h,
            ).json()
            options = {"brief": "A probe.", "stages": ["requirements"]}
            body = {"kind": "design", "design_version_id": dv["id"], "options": options}
            rid = c.post(f"{V1}/projects/{p['id']}/runs", json=body, headers=a.h).json()["id"]
            chat = f"{V1}/runs/{rid}/chat"

            # 1. chat during the tool call: stored and signalled at once, delivered at the turn boundary
            assert runner.in_tool.wait(60)
            r = c.post(chat, json={"text": "make it a 5 V rail"}, headers=a.h)
            assert r.status_code == 201, r.text
            assert c.get(chat, headers=a.h).json()[0]["delivered_at"] is None  # the agent has not read it yet
            runner.chat_sent.set()
            replies = _wait(
                lambda: [m for m in c.get(chat, headers=a.h).json() if m["role"] == "agent"], "reply"
            )
            assert replies[0]["body"] == "5 V rail noted." and replies[0]["stage"] == "requirements"
            thread = c.get(chat, headers=a.h).json()
            assert [m["role"] for m in thread] == ["user", "agent"] and thread[0]["delivered_at"]
            assert runner.seen_chat == [[], ["make it a 5 V rail"]]

            # 2. nobody answers: after the timeout the recommended option is used, and logged in the notes
            qs = f"{V1}/runs/{rid}/questions"
            q = _wait(
                lambda: [x for x in c.get(qs, headers=a.h).json() if x["auto_answered"]], "the default"
            )[0]
            assert q["answers"] == {"q1": "AA"} and q["answered_by"] is None
            assert runner.second_started.wait(60)
            assert 'No answer to "Battery?" within the timeout' in runner.decisions_file
            assert "default after timeout, PM round 1, q1" in runner.decisions_file

            # 3. the late answer overrides it; the stage runs again with the change
            r = c.post(f"{V1}/questions/{q['id']}/answer", json={"answers": {"q1": "CR2032"}}, headers=a.h)
            assert r.status_code == 200, r.text
            runner.override_sent.set()
            final = _wait(
                lambda: (
                    (g := c.get(f"{V1}/runs/{rid}", headers=a.h).json())["status"] in ("succeeded", "failed")
                    and g
                ),
                "the end",
            )
            assert final["status"] == "succeeded", final
            assert len(runner.calls) == 3 and runner.calls[0][0] is None
            assert "Answers of the product person, round 1" in runner.calls[1][1]
            assert runner.calls[1][0] == runner.calls[2][0]  # both resumed the stage's session
            st = [s for s in final["stages"] if s["key"] == "requirements"][0]
            assert st["status"] == "succeeded" and st["summary"]["rerun"]["attempt"] == 2

            async def event_rows():
                db = Database.from_settings(api_settings())
                try:
                    async with db.tx(tenant_id=uuid.UUID(a.tenant_id)) as s:
                        rows = await s.execute(
                            sql(
                                "SELECT type, envelope->'data' AS d FROM outbox WHERE run_id = :r ORDER BY id"
                            ),
                            {"r": uuid.UUID(rid)},
                        )
                        return [(x.type, x.d) for x in rows.all()]
                finally:
                    await db.dispose()

            ev = asyncio.run(event_rows())
            kinds = [t for t, _ in ev]
            for want in (
                "user.message",
                "agent.message",
                "question.asked",
                "question.auto_answered",
                "question.answered",
                "run.completed",
            ):
                assert want in kinds, kinds
            assert kinds.index("question.auto_answered") < kinds.index("question.answered")
            assert [d for t, d in ev if t == "question.answered"][-1]["overrides_default"] is True
