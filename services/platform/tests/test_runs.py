"""PgRunStore: creation, state machine, stages, artifacts, manifest, project state (ADR 0001, 3; issue #7)."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from hwplatform.contracts import (
    TRANSITIONS,
    ArtifactRef,
    InvalidTransition,
    RunStatus,
    StageRecord,
    StageStatus,
)
from hwplatform.runs import RunNotFound

pytestmark = pytest.mark.integration

STATES = [s.value for s in RunStatus]


def ref(tag: str, size: int = 5, mt: str = "text/plain", name: str = "") -> ArtifactRef:
    return ArtifactRef((tag.encode().hex() * 64)[:64], size, mt, name)


def test_create_assigns_uuid7_and_queued_event(run_env):
    async def go():
        async with run_env("create") as env:
            run = await env.new_run()
            u = uuid.UUID(run.run_id)
            assert u.version == 7
            assert run.status == RunStatus.queued and run.verdict is None and run.started_at is None
            assert run.tenant_id == str(env.tenant_id) and run.project_id == str(env.project_id)
            assert run.workflow_id == f"run-{run.run_id}"
            assert run.options == {"until": "sim"} and run.options_hash == "abc123abc123"
            assert run.design_version.design.sha256 and run.design_version.requirements is None
            ev = await env.events(run.run_id)
            assert [(e["type"], e["seq"]) for e in ev] == [("run.queued", 1)]
            env_ = json.loads(ev[0]["envelope"]) if isinstance(ev[0]["envelope"], str) else ev[0]["envelope"]
            assert env_["specversion"] == "1.0" and env_["type"] == "run.queued"
            assert env_["tenantid"] == str(env.tenant_id) and env_["runid"] == run.run_id and env_["seq"] == 1
            assert env_["subject"] == f"runs/{run.run_id}"
            assert env_["dataschema"] == "hwflow.events/run.queued/1"
            assert env_["data"]["design_version_id"] == run.design_version.id
            assert ev[0]["subject"] == f"hwflow.{env.tenant_id}.run.queued"
            assert uuid.UUID(env_["id"]).version == 7 and env_["id"] == str(ev[0]["event_id"])

    asyncio.run(go())


def test_run_ids_sort_by_time_and_list_paginates_newest_first(run_env):
    async def go():
        async with run_env("list") as env:
            dv = await env.design_version()
            runs = [await env.new_run(dv) for _ in range(5)]
            ids = [r.run_id for r in runs]
            assert ids == sorted(ids)
            page1 = await env.store.list(str(env.project_id), limit=2)
            assert [r.run_id for r in page1] == [ids[4], ids[3]]
            page2 = await env.store.list(str(env.project_id), limit=10, before=page1[-1].run_id)
            assert [r.run_id for r in page2] == [ids[2], ids[1], ids[0]]

    asyncio.run(go())


def test_idempotency_key_returns_first_run(run_env):
    async def go():
        async with run_env("idem") as env:
            dv = await env.design_version()
            a = await env.new_run(dv, idempotency_key="k1")
            b = await env.new_run(dv, idempotency_key="k1")
            c = await env.new_run(dv, idempotency_key="k2")
            assert a.run_id == b.run_id != c.run_id
            queued = [e for e in await env.events() if e["type"] == "run.queued"]
            assert len(queued) == 2  # the replay emitted nothing

    asyncio.run(go())


def test_legal_lifecycle_emits_gapless_events(run_env):
    async def go():
        async with run_env("life") as env:
            r = await env.new_run()
            rid = r.run_id
            r = await env.store.transition(rid, RunStatus.running)
            assert r.started_at is not None
            r = await env.store.transition(
                rid, RunStatus.waiting, event_data={"reason": "question", "question_id": "q1"}
            )
            await env.store.transition(rid, RunStatus.running)
            r = await env.store.transition(rid, RunStatus.succeeded, verdict="PASS")
            assert r.status == RunStatus.succeeded and r.verdict == "PASS" and r.ended_at is not None
            ev = await env.events(rid)
            assert [(e["type"], e["seq"]) for e in ev] == [
                ("run.queued", 1),
                ("run.started", 2),
                ("run.waiting", 3),
                ("run.resumed", 4),
                ("run.completed", 5),
            ]
            waiting = ev[2]["envelope"]
            waiting = json.loads(waiting) if isinstance(waiting, str) else waiting
            assert waiting["data"]["reason"] == "question" and waiting["data"]["question_id"] == "q1"
            done = ev[4]["envelope"]
            done = json.loads(done) if isinstance(done, str) else done
            assert done["data"]["status"] == "succeeded" and done["data"]["verdict"] == "PASS"

    asyncio.run(go())


def test_failed_and_cancelled_carry_error_and_actor(run_env):
    async def go():
        async with run_env("term") as env:
            f = await env.new_run()
            await env.store.transition(f.run_id, RunStatus.running)
            f = await env.store.transition(
                f.run_id,
                RunStatus.failed,
                error={"type": "Timeout", "message": "too slow", "retryable": False},
            )
            assert f.error["type"] == "Timeout" and f.verdict is None
            c = await env.new_run()
            await env.store.transition(c.run_id, RunStatus.cancelled, event_data={"by": "user-1"})
            envs = [
                json.loads(e["envelope"]) if isinstance(e["envelope"], str) else e["envelope"]
                for e in await env.events()
            ]
            by_type = {e["type"]: e for e in envs}
            assert by_type["run.failed"]["data"]["error"] == {"type": "Timeout", "message": "too slow"}
            assert by_type["run.cancelled"]["data"]["by"] == "user-1"

    asyncio.run(go())


def test_every_illegal_transition_is_refused_and_leaves_no_trace(run_env):
    """All 6x6 (from, to) pairs: legal ones follow TRANSITIONS, the rest raise InvalidTransition."""

    async def reach(env, target: str):
        r = await env.new_run()
        path = {
            "queued": [],
            "running": ["running"],
            "waiting": ["running", "waiting"],
            "succeeded": ["running", "succeeded"],
            "failed": ["failed"],
            "cancelled": ["cancelled"],
        }[target]
        for step in path:
            await env.store.transition(r.run_id, RunStatus(step))
        return r.run_id

    async def go():
        async with run_env("fsm") as env:
            for frm in STATES:
                for to in STATES:
                    rid = await reach(env, frm)
                    before = len(await env.events(rid))
                    if to in TRANSITIONS.get(frm, set()):
                        run = await env.store.transition(rid, RunStatus(to))
                        assert run.status == RunStatus(to)
                        assert len(await env.events(rid)) == before + 1, (frm, to)
                    else:
                        with pytest.raises(InvalidTransition):
                            await env.store.transition(rid, RunStatus(to))
                        assert (await env.store.get(rid)).status == RunStatus(frm), (frm, to)
                        assert len(await env.events(rid)) == before, (frm, to)

    asyncio.run(go())


def test_transition_errors(run_env):
    async def go():
        async with run_env("errs") as env:
            r = await env.new_run()
            with pytest.raises(ValueError):
                await env.store.transition(r.run_id, RunStatus.running, verdict="MAYBE")
            with pytest.raises(RunNotFound):
                await env.store.transition(str(uuid.uuid4()), RunStatus.running)
            assert (await env.store.get(r.run_id)).status == RunStatus.queued

    asyncio.run(go())


def test_concurrent_transitions_only_one_wins(run_env):
    async def go():
        async with run_env("race") as env:
            r = await env.new_run()
            await env.store.transition(r.run_id, RunStatus.running)
            outcomes = await asyncio.gather(
                *[
                    env.store.transition(r.run_id, t, verdict="PASS" if t == RunStatus.succeeded else None)
                    for t in (RunStatus.succeeded, RunStatus.failed, RunStatus.cancelled, RunStatus.succeeded)
                ],
                return_exceptions=True,
            )
            ok = [o for o in outcomes if not isinstance(o, Exception)]
            assert len(ok) == 1 and all(
                isinstance(o, InvalidTransition) for o in outcomes if isinstance(o, Exception)
            )
            terminal = [
                e
                for e in await env.events(r.run_id)
                if e["type"] in ("run.completed", "run.failed", "run.cancelled")
            ]
            assert len(terminal) == 1

    asyncio.run(go())


def test_stages_upsert_idempotent_and_events(run_env):
    async def go():
        async with run_env("stages") as env:
            r = await env.new_run()
            rid = r.run_id
            t0 = datetime.now(UTC)
            start = StageRecord("check", "check", 0, StageStatus.running, started_at=t0)
            await env.store.upsert_stage(rid, start)
            await env.store.upsert_stage(rid, start)  # replay: no second event
            done = StageRecord(
                "check",
                "check",
                0,
                StageStatus.succeeded,
                verdict="PASS",
                cached=True,
                cache_key="c" * 64,
                started_at=t0,
                ended_at=t0 + timedelta(seconds=2),
                seconds=2.0,
                summary={"n": 3},
                result=ref("res", 7, "application/json"),
                outputs={"board": ref("brd", 9, "x/y", "board")},
            )
            await env.store.upsert_stage(rid, done)
            await env.store.upsert_stage(rid, done)
            await env.store.upsert_stage(rid, start)  # a late replay of "running" must not undo the stage
            fail = StageRecord(
                "sim",
                "sim",
                1,
                StageStatus.failed,
                error={"type": "X", "message": "m", "retryable": True},
                attempt=1,
            )
            await env.store.upsert_stage(rid, fail)
            retry = StageRecord("sim", "sim", 1, StageStatus.running, attempt=2)
            await env.store.upsert_stage(rid, retry)
            skip = StageRecord("fab", "fab", 2, StageStatus.skipped, summary={"reason": "until"})
            await env.store.upsert_stage(rid, skip)

            run = await env.store.get(rid)
            assert [s.key for s in run.stages] == ["check", "sim", "fab"]
            c = run.stages[0]
            assert (
                c.status == StageStatus.succeeded
                and c.verdict == "PASS"
                and c.cached
                and c.cache_key == "c" * 64
            )
            assert c.seconds == 2.0 and c.summary == {"n": 3}
            assert c.result is not None and c.result.sha256 == ref("res").sha256 and c.result.size == 7
            assert c.outputs["board"] == ref("brd", 9, "x/y", "board")
            assert run.stages[1].status == StageStatus.running and run.stages[1].attempt == 2

            types = [(e["type"], e["seq"]) for e in await env.events(rid)]
            assert types == [
                ("run.queued", 1),
                ("stage.started", 2),
                ("stage.completed", 3),
                ("stage.failed", 4),
                ("stage.started", 5),
                ("stage.skipped", 6),
            ]
            with pytest.raises(RunNotFound):
                await env.store.upsert_stage(str(uuid.uuid4()), start)

    asyncio.run(go())


def test_artifacts_events_and_manifest(run_env, s3_store):
    async def go():
        async with run_env("manifest") as env:
            r = await env.new_run()
            rid = r.run_id
            await env.store.transition(rid, RunStatus.running)
            await env.store.upsert_stage(
                rid,
                StageRecord(
                    "layout_select", "layout_select", 5, StageStatus.succeeded, verdict="PASS", seconds=1.5
                ),
            )
            await env.store.upsert_stage(
                rid, StageRecord("check", "check", 0, StageStatus.succeeded, verdict="PASS")
            )
            await env.store.set_artifacts(
                rid,
                {
                    "REPORT.md": ref("rep", 11, "text/markdown"),
                    "layout_select/board": ref("brd", 30),
                    "status.json": ref("st", 4),
                },
            )
            await env.store.set_artifacts(
                rid, {"REPORT.md": ref("rep", 11, "text/markdown")}
            )  # same sha: no event
            await env.store.transition(rid, RunStatus.succeeded, verdict="FAIL")

            store = s3_store(env.tenant_id)
            mref = await env.store.store_manifest(rid, store)
            assert mref.media_type == "application/json" and store.exists(mref.sha256)
            with store.open(mref.sha256) as f:
                m = json.loads(f.read())

            created = [e for e in await env.events(rid) if e["type"] == "artifact.created"]
            names = sorted(
                json.loads(e["envelope"])["data"]["name"]
                if isinstance(e["envelope"], str)
                else e["envelope"]["data"]["name"]
                for e in created
            )
            assert names == [
                "REPORT.md",
                "layout_select/board",
                "manifest.json",
            ]  # status.json is not user-visible

            assert m["schema"] == "hwflow.run/1"
            assert (
                m["run_id"] == rid
                and m["kind"] == "build"
                and m["status"] == "succeeded"
                and m["verdict"] == "FAIL"
            )
            assert [s["key"] for s in m["stages"]] == ["check", "layout_select"]  # seq order
            assert m["artifacts"]["REPORT.md"] == {
                "sha256": ref("rep").sha256,
                "size": 11,
                "media_type": "text/markdown",
                "name": "REPORT.md",
            }
            for k in ("created_at", "started_at", "ended_at"):
                assert m[k].endswith("Z"), k
            assert m["design_version"]["design"]["sha256"] and m["design_version"]["requirements"] is None
            assert m["stages"][1]["status"] == "succeeded" and m["stages"][1]["seconds"] == 1.5
            # the full Run round-trips: the stored manifest of the final state is rendered from the rows
            assert (await env.store.get(rid)).artifacts["manifest.json"].sha256 == mref.sha256
            assert set(m) >= {
                "run_id",
                "kind",
                "tenant_id",
                "project_id",
                "design_version",
                "status",
                "verdict",
                "options",
                "options_hash",
                "settings",
                "code",
                "tools",
                "stages",
                "artifacts",
                "created_at",
                "error",
                "parent_run_id",
                "workflow_id",
            }

    asyncio.run(go())


def test_project_state_older_run_never_overwrites_newer(run_env):
    async def go():
        async with run_env("pstate") as env:
            dv = await env.design_version()
            old, new = await env.new_run(dv), await env.new_run(dv)
            pid = str(env.project_id)
            await env.store.set_project_state(pid, {"board": ref("b2", 2), "fab": ref("f2", 3)}, new.run_id)
            await env.store.set_project_state(
                pid, {"board": ref("b1", 1)}, old.run_id
            )  # late writer: ignored
            st = await env.store.project_state(pid)
            assert st["board"].sha256 == ref("b2").sha256 and st["board"].size == 2 and st["fab"].size == 3
            await env.store.set_project_state(pid, {"board": ref("b3", 4)}, new.run_id)  # same run may update
            assert (await env.store.project_state(pid))["board"].sha256 == ref("b3").sha256

    asyncio.run(go())


def test_design_versions_sequence_and_idempotent_content(run_env):
    async def go():
        async with run_env("dv") as env:
            s = env.store
            a = await s.create_design_version(
                env.project_id, content_sha256="a" * 64, design=ref("d1"), project=ref("p1")
            )
            b = await s.create_design_version(
                env.project_id,
                content_sha256="b" * 64,
                design=ref("d2"),
                project=ref("p2"),
                requirements=ref("rq"),
                source="agent",
                message="m",
            )
            again = await s.create_design_version(
                env.project_id, content_sha256="a" * 64, design=ref("d1"), project=ref("p1")
            )
            assert again.id == a.id != b.id
            assert b.requirements is not None and b.requirements.sha256 == ref("rq").sha256
            ev = [e for e in await env.events() if e["type"] == "design_version.created"]
            assert len(ev) == 2 and all(e["run_id"] is None and e["seq"] is None for e in ev)
            with pytest.raises(LookupError):
                await s.create_design_version(
                    uuid.uuid4(), content_sha256="c" * 64, design=ref("d"), project=ref("p")
                )

    asyncio.run(go())


def test_append_event_seq_is_gapless_under_concurrency(run_env):
    async def go():
        async with run_env("seq") as env:
            r = await env.new_run()
            seqs = await asyncio.gather(
                *[
                    env.store.append_event(
                        r.run_id, "stage.progress", {"key": "layout_attempt.1", "line": f"l{i}"}
                    )
                    for i in range(20)
                ]
            )
            assert sorted(seqs) == list(range(2, 22))  # run.queued took 1
            with pytest.raises(ValueError):
                await env.store.append_event(r.run_id, "not.a.type", {})

    asyncio.run(go())
