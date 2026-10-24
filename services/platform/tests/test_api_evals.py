"""Evaluations through the HTTP API (#19): start, list, get, report, promote a baseline, roles, tenant isolation.

Real Postgres and S3; Temporal is the fake gateway of conftest (the workflow itself is tested in test_workflow_eval).
"""

from __future__ import annotations

import json

import pytest
from hwflow.runs import RunStatus

from hwplatform.contracts import EvalInput
from hwplatform.runs import PgRunStore
from hwplatform.storage import S3ArtifactStore

from conftest import S3_BUCKET, Account

pytestmark = pytest.mark.integration

V1 = "/api/v1"


def fixture(fid="tiny", **kw):
    return {
        "id": fid,
        "brief": "A small battery powered night light.",
        "hidden_spec": "9 V battery, one LED, 20 mA.",
        "planted_conflicts": [{"id": "c1", "desc": "x", "keywords": [["led"], ["battery"]]}],
        "expected_requirements": [["led"]],
        "limits": {"area_mm2": 2000, "cost_usd": 5},
        **kw,
    }


BASELINE = {"tiny::default": {"mean": 80.0, "spread": 0.0, "n": 1}}


def start(api, acct, body=None, headers=None, **kw):
    body = body or {"suite": "smoke", "fixtures": [fixture()], **kw}
    return api.post(f"{V1}/evals", json=body, headers=headers or acct.h)


@pytest.fixture
def owner(api):
    return Account(api, "eval-owner")


def finish(run_async, api, acct, eval_id, doc, status=RunStatus.succeeded):
    """What the workflow's finish_eval does: store eval.json and REPORT.md, then end the run."""

    async def go(db):
        st = PgRunStore(db, acct.tenant_id)
        store = S3ArtifactStore(api.app.state.s3, S3_BUCKET, acct.tenant_id)
        j = store.put_bytes(json.dumps(doc).encode(), media_type="application/json", name="eval.json")
        m = store.put_bytes(b"# Eval smoke\n\nverdict: PASS\n", media_type="text/markdown", name="REPORT.md")
        await st.transition(eval_id, RunStatus.running)
        await st.set_artifacts(eval_id, {"eval.json": j, "REPORT.md": m})
        await st.transition(eval_id, status, verdict="PASS")

    run_async(go)


@pytest.fixture
def run_async(api_settings):
    import asyncio

    from hwplatform.db import Database

    def go(fn):
        async def main():
            db = Database.from_settings(api_settings())
            try:
                return await fn(db)
            finally:
                await db.dispose()

        return asyncio.run(main())

    return go


def test_start_calls_the_gateway_with_the_adr_workflow_id(api, gateway, owner):
    r = start(api, owner, repetitions=2, models=["sonnet", "sonnet"], budget_usd=3)
    assert r.status_code == 202, r.text
    out = r.json()
    assert out["kind"] == "eval" and out["status"] == "queued" and out["suite"] == "smoke"
    assert out["children"] == []
    wf, arg = gateway.started[-1]
    assert wf == f"eval-{owner.tenant_id}-{out['id']}" and out["workflow_id"] == wf
    assert isinstance(arg, EvalInput)
    assert arg.suite == "smoke" and arg.models == ["sonnet"] and arg.repetitions == 2 and arg.budget_usd == 3
    assert [f["id"] for f in arg.fixtures] == ["tiny"] and arg.baseline is None
    assert arg.ctx.run_id == out["id"] and arg.max_parallel == 1
    got = api.get(f"{V1}/evals/{out['id']}", headers=owner.h).json()
    assert got["id"] == out["id"] and got["options"]["fixtures"] == ["tiny"]


def test_max_parallel_is_capped_by_the_tenant_quota(api, gateway, owner):
    assert start(api, owner, max_parallel=20).status_code == 202
    assert gateway.started[-1][1].max_parallel == 2  # default quota: 2 concurrent runs


def test_validation(api, gateway, owner, make_api):
    assert start(api, owner, {"suite": "s", "fixtures": []}).status_code == 422
    assert start(api, owner, {"suite": "bad suite!", "fixtures": [fixture()]}).status_code == 422
    assert start(api, owner, {"suite": "s", "fixtures": ["no-such-fixture"]}).status_code == 422
    assert start(api, owner, {"suite": "s", "fixtures": [fixture(), fixture()]}).status_code == 422
    assert (
        start(api, owner, {"suite": "s", "fixtures": [fixture(mode="from_requirements")]}).status_code == 422
    )
    assert start(api, owner, {"suite": "s", "fixtures": [fixture()], "baseline": {"x": 1}}).status_code == 422
    assert start(api, owner, {"suite": "s", "fixtures": [fixture()], "models": ["a b"]}).status_code == 422
    assert not gateway.started
    small = make_api(eval_max_trials=3)
    a = Account(small, "eval-cap")
    r = start(small, a, repetitions=4)
    assert r.status_code == 422 and "trials" in r.json()["detail"]


