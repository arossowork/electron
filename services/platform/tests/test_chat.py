"""Agent chat and defaulted answers over the HTTP API and the stores (#54), with a fake workflow gateway.

Real Postgres (RLS applies). Chat: store, `chat` signal, turn-boundary delivery, `user.message` / `agent.message`
events, tenant isolation. Defaulted answers: `question.auto_answered`, `question.waiting_critical`, a late answer
that overrides (`question.answered` with `overrides_default`)."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from hwflow.runs import RunStatus
from sqlalchemy import text

from hwplatform.chat import ChatStore
from hwplatform.contracts import ChatSignal
from hwplatform.db import Database
from hwplatform.questions import QuestionNotOpen, QuestionStore
from hwplatform.runs import PgRunStore

from conftest import APP_URL, Account

pytestmark = pytest.mark.integration

V1 = "/api/v1"
DESIGN = b"name: demo\nparts:\n  - ref: R1\n"
QS = [
    {
        "id": "q1",
        "text": "Battery?",
        "options": ["CR2032", "AA"],
        "recommended": "AA",
        "critical": False,
        "default": "AA",
    }
]


@pytest.fixture
def run_async(api_settings):
    def go(fn):
        async def main():
            db = Database.from_settings(api_settings())
            try:
                return await fn(db)
            finally:
                await db.dispose()

        return asyncio.run(main())

    return go


@pytest.fixture
def world(api):
    a = Account(api, "chat")
    p = a.project("demo")
    r = api.post(
        f"{V1}/projects/{p['id']}/design-versions",
        files=[("design", ("design.yaml", DESIGN, "application/yaml"))],
        headers=a.h,
    )
    assert r.status_code == 201, r.text
    return a, p, r.json()


def start(api, a, p, dv, kind="design"):
    body = {
        "kind": kind,
        "design_version_id": dv["id"],
        "options": {"brief": "x"} if kind == "design" else {},
    }
    r = api.post(f"{V1}/projects/{p['id']}/runs", json=body, headers=a.h)
    assert r.status_code in (200, 202), r.text
    return r.json()


def events(run_async, a, run_id) -> list[tuple[str, dict]]:
    async def q(db):
        async with db.tx(tenant_id=uuid.UUID(a.tenant_id)) as s:
            rows = (
                await s.execute(
                    text("SELECT type, envelope->'data' AS d FROM outbox WHERE run_id = :r ORDER BY id"),
                    {"r": uuid.UUID(run_id)},
                )
            ).all()
        return [(r.type, r.d) for r in rows]

    return run_async(q)


# --- chat ----------------------------------------------------------------------------------------------


def test_post_stores_signals_and_lists(api, gateway, world, run_async):
    a, p, dv = world
    run = start(api, a, p, dv)
    r = api.post(f"{V1}/runs/{run['id']}/chat", json={"text": "  use a 5 V rail  "}, headers=a.h)
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["role"] == "user" and m["body"] == "use a 5 V rail" and m["user_id"] == a.user_id
    assert m["delivered_at"] is None and m["stage"] is None
    wf, sig = gateway.signals[-1]
    assert wf == run["workflow_id"] and sig == ChatSignal(message_id=m["id"])
    assert [x["id"] for x in api.get(f"{V1}/runs/{run['id']}/chat", headers=a.h).json()] == [m["id"]]
    ev = [d for t, d in events(run_async, a, run["id"]) if t == "user.message"]
    assert ev == [{"message_id": m["id"], "text": "use a 5 V rail", "user_id": a.user_id}]


def test_post_validation_and_run_state(api, gateway, world, run_async):
    a, p, dv = world
    run = start(api, a, p, dv)
    url = f"{V1}/runs/{run['id']}/chat"
    assert api.post(url, json={"text": ""}, headers=a.h).status_code == 422
    assert api.post(url, json={"text": "x" * 4001}, headers=a.h).status_code == 422
    assert api.post(url, json={}, headers=a.h).status_code == 422
    build = start(api, a, p, dv, kind="build")
    r = api.post(f"{V1}/runs/{build['id']}/chat", json={"text": "hi"}, headers=a.h)
    assert r.status_code == 409 and "design run" in r.json()["detail"]
    gateway.fail = True  # nobody can be told: nothing is kept for the agent to never receive
    assert api.post(url, json={"text": "hi"}, headers=a.h).status_code == 503
    assert api.get(url, headers=a.h).json() == []
    gateway.fail = False
    n = len(gateway.signals)
    run_async(lambda db: PgRunStore(db, a.tenant_id).transition(run["id"], RunStatus.cancelled))
    r = api.post(url, json={"text": "hi"}, headers=a.h)
    assert r.status_code == 409 and len(gateway.signals) == n
    assert api.get(url, headers=a.h).status_code == 200  # the history stays readable


def test_turn_boundary_delivery_and_replies(api, world, run_async):
    """take_pending hands each message over once, in order; a failed session can release them; the agent's
    reply is a row and an `agent.message` event."""
    a, p, dv = world
    run = start(api, a, p, dv)
    rid = run["id"]

    async def go(db):
        cs = ChatStore(db, a.tenant_id)
        m1 = await cs.post_user(rid, "first", a.user_id)
        m2 = await cs.post_user(rid, "second", None)
        got = await cs.take_pending(rid)
        assert [m.body for m in got] == ["first", "second"] and all(m.delivered_at for m in got)
        assert await cs.take_pending(rid) == []  # delivered once
        await cs.release([m2.id])  # the session failed before the agent saw the second
        assert [m.id for m in await cs.take_pending(rid)] == [m2.id]
        m3 = await cs.post_user(rid, "third", a.user_id)
        reply = await cs.post_agent(rid, "Done: both.", "circuit", [m1.id, m3.id])
        assert reply.role == "agent" and reply.stage == "circuit"
        return [m.role + ":" + m.body for m in await cs.list_for_run(rid)]

    assert run_async(go) == ["user:first", "user:second", "user:third", "agent:Done: both."]
    ev = [(t, d) for t, d in events(run_async, a, rid) if t in ("user.message", "agent.message")]
    assert [t for t, _ in ev] == ["user.message"] * 3 + ["agent.message"]
    assert (
        ev[-1][1]["text"] == "Done: both."
        and ev[-1][1]["stage"] == "circuit"
        and len(ev[-1][1]["reply_to"]) == 2
    )


def test_chat_is_tenant_isolated(api, gateway, world, run_async):
    a, p, dv = world
    run = start(api, a, p, dv)
    api.post(f"{V1}/runs/{run['id']}/chat", json={"text": "secret plan"}, headers=a.h)
    b = Account(api, "intruder")
    n = len(gateway.signals)
    assert api.get(f"{V1}/runs/{run['id']}/chat", headers=b.h).status_code == 404
    assert api.post(f"{V1}/runs/{run['id']}/chat", json={"text": "hijack"}, headers=b.h).status_code == 404
    assert len(gateway.signals) == n
    assert [x["body"] for x in api.get(f"{V1}/runs/{run['id']}/chat", headers=a.h).json()] == ["secret plan"]
    # the store of B sees nothing of A's run, and raw SQL as the app role is held back by RLS
    assert run_async(lambda db: ChatStore(db, b.tenant_id).list_for_run(run["id"])) == []

    async def raw():
        conn = await asyncpg.connect(APP_URL)
        try:
            assert await conn.fetchval("SELECT count(*) FROM chat_messages") == 0  # no tenant context
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", b.tenant_id)
                assert await conn.fetchval("SELECT count(*) FROM chat_messages") == 0
                assert (
                    await conn.execute(
                        "UPDATE chat_messages SET body = 'x' WHERE run_id = $1", uuid.UUID(run["id"])
                    )
                    == "UPDATE 0"
                )
                with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                    await conn.execute(
                        "INSERT INTO chat_messages (id, tenant_id, run_id, role, body) VALUES ($1, $2, $3, 'user', 'x')",
                        uuid.uuid4(),
                        uuid.UUID(a.tenant_id),
                        uuid.UUID(run["id"]),
                    )
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", a.tenant_id)
                assert await conn.fetchval("SELECT count(*) FROM chat_messages") == 1
        finally:
            await conn.close()

    asyncio.run(raw())


def test_chat_needs_a_login(api, world):
    a, p, dv = world
    rid = uuid.uuid4()
    assert api.get(f"{V1}/runs/{rid}/chat").status_code == 401
    assert api.post(f"{V1}/runs/{rid}/chat", json={"text": "x"}).status_code == 401


# --- defaulted answers ---------------------------------------------------------------------------------


def ask(run_async, a, run_id, questions=QS):
    async def go(db):
        await PgRunStore(db, a.tenant_id).transition(run_id, RunStatus.running)
        q = await QuestionStore(db, a.tenant_id).ask(run_id, "requirements", questions)
        await PgRunStore(db, a.tenant_id).transition(
            run_id, RunStatus.waiting, event_data={"reason": "question"}
        )
        return q

    return run_async(go)


def test_auto_answer_then_override(api, gateway, world, run_async):
    a, p, dv = world
    run = start(api, a, p, dv)
    q = ask(run_async, a, run["id"])
    done = run_async(lambda db: QuestionStore(db, a.tenant_id).auto_answer(q.id, {"q1": "AA"}))
    assert done.status == "answered" and done.auto_answered and done.answered_by is None
    assert done.answers == done.default_answers == {"q1": "AA"}
    shown = api.get(f"{V1}/runs/{run['id']}/questions", headers=a.h).json()[0]
    assert shown["status"] == "answered" and shown["auto_answered"] is True and shown["overridden_at"] is None

    # a late answer overrides: recorded, signalled with overrides_default, the badge goes away
    ans = f"{V1}/questions/{q.id}/answer"
    r = api.post(ans, json={"answers": {"q1": "CR2032"}}, headers=a.h)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["answers"] == {"q1": "CR2032"} and got["auto_answered"] is False and got["overridden_at"]
    assert got["default_answers"] == {"q1": "AA"} and got["answered_by"] == a.user_id
    wf, sig = gateway.signals[-1]
    assert wf == run["workflow_id"] and sig.overrides_default is True and sig.answers == {"q1": "CR2032"}
    ev = dict(events(run_async, a, run["id"]))  # type -> data (the last of each)
    assert ev["question.auto_answered"] == {
        "question_id": q.id,
        "stage": "requirements",
        "answers": {"q1": "AA"},
    }
    assert ev["question.answered"]["overrides_default"] is True
    assert ev["question.answered"]["default_answers"] == {"q1": "AA"} and ev["question.answered"][
        "answers"
    ] == {"q1": "CR2032"}
    # only one override: the question is a person's answer now
    assert api.post(ans, json={"answers": {"q1": "AA"}}, headers=a.h).status_code == 409
    assert len([s for _, s in gateway.signals if getattr(s, "overrides_default", False)]) == 1


def test_a_person_who_answers_first_blocks_the_default(api, gateway, world, run_async):
    a, p, dv = world
    run = start(api, a, p, dv)
    q = ask(run_async, a, run["id"])
    assert (
        api.post(f"{V1}/questions/{q.id}/answer", json={"answers": {"q1": "AA"}}, headers=a.h).status_code
        == 200
    )
    assert run_async(lambda db: QuestionStore(db, a.tenant_id).auto_answer(q.id, {"q1": "CR2032"})) is None
    got = api.get(f"{V1}/runs/{run['id']}/questions", headers=a.h).json()[0]
    assert got["auto_answered"] is False and got["answers"] == {"q1": "AA"}
    # a question a person answered cannot be "overridden"
    assert (
        api.post(f"{V1}/questions/{q.id}/answer", json={"answers": {"q1": "CR2032"}}, headers=a.h).status_code
        == 409
    )

    async def direct(db):
        with pytest.raises(QuestionNotOpen):
            await QuestionStore(db, a.tenant_id).override(q.id, {"q1": "x"}, a.user_id)

    run_async(direct)


def test_override_signal_failure_restores_the_default(api, gateway, world, run_async):
    a, p, dv = world
    run = start(api, a, p, dv)
    q = ask(run_async, a, run["id"])
    run_async(lambda db: QuestionStore(db, a.tenant_id).auto_answer(q.id, {"q1": "AA"}))
    gateway.fail = True
    ans = f"{V1}/questions/{q.id}/answer"
    assert api.post(ans, json={"answers": {"q1": "CR2032"}}, headers=a.h).status_code == 503
    got = api.get(f"{V1}/runs/{run['id']}/questions", headers=a.h).json()[0]
    assert got["auto_answered"] is True and got["answers"] == {"q1": "AA"} and got["overridden_at"] is None
    gateway.fail = False
    assert api.post(ans, json={"answers": {"q1": "CR2032"}}, headers=a.h).status_code == 200


def test_override_after_the_run_ended_is_409_and_cross_tenant_404(api, gateway, world, run_async):
    a, p, dv = world
    run = start(api, a, p, dv)
    q = ask(run_async, a, run["id"])
    run_async(lambda db: QuestionStore(db, a.tenant_id).auto_answer(q.id, {"q1": "AA"}))
    b = Account(api, "other")
    assert (
        api.post(f"{V1}/questions/{q.id}/answer", json={"answers": {"q1": "CR2032"}}, headers=b.h).status_code
        == 404
    )
    run_async(lambda db: PgRunStore(db, a.tenant_id).transition(run["id"], RunStatus.cancelled))
    n = len(gateway.signals)
    r = api.post(f"{V1}/questions/{q.id}/answer", json={"answers": {"q1": "CR2032"}}, headers=a.h)
    assert r.status_code == 409 and len(gateway.signals) == n


def test_waiting_critical_event_only_for_an_open_question(api, world, run_async):
    a, p, dv = world
    run = start(api, a, p, dv)
    q = ask(run_async, a, run["id"], [{**QS[0], "critical": True}])
    assert run_async(lambda db: QuestionStore(db, a.tenant_id).waiting_critical(q.id)) is True
    ev = [d for t, d in events(run_async, a, run["id"]) if t == "question.waiting_critical"]
    assert ev == [{"question_id": q.id, "stage": "requirements"}]
    assert (
        api.post(f"{V1}/questions/{q.id}/answer", json={"answers": {"q1": "AA"}}, headers=a.h).status_code
        == 200
    )
    assert run_async(lambda db: QuestionStore(db, a.tenant_id).waiting_critical(q.id)) is False
