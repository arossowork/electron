"""hwplatform-worker without Temporal: the health server (/livez, /readyz), the queue selection and the stage
activity's error classification."""

from __future__ import annotations

import asyncio

from hwplatform.activities.stage import classify
from hwplatform.contracts import Q_AGENT, QUEUES, errors
from hwplatform.settings import Settings
from hwplatform.workers.main import DEFAULT_QUEUES, Health, queues_from


def _get(port: int, path: str) -> int:
    async def go():
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"GET {path} HTTP/1.0\r\n\r\n".encode())
        await w.drain()
        line = await r.readline()
        w.close()
        return int(line.split()[1])

    return go()


def test_health_live_and_ready():
    async def go():
        h = Health()
        server = await h.serve("127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            assert await _get(port, "/livez") == 200  # no Temporal yet: alive, not ready
            assert await _get(port, "/readyz") == 503
            h.client = object()
            stop = asyncio.Event()
            h.tasks = [asyncio.create_task(stop.wait())]
            assert await _get(port, "/readyz") == 200
            stop.set()  # a poller ended
            await asyncio.sleep(0)
            assert await _get(port, "/readyz") == 503
            assert await _get(port, "/nope") == 404
        finally:
            server.close()

    asyncio.run(go())


def test_queues_from_flag_env_or_default():
    assert queues_from(None, Settings()) == DEFAULT_QUEUES and Q_AGENT not in DEFAULT_QUEUES
    assert queues_from(None, Settings(worker_queues="hwflow-sim, hwflow-layout")) == (
        "hwflow-sim",
        "hwflow-layout",
    )
    assert queues_from("hwflow-engine", Settings(worker_queues="hwflow-sim")) == ("hwflow-engine",)
    assert set(DEFAULT_QUEUES) < set(QUEUES)


def test_stage_error_classification():
    e = classify(3, {"error": {"type": "DesignInvalid", "message": "bad"}}, "")
    assert e.type == errors.DesignInvalid and e.non_retryable
    e = classify(3, {"error": {"type": "ToolMissing", "message": "no ngspice"}}, "")
    assert e.type == errors.ToolMissing and e.non_retryable
    e = classify(3, {"error": {"type": "EndpointConnectionError", "message": "s3"}}, "")
    assert e.type == errors.StoreUnavailable and not e.non_retryable
    assert classify(-9, None, "").type == errors.WorkerLost
    assert classify(1, None, "Segmentation fault").type == errors.ToolCrashed
