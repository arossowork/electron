"""Design versions, runs, artifacts and questions through the HTTP API (#12), with a fake workflow gateway.

Real Postgres (RLS applies), real SeaweedFS (S3). Temporal is the fake gateway of conftest.
"""

from __future__ import annotations

import asyncio
import io
import tarfile
import uuid

import httpx
import pytest
from hwflow.artifacts import ArtifactRef
from hwflow.runs import RunStatus, StageRecord, StageStatus

from hwplatform.db import Database
from hwplatform.questions import QuestionStore
from hwplatform.runs import PgRunStore
from hwplatform.storage import S3ArtifactStore

from conftest import Account

pytestmark = pytest.mark.integration

DESIGN = b"name: demo\nparts:\n  - ref: R1\n"
REQS = b"requirements:\n  - id: R-1\n"
V1 = "/api/v1"


def upload(api, acct, project, design=DESIGN, requirements=REQS, notes=(), bundle=None, message=""):
    files = []
    if design is not None:
        files.append(("design", ("design.yaml", design, "application/yaml")))
    if requirements is not None:
        files.append(("requirements", ("requirements.yaml", requirements, "application/yaml")))
    for name, data in notes:
        files.append(("notes", (name, data, "text/markdown")))
    if bundle is not None:
        files.append(("bundle", ("project.tar.gz", bundle, "application/gzip")))
    return api.post(
        f"{V1}/projects/{project['id']}/design-versions",
        files=files,
        data={"message": message},
        headers=acct.h,
    )