def test_member_cannot_start_or_promote_but_can_read(api, gateway, owner, run_async):
    m = Account(api, "eval-member")
    h = m.join(owner, "member")
    assert start(api, owner, headers=h).status_code == 403
    eid = start(api, owner).json()["id"]
    assert api.get(f"{V1}/evals/{eid}", headers=h).status_code == 200
    assert len(api.get(f"{V1}/evals", headers=h).json()["items"]) == 1
    finish(run_async, api, owner, eid, {"schema": "hwflow.eval/1", "aggregate": BASELINE})
    assert api.post(f"{V1}/evals/{eid}/promote-baseline", json={}, headers=h).status_code == 403
    assert api.get(f"{V1}/evals/{eid}/report", headers=h).status_code == 200


def test_quota_429_drops_the_orphan_project(api, gateway, owner):
    assert start(api, owner).status_code == 202
    assert start(api, owner, suite="s2").status_code == 202
    projects = len(api.get(f"{V1}/projects", headers=owner.h).json())
    r = start(api, owner, suite="s3")
    assert r.status_code == 429 and r.json()["code"] == "quota_exceeded"
    assert len(api.get(f"{V1}/projects", headers=owner.h).json()) == projects
    assert len(gateway.started) == 2


def test_start_failure_marks_the_run_failed(api, gateway, owner):
    gateway.fail = True
    r = start(api, owner)
    assert r.status_code == 503
    items = api.get(f"{V1}/evals", headers=owner.h).json()["items"]
    assert [i["status"] for i in items] == ["failed"]


def test_idempotency_key(api, gateway, owner):
    h = {**owner.h, "Idempotency-Key": "k1"}
    a = start(api, owner, headers=h)
    b = start(api, owner, headers=h)
    assert a.status_code == 202 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    assert len(gateway.started) == 1


def test_list_filter_and_page(api, gateway, owner, run_async):
    ids = [start(api, owner, suite=f"s{i}").json()["id"] for i in range(2)]
    finish(run_async, api, owner, ids[0], {"schema": "hwflow.eval/1", "aggregate": BASELINE})
    all_ = api.get(f"{V1}/evals", headers=owner.h).json()
    assert [i["id"] for i in all_["items"]] == ids[::-1] and all_["items"][0]["suite"] == "s1"
    done = api.get(f"{V1}/evals?status=succeeded", headers=owner.h).json()["items"]
    assert [i["id"] for i in done] == [ids[0]]
    assert [i["id"] for i in api.get(f"{V1}/evals?suite=s1", headers=owner.h).json()["items"]] == [ids[1]]
    p1 = api.get(f"{V1}/evals?limit=1", headers=owner.h).json()
    assert len(p1["items"]) == 1 and p1["next_cursor"] == ids[1]
    p2 = api.get(f"{V1}/evals?limit=1&cursor={p1['next_cursor']}", headers=owner.h).json()
    assert [i["id"] for i in p2["items"]] == [ids[0]] and p2["next_cursor"] is None


def test_report_and_result_download(api, gateway, owner, run_async):
    eid = start(api, owner).json()["id"]
    assert api.get(f"{V1}/evals/{eid}/report", headers=owner.h).status_code == 404
    doc = {"schema": "hwflow.eval/1", "aggregate": BASELINE, "verdict": "PASS"}
    finish(run_async, api, owner, eid, doc)
    rep = api.get(f"{V1}/evals/{eid}/report", headers=owner.h)
    assert rep.status_code == 200 and rep.content.startswith(b"# Eval smoke")
    assert rep.headers["content-type"].startswith("text/markdown")
    assert api.get(f"{V1}/evals/{eid}/result", headers=owner.h).json() == doc
    # the generic run endpoint serves the same report
    assert api.get(f"{V1}/runs/{eid}/report", headers=owner.h).content == rep.content


def test_promote_baseline_is_explicit_and_becomes_the_default(api, gateway, owner, run_async):
    e1 = start(api, owner).json()["id"]
    r = api.post(f"{V1}/evals/{e1}/promote-baseline", json={}, headers=owner.h)
    assert r.status_code == 409  # not finished
    doc = {"schema": "hwflow.eval/1", "aggregate": BASELINE}
    finish(run_async, api, owner, e1, doc)
    # a finished evaluation is not a baseline until somebody promotes it
    e2 = start(api, owner).json()["id"]
    assert gateway.started[-1][1].baseline is None
    run_async(lambda db: PgRunStore(db, owner.tenant_id).transition(e2, RunStatus.cancelled))
    r = api.post(f"{V1}/evals/{e1}/promote-baseline", json={"name": "first"}, headers=owner.h)
    assert r.status_code == 201, r.text
    assert r.json()["suite"] == "smoke" and r.json()["name"] == "first" and r.json()["run_id"] == e1
    listed = api.get(f"{V1}/evals/baselines?suite=smoke", headers=owner.h).json()
    assert [b["name"] for b in listed] == ["first"]
    assert api.get(f"{V1}/evals/baselines?suite=other", headers=owner.h).json() == []
    # now the default baseline of the suite
    e3 = start(api, owner)
    assert e3.status_code == 202
    arg = gateway.started[-1][1]
    assert arg.baseline is not None and arg.baseline_name == "first"
    assert api.get(f"{V1}/evals/{e3.json()['id']}", headers=owner.h).json()["options"]["baseline"] == {
        "source": "promoted",
        "name": "first",
        "run_id": e1,
    }
    # another suite does not use it
    start(api, owner, suite="other")
    assert gateway.started[-1][1].baseline is None


