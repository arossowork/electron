"""#18 against the compose stack: the real API (tenant_id from the token in every log line) and the relay gauges."""

from __future__ import annotations

import asyncio

import pytest
from test_observability import capture, sample

from hwplatform.events.relay import Relay

from conftest import NATS_URL, RELAY_URL, Account

pytestmark = pytest.mark.integration


def test_api_request_logs_carry_request_id_and_tenant_id(client):
    acct = Account(client, "obs")
    with capture() as lines:
        r = client.get("/api/v1/tenant", headers={**acct.h, "X-Request-ID": "trace-me-1"})
    assert r.status_code == 200 and r.headers["x-request-id"] == "trace-me-1"
    access = [ln for ln in lines if ln["logger"] == "hwplatform.access" and ln["request_id"] == "trace-me-1"]
    assert len(access) == 1
    assert access[0]["tenant_id"] == acct.tenant_id and access[0]["user_id"] == acct.user_id
    assert access[0]["route"] == "/api/v1/tenant" and access[0]["status"] == 200
    # a request without a token: no tenant in the line, still a request id
    with capture() as lines:
        r = client.get("/api/v1/tenant")
    assert r.status_code == 401
    line = [ln for ln in lines if ln["logger"] == "hwplatform.access"][0]
    assert (
        "tenant_id" not in line and line["status"] == 401 and line["request_id"] == r.headers["x-request-id"]
    )
    text = client.get("/metrics").text
    assert 'hwp_http_requests_total{method="GET",route="/api/v1/tenant",status="200"}' in text


def test_relay_gauges_follow_the_outbox(relay_ready, settings):
    async def go():
        relay = Relay(settings, dsn=RELAY_URL, nats_url=NATS_URL)
        await relay.connect()
        try:
            n, age = await relay.observe_backlog()
            assert sample("hwp_relay_backlog") == n and sample("hwp_outbox_unpublished_rows") == n
            assert sample("hwp_relay_lag_seconds") == pytest.approx(age)
            assert await relay.try_lead() and sample("hwp_relay_leader") == 1
            await relay.run_once()
            assert (await relay.observe_backlog())[0] == 0
            assert sample("hwp_relay_backlog") == 0 and sample("hwp_relay_lag_seconds") == 0
        finally:
            await relay.close()
        assert sample("hwp_relay_leader") == 0

    asyncio.run(go())