def targz(entries: dict[str, bytes], extra=None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        if extra:
            extra(tf)
    return buf.getvalue()


def start(api, acct, project, dv, options=None, **kw):
    headers = {**acct.h, **kw.pop("headers", {})}
    body = {"kind": "build", "design_version_id": dv["id"], "options": options or {}, **kw}
    return api.post(f"{V1}/projects/{project['id']}/runs", json=body, headers=headers)


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
    """An account with a project and an uploaded design version."""
    a = Account(api, "runs")
    p = a.project("demo")
    r = upload(api, a, p)
    assert r.status_code == 201, r.text
    return a, p, r.json()


# --- design versions ------------------------------------------------------------------------------------


def test_upload_is_content_addressed_and_immutable(api):
    a = Account(api, "dv")
    p = a.project("demo")
    r1 = upload(api, a, p, notes=[("n1.md", b"# note\n")], message="first")
    assert r1.status_code == 201, r1.text
    v1 = r1.json()
    assert v1["seq"] == 1 and v1["source"] == "upload" and v1["message"] == "first"
    assert v1["requirements_sha256"] and v1["created_by"] == a.user_id
    # same content: 200 and the same version
    again = upload(api, a, p, notes=[("n1.md", b"# note\n")], message="ignored")
    assert again.status_code == 200 and again.json()["id"] == v1["id"]
    # a changed file makes version 2; version 1 is untouched
    r2 = upload(api, a, p, design=DESIGN + b"  - ref: R2\n")
    assert r2.status_code == 201 and r2.json()["seq"] == 2
    assert r2.json()["content_sha256"] != v1["content_sha256"]
    page = api.get(f"{V1}/projects/{p['id']}/design-versions", headers=a.h).json()
    assert [x["seq"] for x in page["items"]] == [2, 1] and page["next_cursor"] is None
    one = api.get(f"{V1}/projects/{p['id']}/design-versions?limit=1", headers=a.h).json()
    assert len(one["items"]) == 1 and one["next_cursor"] == one["items"][0]["id"]
    nxt = api.get(
        f"{V1}/projects/{p['id']}/design-versions?limit=1&cursor={one['next_cursor']}", headers=a.h
    ).json()
    assert [x["seq"] for x in nxt["items"]] == [1]
    detail = api.get(f"{V1}/design-versions/{v1['id']}", headers=a.h).json()
    assert [f["path"] for f in detail["files"]] == ["design.yaml", "notes/n1.md", "requirements.yaml"]
    # download a file (no public S3 URL configured: the API streams it)
    d = api.get(f"{V1}/design-versions/{v1['id']}/files/design.yaml", headers=a.h)
    assert d.status_code == 200 and d.content == DESIGN
    assert "design.yaml" in d.headers["content-disposition"]
    assert api.get(f"{V1}/design-versions/{v1['id']}/files/nope.txt", headers=a.h).status_code == 404


def test_upload_bundle_and_validation(api):
    a = Account(api, "bundle")
    p = a.project("demo")
    b = targz({"design.yaml": DESIGN, "notes/a.md": b"a", "fw/main.c": b"int main(){}"})
    r = upload(api, a, p, design=None, requirements=None, bundle=b)
    assert r.status_code == 201, r.text
    files = api.get(f"{V1}/design-versions/{r.json()['id']}", headers=a.h).json()["files"]
    assert {f["path"] for f in files} == {"design.yaml", "notes/a.md", "fw/main.c"}
    # an explicit part overrides the bundle entry
    r2 = upload(api, a, p, design=DESIGN + b"  - ref: R9\n", requirements=None, bundle=b)
    assert r2.status_code == 201 and r2.json()["content_sha256"] != r.json()["content_sha256"]

    bad = [
        (dict(design=None, requirements=None), "design.yaml is required"),
        (dict(design=b"a: [1, 2"), "not valid YAML"),
        (dict(design=b"- a\n- b\n"), "must be a YAML mapping"),
        (
            dict(design=None, requirements=None, bundle=targz({"../evil": b"x", "design.yaml": DESIGN})),
            "bad file path",
        ),
        (
            dict(design=None, requirements=None, bundle=targz({"/abs": b"x", "design.yaml": DESIGN})),
            "bad file path",
        ),
        (dict(design=None, requirements=None, bundle=b"not a tarball"), "not a valid tar.gz"),
    ]
    for kw, msg in bad:
        r = upload(api, a, p, **kw)
        assert r.status_code == 422 and msg in r.json()["detail"], (kw, r.text)

    def symlink(tf):
        ti = tarfile.TarInfo("link")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "/etc/passwd"
        tf.addfile(ti)

    r = upload(api, a, p, design=None, requirements=None, bundle=targz({"design.yaml": DESIGN}, symlink))
    assert r.status_code == 422 and "not a regular file" in r.json()["detail"]
    assert api.post(f"{V1}/projects/{uuid.uuid4()}/design-versions", files=[], headers=a.h).status_code in (
        404,
        422,
    )


def test_upload_size_limits(make_api):
    small = make_api(max_bundle_bytes=1000, max_bundle_files=3)
    a = Account(small, "limits")
    p = a.project("demo")
    assert upload(small, a, p, design=DESIGN + b"#" * 2000).status_code == 413
    big = targz({"design.yaml": DESIGN, "a": b"1", "b": b"2", "c": b"3"})
    assert upload(small, a, p, design=None, requirements=None, bundle=big).status_code == 413
    assert upload(small, a, p).status_code == 201


# --- runs: start, idempotency, quota, cancel ------------------------------------------------------------


def test_start_run_calls_the_gateway_with_the_adr_workflow_id(api, gateway, world):
    a, p, dv = world
    r = start(api, a, p, dv, {"until": "sim", "mc": 5})
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["status"] == "queued" and run["kind"] == "build" and run["verdict"] is None
    assert run["options"]["until"] == "sim" and run["options"]["mc"] == 5 and run["options"]["attempts"] == 4
    wf, arg = gateway.started[0]
    assert wf == f"build-{a.tenant_id}-{p['id']}-{dv['content_sha256'][:16]}-{run['options_hash']}"
    assert run["workflow_id"] == wf
    assert arg.ctx.run_id == run["id"] and arg.ctx.tenant_id == a.tenant_id and arg.ctx.workflow_id == wf
    assert arg.design_version_id == dv["id"] and arg.design.sha256 == dv["design_sha256"]
    assert arg.project.sha256 == dv["content_sha256"] and arg.options == run["options"]
    got = api.get(f"{V1}/runs/{run['id']}", headers=a.h).json()
    assert got["id"] == run["id"] and got["stages"] == []
    lst = api.get(f"{V1}/projects/{p['id']}/runs", headers=a.h).json()
    assert [x["id"] for x in lst["items"]] == [run["id"]]
    assert "stages" not in lst["items"][0]
    assert api.get(f"{V1}/projects/{p['id']}/runs?status=running", headers=a.h).json()["items"] == []
    assert (
        len(api.get(f"{V1}/projects/{p['id']}/runs?kind=build&status=queued", headers=a.h).json()["items"])
        == 1
    )
    assert api.get(f"{V1}/projects/{p['id']}/runs?status=bogus", headers=a.h).status_code == 422


def test_start_is_idempotent_while_active(api, gateway, world, run_async):
    a, p, dv = world
    first = start(api, a, p, dv, {"until": "sim"})
    assert first.status_code == 202
    # same build again (options spelled differently, same result): the running one, 200, no second start
    second = start(api, a, p, dv, {"until": "sim", "attempts": 4})
    assert second.status_code == 200 and second.json()["id"] == first.json()["id"]
    assert len(gateway.started) == 1
    # an Idempotency-Key returns its run even for different options
    k1 = start(api, a, p, dv, {"until": "check"}, headers={"Idempotency-Key": "key-1"})
    assert k1.status_code == 202
    k2 = start(api, a, p, dv, {"until": "layout"}, headers={"Idempotency-Key": "key-1"})
    assert k2.status_code == 200 and k2.json()["id"] == k1.json()["id"]
    # once it has ended, the same build starts a NEW run (the cache makes it cheap)
    rid = first.json()["id"]

    async def finish(db):
        s = PgRunStore(db, a.tenant_id)
        await s.transition(rid, RunStatus.running)
        await s.transition(rid, RunStatus.succeeded, verdict="PASS")

    run_async(finish)
    third = start(api, a, p, dv, {"until": "sim"})
    assert third.status_code == 202 and third.json()["id"] != rid
    assert third.json()["workflow_id"] == first.json()["workflow_id"]


def test_option_allowlist(api, world):
    a, p, dv = world
    for opts in (
        {"workers": 8},
        {"stages": ["sim"]},
        {"mc": -1},
        {"mc": True},
        {"until": "nope"},
        {"force": "yes"},
    ):
        r = start(api, a, p, dv, opts)
        assert r.status_code == 422, (opts, r.text)
    assert start(api, a, p, dv, settings_overrides={"layout.budget_s": 60}).status_code == 202
    assert start(api, a, p, dv, {"mc": 1}, settings_overrides={"paths.cas_dir": "/x"}).status_code == 422
    assert start(api, a, p, dv, {"mc": 2}, kind="design").status_code == 422  # a design run needs a brief
    r = start(api, a, p, dv, {"brief": "a thermostat"}, kind="design")
    assert r.status_code == 202 and r.json()["workflow_id"].startswith(f"design-{a.tenant_id}-{p['id']}-")


def test_quota_429_and_slot_freed_by_ending(api, gateway, world, run_async):
    a, p, dv = world  # default quota: 2 concurrent runs
    r1 = start(api, a, p, dv, {"mc": 1})
    r2 = start(api, a, p, dv, {"mc": 2})
    assert r1.status_code == r2.status_code == 202
    r3 = start(api, a, p, dv, {"mc": 3})
    assert r3.status_code == 429 and r3.json()["code"] == "quota_exceeded"
    assert r3.json()["limit"] == 2 and r3.json()["active"] == 2 and "Retry-After" in r3.headers
    assert len(gateway.started) == 2
    # re-requesting a running build is not new work: no 429
    assert start(api, a, p, dv, {"mc": 1}).status_code == 200
    # another tenant is not affected
    b = Account(api, "quota-b")
    pb = b.project("p")
    dvb = upload(api, b, pb).json()
    assert start(api, b, pb, dvb).status_code == 202

    async def end(db):
        s = PgRunStore(db, a.tenant_id)
        await s.transition(r1.json()["id"], RunStatus.cancelled)

    run_async(end)
    assert start(api, a, p, dv, {"mc": 3}).status_code == 202


def test_concurrent_starts_never_exceed_the_quota(api, gateway, world):
    a, p, dv = world
    import concurrent.futures as cf

    with cf.ThreadPoolExecutor(6) as ex:
        codes = list(ex.map(lambda i: start(api, a, p, dv, {"mc": 10 + i}).status_code, range(6)))
    assert sorted(codes) == [202, 202, 429, 429, 429, 429], codes
    assert len(gateway.started) == 2


def test_start_failure_marks_the_run_failed_and_frees_the_slot(api, gateway, world):
    a, p, dv = world
    gateway.fail = True
    r = start(api, a, p, dv, {"mc": 1})
    assert r.status_code == 503
    runs = api.get(f"{V1}/projects/{p['id']}/runs", headers=a.h).json()["items"]
    assert [x["status"] for x in runs] == ["failed"]
    gateway.fail = False
    assert (
        start(api, a, p, dv, {"mc": 1}).status_code == 202
    )  # the failed run no longer holds the workflow ID


def test_cancel(api, gateway, world):
    a, p, dv = world
    run = start(api, a, p, dv).json()
    r = api.post(f"{V1}/runs/{run['id']}/cancel", headers=a.h)
    assert r.status_code == 202 and gateway.cancelled == [run["workflow_id"]]
    # no workflow behind the run (never started, history gone): the API marks it cancelled itself
    run2 = start(api, a, p, dv, {"mc": 2}).json()
    gateway.missing.add(run2["workflow_id"])
    r = api.post(f"{V1}/runs/{run2['id']}/cancel", headers=a.h)
    assert r.status_code == 202 and r.json()["status"] == "cancelled"
    assert api.post(f"{V1}/runs/{run2['id']}/cancel", headers=a.h).status_code == 409
    run3 = start(api, a, p, dv, {"mc": 3}).json()
    gateway.fail = True
    assert api.post(f"{V1}/runs/{run3['id']}/cancel", headers=a.h).status_code == 503
    assert api.get(f"{V1}/runs/{run3['id']}", headers=a.h).json()["status"] == "queued"


def test_cancel_queued_run_with_workflow_but_no_worker_is_cancelled_and_emits_event(
    api, gateway, world, run_async
):
    # the workflow exists (started) but no worker ran its first task: the run is still `queued`
    a, p, dv = world
    run = start(api, a, p, dv, {"mc": 7}).json()
    assert run["status"] == "queued"
    r = api.post(f"{V1}/runs/{run['id']}/cancel", headers=a.h)
    assert r.status_code == 202 and r.json()["status"] == "cancelled"
    assert gateway.cancelled == [run["workflow_id"]]  # the workflow is still told to stop
    assert api.get(f"{V1}/runs/{run['id']}", headers=a.h).json()["status"] == "cancelled"

    async def events(db):
        from sqlalchemy import text

        async with db.tx(tenant_id=a.tenant_id) as s:
            q = text("SELECT type FROM outbox WHERE run_id = CAST(:r AS uuid)")
            return list((await s.execute(q, {"r": run["id"]})).scalars())

    assert "run.cancelled" in run_async(events)


def test_cancel_running_run_is_left_to_the_workflow(api, gateway, world, run_async):
    a, p, dv = world
    run = start(api, a, p, dv, {"mc": 8}).json()
    run_async(lambda db: PgRunStore(db, a.tenant_id).transition(run["id"], RunStatus.running))
    r = api.post(f"{V1}/runs/{run['id']}/cancel", headers=a.h)
    assert r.status_code == 202 and r.json()["status"] == "running"
    assert gateway.cancelled == [run["workflow_id"]]


# --- run detail, manifest, report, artifacts ------------------------------------------------------------


def test_run_detail_manifest_report_and_artifact_download(make_api, gateway, api_settings, run_async):
    # public S3 URL set: downloads are 302s to presigned URLs
    pub = make_api(s3_public_url=api_settings().s3_endpoint_url)
    a = Account(pub, "art")
    p = a.project("demo")
    dv = upload(pub, a, p).json()
    run = start(pub, a, p, dv).json()
    report = b"# Report\nverdict: PASS\n"
    board = b"(kicad_pcb)"

    async def progress(db):
        from conftest import S3_BUCKET

        st = PgRunStore(db, a.tenant_id)
        store = S3ArtifactStore(pub.app.state.s3, S3_BUCKET, a.tenant_id)
        rid = run["id"]
        await st.transition(rid, RunStatus.running)
        await st.upsert_stage(rid, StageRecord("check", "check", 0, StageStatus.running))
        await st.upsert_stage(
            rid,
            StageRecord(
                "check", "check", 0, StageStatus.succeeded, verdict="PASS", seconds=1.5, summary={"n": 1}
            ),
        )
        rep = store.put_bytes(report, media_type="text/markdown", name="REPORT.md")
        brd = store.put_bytes(board, media_type="application/octet-stream", name="board")
        await st.set_artifacts(rid, {"REPORT.md": rep, "layout_select/board": brd})
        await st.transition(rid, RunStatus.succeeded, verdict="PASS")

    run_async(progress)
    got = pub.get(f"{V1}/runs/{run['id']}", headers=a.h).json()
    assert got["status"] == "succeeded" and got["verdict"] == "PASS"
    assert [(s["key"], s["status"], s["verdict"], s["seconds"]) for s in got["stages"]] == [
        ("check", "succeeded", "PASS", 1.5)
    ]
    assert got["artifacts"] == ["REPORT.md", "layout_select/board"]
    man = pub.get(f"{V1}/runs/{run['id']}/manifest", headers=a.h).json()
    assert (
        man["schema"] == "hwflow.run/1" and man["run_id"] == run["id"] and man["stages"][0]["key"] == "check"
    )
    rep = pub.get(f"{V1}/runs/{run['id']}/report", headers=a.h)
    assert (
        rep.status_code == 200
        and rep.content == report
        and rep.headers["content-type"].startswith("text/markdown")
    )
    arts = pub.get(f"{V1}/runs/{run['id']}/artifacts", headers=a.h).json()
    assert [(x["name"], x["stage_key"], x["size"]) for x in arts] == [
        ("REPORT.md", None, len(report)),
        ("layout_select/board", "layout_select", len(board)),
    ]
    r = pub.get(f"{V1}/runs/{run['id']}/artifacts/layout_select/board", headers=a.h, follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert (
        loc.startswith(api_settings().s3_endpoint_url)
        and "X-Amz-Signature" in loc
        and "X-Amz-Expires=300" in loc
    )
    assert httpx.get(loc, timeout=10).content == board  # the bytes come from S3, not through the API
    # the signature covers the key: another object under the same bucket is refused
    assert httpx.get(loc.replace("/artifacts/", "/artifacts/0"), timeout=10).status_code == 403
    assert pub.get(f"{V1}/runs/{run['id']}/artifacts/nope", headers=a.h).status_code == 404
    # the same run, API without a public URL: streamed
    plain = make_api()
    s = plain.get(f"{V1}/runs/{run['id']}/artifacts/REPORT.md", headers=a.h)
    assert s.status_code == 200 and s.content == report
    assert 'filename="REPORT.md"' in s.headers["content-disposition"]


def test_report_before_it_exists_is_404(api, world):
    a, p, dv = world
    run = start(api, a, p, dv).json()
    assert api.get(f"{V1}/runs/{run['id']}/report", headers=a.h).status_code == 404
    assert api.get(f"{V1}/runs/{run['id']}/artifacts", headers=a.h).json() == []
    assert api.get(f"{V1}/runs/{run['id']}/manifest", headers=a.h).status_code == 200


# --- questions -----------------------------------------------------------------------------------------


QS = [
    {"id": "q1", "text": "Supply voltage?", "options": ["3.3", "5"]},
    {"id": "q2", "text": "Colour?", "default": "red"},
]


def test_questions_and_answer_signal(api, gateway, world, run_async):
    a, p, dv = world
    run = start(api, a, p, {**dv}, kind="design", options={"brief": "x"}).json()

    async def ask(db):
        await PgRunStore(db, a.tenant_id).transition(run["id"], RunStatus.running)
        q = await QuestionStore(db, a.tenant_id).ask(run["id"], "agent.requirements", QS)
        await PgRunStore(db, a.tenant_id).transition(
            run["id"], RunStatus.waiting, event_data={"reason": "question"}
        )
        return q

    q = run_async(ask)
    lst = api.get(f"{V1}/runs/{run['id']}/questions", headers=a.h).json()
    assert [x["id"] for x in lst] == [q.id] and lst[0]["status"] == "open" and lst[0]["questions"] == QS
    assert api.get(f"{V1}/runs/{run['id']}/questions?status=answered", headers=a.h).json() == []
    ans = f"{V1}/questions/{q.id}/answer"
    assert api.post(ans, json={"answers": {"zz": 1}}, headers=a.h).status_code == 422  # unknown id
    assert api.post(ans, json={"answers": {}}, headers=a.h).status_code == 422  # q1 has no default
    assert gateway.signals == []
    r = api.post(ans, json={"answers": {"q1": "3.3"}}, headers=a.h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "answered" and r.json()["answers"] == {"q1": "3.3"}
    assert r.json()["answered_by"] == a.user_id
    wf, sig = gateway.signals[0]
    assert wf == run["workflow_id"] and sig.question_id == q.id and sig.answers == {"q1": "3.3"}
    assert sig.answered_by == a.user_id
    assert api.post(ans, json={"answers": {"q1": "5"}}, headers=a.h).status_code == 409  # not open any more
    assert len(gateway.signals) == 1
    assert api.get(f"{V1}/runs/{run['id']}/questions?status=answered", headers=a.h).json()[0]["id"] == q.id


def test_answer_when_the_signal_fails_leaves_the_question_open(api, gateway, world, run_async):
    a, p, dv = world
    run = start(api, a, p, dv, kind="design", options={"brief": "x"}).json()
    q = run_async(lambda db: QuestionStore(db, a.tenant_id).ask(run["id"], "agent.parts", QS))
    gateway.fail = True
    ans = f"{V1}/questions/{q.id}/answer"
    assert api.post(ans, json={"answers": {"q1": "5"}}, headers=a.h).status_code == 503
    assert api.get(f"{V1}/runs/{run['id']}/questions", headers=a.h).json()[0]["status"] == "open"
    gateway.fail = False
    assert api.post(ans, json={"answers": {"q1": "5"}}, headers=a.h).status_code == 200


def test_answer_after_the_run_ended_is_409(api, gateway, world, run_async):
    a, p, dv = world
    run = start(api, a, p, dv, kind="design", options={"brief": "x"}).json()
    q = run_async(lambda db: QuestionStore(db, a.tenant_id).ask(run["id"], "agent.parts", QS))
    run_async(lambda db: PgRunStore(db, a.tenant_id).transition(run["id"], RunStatus.cancelled))
    r = api.post(f"{V1}/questions/{q.id}/answer", json={"answers": {"q1": "5"}}, headers=a.h)
    assert r.status_code == 409 and gateway.signals == []


# --- roles ---------------------------------------------------------------------------------------------


def test_member_reads_and_starts_runs_but_cannot_delete_projects(api, gateway, world):
    owner, p, dv = world
    m = Account(api, "member")
    h = m.join(owner, "member")
    r = api.post(f"{V1}/projects/{p['id']}/runs", json={"design_version_id": dv["id"]}, headers=h)
    assert r.status_code == 202, r.text
    assert api.get(f"{V1}/runs/{r.json()['id']}", headers=h).status_code == 200
    assert api.get(f"{V1}/projects/{p['id']}/design-versions", headers=h).status_code == 200
    assert api.delete(f"{V1}/projects/{p['id']}", headers=h).status_code == 403
    adm = Account(api, "admin")
    ha = adm.join(owner, "admin")
    assert api.delete(f"{V1}/projects/{p['id']}", headers=ha).status_code == 204


def test_unauthenticated_requests_are_401(api, world):
    a, p, dv = world
    rid = uuid.uuid4()
    for method, path in [
        ("get", f"/projects/{p['id']}/runs"),
        ("post", f"/projects/{p['id']}/runs"),
        ("get", f"/runs/{rid}"),
        ("post", f"/runs/{rid}/cancel"),
        ("get", f"/runs/{rid}/events"),
        ("get", "/events"),
        ("get", f"/design-versions/{dv['id']}"),
        ("post", f"/questions/{rid}/answer"),
    ]:
        assert getattr(api, method)(V1 + path).status_code == 401, path


# --- cross-tenant isolation ----------------------------------------------------------------------------


def test_cross_tenant_everything_is_404(api, gateway, world, run_async):
    a, p, dv = world
    run = start(api, a, p, dv, kind="design", options={"brief": "x"}).json()
    q = run_async(lambda db: QuestionStore(db, a.tenant_id).ask(run["id"], "agent.parts", QS))

    async def art(db):
        from conftest import S3_BUCKET

        store = S3ArtifactStore(api.app.state.s3, S3_BUCKET, a.tenant_id)
        await PgRunStore(db, a.tenant_id).set_artifacts(
            run["id"], {"REPORT.md": store.put_bytes(b"secret", media_type="text/markdown", name="REPORT.md")}
        )

    run_async(art)
    b = Account(api, "intruder")
    pb = b.project("mine")
    dvb = upload(api, b, pb).json()
    n_started = len(gateway.started)
    checks = [
        ("get", f"/projects/{p['id']}/design-versions", None),
        ("post", f"/projects/{p['id']}/design-versions", None),
        ("get", f"/design-versions/{dv['id']}", None),
        ("get", f"/design-versions/{dv['id']}/files/design.yaml", None),
        ("get", f"/projects/{p['id']}/runs", None),
        ("post", f"/projects/{p['id']}/runs", {"design_version_id": dv["id"]}),  # A's project, A's version
        ("post", f"/projects/{pb['id']}/runs", {"design_version_id": dv["id"]}),  # B's project, A's version
        ("post", f"/projects/{p['id']}/runs", {"design_version_id": dvb["id"]}),  # A's project, B's version
        ("get", f"/runs/{run['id']}", None),
        ("post", f"/runs/{run['id']}/cancel", None),
        ("get", f"/runs/{run['id']}/manifest", None),
        ("get", f"/runs/{run['id']}/report", None),
        ("get", f"/runs/{run['id']}/artifacts", None),
        ("get", f"/runs/{run['id']}/artifacts/REPORT.md", None),
        ("get", f"/runs/{run['id']}/questions", None),
        ("post", f"/questions/{q.id}/answer", {"answers": {"q1": "5"}}),
        ("get", f"/runs/{run['id']}/events", None),
        ("get", f"/projects/{p['id']}/events", None),
    ]
    for method, path, body in checks:
        kw = {"headers": b.h}
        if method == "post" and body is not None:
            kw["json"] = body
        elif method == "post":
            kw["files"] = [("design", ("design.yaml", DESIGN, "application/yaml"))]
        r = getattr(api, method)(V1 + path, **kw)
        assert r.status_code == 404, (method, path, r.status_code, r.text[:200])
    assert len(gateway.started) == n_started and gateway.cancelled == [] and gateway.signals == []
    # A still has everything, nothing leaked into B's lists
    assert api.get(f"{V1}/runs/{run['id']}/report", headers=a.h).content == b"secret"
    assert api.get(f"{V1}/projects/{pb['id']}/runs", headers=b.h).json()["items"] == []
    assert [
        x["id"] for x in api.get(f"{V1}/projects/{pb['id']}/design-versions", headers=b.h).json()["items"]
    ] == [dvb["id"]]
    # the same bytes uploaded by B are B's own version, not A's
    same = upload(api, b, pb)
    assert same.json()["id"] == dvb["id"]


def test_artifact_ref_of_another_tenant_is_not_served(api, world, run_async):
    """Even a run_artifacts row that names a sha256 only the other tenant stored cannot leak bytes: the key is
    built from the caller's tenant prefix."""
    from conftest import S3_BUCKET

    a, p, dv = world
    b = Account(api, "thief")
    pb = b.project("p")
    dvb = upload(api, b, pb).json()
    runb = start(api, b, pb, dvb).json()
    secret = S3ArtifactStore(api.app.state.s3, S3_BUCKET, a.tenant_id).put_bytes(
        b"A-only " + uuid.uuid4().bytes
    )
    run_async(
        lambda db: PgRunStore(db, b.tenant_id).set_artifacts(
            runb["id"], {"stolen": ArtifactRef(secret.sha256, secret.size, "text/plain", "stolen")}
        )
    )
    r = api.get(f"{V1}/runs/{runb['id']}/artifacts/stolen", headers=b.h)
    assert r.status_code == 404  # B's prefix has no such object
