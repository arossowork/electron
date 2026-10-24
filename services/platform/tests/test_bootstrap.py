"""hwplatform-migrate's infrastructure bootstrap: bucket CORS (SeaweedFS) and Temporal search attributes."""

import asyncio
import urllib.error
import urllib.request
import uuid

import pytest
from pydantic import SecretStr

from hwplatform import bootstrap
from hwplatform.settings import Settings
from hwplatform.storage import make_s3_client

from conftest import S3_ACCESS, S3_ENDPOINT, S3_SECRET

ORIGIN = "http://127.0.0.1:8088"


def _preflight(url: str, origin: str):
    req = urllib.request.Request(  # noqa: S310
        url, method="OPTIONS", headers={"Origin": origin, "Access-Control-Request-Method": "GET"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


@pytest.mark.integration
def test_bucket_cors_rule_is_set_idempotently_and_answers_a_preflight(s3_client):
    bucket = f"cors-{uuid.uuid4().hex[:12]}"
    s = Settings(
        s3_endpoint_url=S3_ENDPOINT,
        s3_access_key=S3_ACCESS,
        s3_secret_key=SecretStr(S3_SECRET),
        s3_bucket=bucket,
        web_origins=f"{ORIGIN}/, https://app.example.com",
    )
    client = make_s3_client(s)
    client.create_bucket(Bucket=bucket)
    try:
        assert bootstrap.ensure_bucket_cors(s, client) is True
        assert bootstrap.ensure_bucket_cors(s, client) is True  # again: same rule set, not a second rule
        rules = client.get_bucket_cors(Bucket=bucket)["CORSRules"]
        assert len(rules) == 1
        assert rules[0]["AllowedOrigins"] == [ORIGIN, "https://app.example.com"]
        assert "GET" in rules[0]["AllowedMethods"]

        client.put_object(Bucket=bucket, Key="t/obj", Body=b"hi")
        url = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": "t/obj"})
        status, headers = _preflight(url, ORIGIN)
        assert status == 200 and headers.get("Access-Control-Allow-Origin") == ORIGIN
        status, headers = _preflight(url, "http://evil.example")
        assert status == 403 and "Access-Control-Allow-Origin" not in headers
    finally:
        client.delete_object(Bucket=bucket, Key="t/obj")
        client.delete_bucket(Bucket=bucket)


def test_no_web_origins_means_no_rule():
    class Boom:
        def put_bucket_cors(self, **kw):
            raise AssertionError("must not be called")

    assert bootstrap.ensure_bucket_cors(Settings(web_origins=""), Boom()) is False


def test_search_attributes_are_registered_idempotently():
    pytest.importorskip("temporalio")
    from temporalio.api.operatorservice.v1 import ListSearchAttributesRequest
    from temporalio.testing import WorkflowEnvironment

    async def go():
        try:
            env = await WorkflowEnvironment.start_local()
        except Exception as e:  # no network for the one-time dev server download
            pytest.skip(f"Temporal dev server unavailable: {type(e).__name__}: {e}")
        try:
            s = Settings(temporal_address=env.client.service_client.config.target_host)
            ops = env.client.operator_service

            async def have():
                r = await ops.list_search_attributes(ListSearchAttributesRequest(namespace="default"))
                return set(r.custom_attributes)

            assert not set(bootstrap.SEARCH_ATTRIBUTES) & await have()
            assert await bootstrap.ensure_search_attributes(s) == list(bootstrap.SEARCH_ATTRIBUTES)
            assert set(bootstrap.SEARCH_ATTRIBUTES) <= await have()
            assert await bootstrap.ensure_search_attributes(s) == []  # second run: nothing to add
        finally:
            await env.shutdown()

    asyncio.run(go())