def test_baseline_run_and_inline_baseline(api, gateway, owner, run_async):
    e1 = start(api, owner).json()["id"]
    assert start(api, owner, baseline_run_id=e1).status_code == 409  # no result yet
    finish(run_async, api, owner, e1, {"schema": "hwflow.eval/1", "aggregate": BASELINE})
    r = start(api, owner, baseline_run_id=e1)
    assert r.status_code == 202, r.text
    assert gateway.started[-1][1].baseline.name == "baseline.json"
    assert r.json()["options"]["baseline"] == {"source": "run", "run_id": e1}
    r = start(api, owner, baseline=BASELINE)  # the legacy aggregate shape
    assert r.status_code == 202, r.text
    assert gateway.started[-1][1].baseline is not None
    assert "baseline.json" in api.get(f"{V1}/runs/{r.json()['id']}", headers=owner.h).json()["artifacts"]


def test_other_tenants_see_nothing(api, gateway, owner, run_async):
    eid = start(api, owner).json()["id"]
    finish(run_async, api, owner, eid, {"schema": "hwflow.eval/1", "aggregate": BASELINE})
    assert api.post(f"{V1}/evals/{eid}/promote-baseline", json={}, headers=owner.h).status_code == 201
    other = Account(api, "eval-other")
    assert api.get(f"{V1}/evals", headers=other.h).json()["items"] == []
    assert api.get(f"{V1}/evals/baselines", headers=other.h).json() == []
    for path in ("", "/report", "/result"):
        assert api.get(f"{V1}/evals/{eid}{path}", headers=other.h).status_code == 404
    assert api.post(f"{V1}/evals/{eid}/promote-baseline", json={}, headers=other.h).status_code == 404
    assert start(api, other, baseline_run_id=eid).status_code == 404
    # the same suite name in another tenant has no baseline
    start(api, other)
    assert gateway.started[-1][1].baseline is None


def test_a_build_run_is_not_an_evaluation(api, gateway, owner):
    p = owner.project("demo")
    r = api.post(
        f"{V1}/projects/{p['id']}/design-versions",
        files=[("design", ("design.yaml", b"name: demo\n", "application/yaml"))],
        headers=owner.h,
    )
    dv = r.json()
    run = api.post(
        f"{V1}/projects/{p['id']}/runs",
        json={"kind": "build", "design_version_id": dv["id"]},
        headers=owner.h,
    ).json()
    assert api.get(f"{V1}/evals/{run['id']}", headers=owner.h).status_code == 404
    assert api.get(f"{V1}/evals", headers=owner.h).json()["items"] == []


def test_repository_fixtures_are_accepted_inline_and_by_id(make_api, gateway):
    """The fixtures of evals/fixtures/ as the local CLI sends them (inline), and installed on the server (by id)."""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "evals"
    sys.path.insert(0, str(root))
    try:
        import remote
    finally:
        sys.path.remove(str(root))
    ids = sorted(p.stem for p in (root / "fixtures").glob("*.yaml"))
    assert ids
    api = make_api(eval_fixtures_dir=str(root / "fixtures"))
    a = Account(api, "eval-fixtures")
    inline = [remote.fixture_doc(i) for i in ids]
    r = api.post(f"{V1}/evals", json={"suite": "repo", "fixtures": inline}, headers=a.h)
    assert r.status_code == 202, r.text
    got = {f["id"]: f for f in gateway.started[-1][1].fixtures}
    assert set(got) == set(ids)
    assert got["usb_preamp"]["mode"] == "from_requirements" and got["usb_preamp"]["requirements"].strip()
    r = api.post(f"{V1}/evals", json={"suite": "repo2", "fixtures": ids}, headers=a.h)
    assert r.status_code == 202, r.text
    assert {f["id"] for f in gateway.started[-1][1].fixtures} == set(ids)
    bad = api.post(f"{V1}/evals", json={"suite": "x", "fixtures": ["../etc/passwd"]}, headers=a.h)
    assert bad.status_code == 422


def test_unauthenticated_is_401(api):
    assert api.get(f"{V1}/evals").status_code == 401
    assert api.post(f"{V1}/evals", json={}).status_code == 401
