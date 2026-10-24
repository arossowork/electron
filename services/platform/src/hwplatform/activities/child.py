"""The stage child of the platform worker: `python -m hwplatform.activities.child in.json out.json` (ADR 0001,
4.5).

The engine's `hwflow.pipeline.exec.run_child` with the tenant's S3ArtifactStore and the Postgres stage cache
(PgStageCache behind a synchronous adapter on a private event loop). Before the stage runs, the tools it
declares are checked: a missing one is `ToolMissing` (not retryable). A store that does not answer is
`StoreUnavailable`. The job adds `tenant_id` to the engine's child job format. The child runs in its own
process group and kills that group when its parent (the worker) dies, so a killed worker leaves no tool
running.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

from ..contracts import errors

ROUTERS = ("krt", "freerouting")  # layout_attempt needs one router, not both


class SyncStageCache:
    """The engine's synchronous StageCache over PgStageCache: one event loop in a thread, one DB pool on
    it."""

    def __init__(self, settings, tenant_id: str):
        from ..db import Database
        from ..runs import PgStageCache

        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, name="stage-cache", daemon=True).start()

        async def make():
            return PgStageCache(
                Database.from_settings(settings.model_copy(update={"db_pool_size": 1})), tenant_id
            )

        self.cache = self._run(make())

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=60)

    def get(self, stage: str, key: str):
        from hwflow.pipeline.spec import StageResult

        doc = self._run(self.cache.get(stage, key))
        try:
            return StageResult.from_json(doc) if doc else None
        except (KeyError, TypeError, ValueError):
            return None  # a corrupt entry reads as a miss, as in LocalStageCache

    def put(self, stage: str, key: str, result) -> None:
        self._run(self.cache.put(stage, key, result.to_json()))


def missing_tools(stage: str) -> list[str]:
    from hwflow.pipeline.spec import STAGE_SPECS, tool_versions

    spec = STAGE_SPECS[stage]
    have = tool_versions(spec.tools)
    gone = [t for t, v in have.items() if not v or v == "missing"]
    if stage == "layout_attempt" and any(have.get(r) not in (None, "", "missing") for r in ROUTERS):
        gone = [t for t in gone if t not in ROUTERS]
    return gone


def _make_store(j: dict):
    from ..settings import get_settings
    from ..storage import S3ArtifactStore, make_s3_client

    s = get_settings()
    return S3ArtifactStore(make_s3_client(s), s.s3_bucket, j["tenant_id"]), SyncStageCache(s, j["tenant_id"])


def _fail(dst: Path, type_: str, message: str) -> int:
    from hwflow.pipeline.exec import write_json_atomic

    write_json_atomic(dst, {"error": {"type": type_, "message": message[:2000]}})
    return 3


def main(argv: list[str]) -> int:
    from hwflow.pipeline.exec import run_child

    from ..observability.child import setup_child, write_rusage_at_exit
    from ..settings import get_settings

    src, dst = Path(argv[0]), Path(argv[1])
    j = json.loads(src.read_text())
    st = get_settings()
    setup_child(j, level=st.log_level, fmt=st.log_format)
    write_rusage_at_exit(dst.with_name("rusage.json"))
    gone = missing_tools(j["spec"])
    if gone:
        return _fail(
            dst, errors.ToolMissing, f"stage {j['spec']}: tools missing on this worker: {', '.join(gone)}"
        )
    return run_child(argv, _make_store)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
