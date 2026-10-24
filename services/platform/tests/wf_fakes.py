"""Fake build activities under the real names (BuildWorkflow tests, the gateway test)."""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter

from hwflow.artifacts import ArtifactRef
from temporalio import activity

from hwplatform.contracts import (
    A_RECORD_RUN,
    A_RECORD_STAGE,
    A_RENDER_RUN,
    A_RESOLVE_RUN,
    A_STAGE,
    Q_ENGINE,
    BuildInput,
    RecordedRun,
    RecordRun,
    RecordStage,
    RenderInput,
    RenderOutput,
    ResolvedRun,
    ResolveInput,
    RunCtx,
    StageActivityInput,
    StageOut,
)

REF = ArtifactRef("c" * 64, 3, "application/json", "r.json")
SETTINGS = {"layout": {"budget_s": 3000, "auto_max_errors": 40}}


def out(stage: str, verdict: str = "pass", **summary) -> StageOut:
    return StageOut(
        stage=stage, verdict=verdict, cache_key="k" * 8, cached=False, result=REF, summary=summary
    )


class Fakes:
    """Activities under the real names; `stage` maps a key to an async function (inp) -> StageOut."""

    def __init__(self, stage=None):
        self.stage = stage or {}
        self.status = "queued"
        self.runs: list[RecordRun] = []
        self.stages: list[RecordStage] = []
        self.calls: Counter = Counter()
        self.renders = 0
        self.cancelled: list[str] = []

    @activity.defn(name=A_RECORD_RUN)
    async def record_run(self, r: RecordRun) -> RecordedRun:
        self.runs.append(r)
        allowed = {
            "running": {"queued"},
            "succeeded": {"running"},
            "failed": {"queued", "running"},
            "cancelled": {"queued", "running"},
        }
        if self.status in allowed[r.to]:
            self.status = r.to
            return RecordedRun(self.status, True)
        return RecordedRun(self.status, False)

    @activity.defn(name=A_RECORD_STAGE)
    async def record_stage(self, r: RecordStage) -> None:
        self.stages.append(r)

    @activity.defn(name=A_RESOLVE_RUN)
    async def resolve_run(self, r: ResolveInput) -> ResolvedRun:
        return ResolvedRun(settings=SETTINGS, code={}, tools={})

    @activity.defn(name=A_RENDER_RUN)
    async def render_run(self, r: RenderInput) -> RenderOutput:
        self.renders += 1
        fails = [
            s
            for s in self.stages
            if s.out is not None and s.out.verdict != "pass" and not s.key.startswith("layout_attempt")
        ]
        return RenderOutput(verdict="FAIL" if fails else "PASS", report=REF, status_json=REF)

    @activity.defn(name=A_STAGE)
    async def run_stage_activity(self, inp: StageActivityInput) -> StageOut:
        self.calls[inp.key] += 1
        fn = self.stage.get(inp.key)
        try:
            if fn is not None:
                return await fn(inp)
            if inp.stage == "layout_attempt":
                return out(inp.stage, ok=True)
            return out(inp.stage)
        except asyncio.CancelledError:
            self.cancelled.append(inp.key)
            raise

    def acts(self, queue: str) -> list:
        acts = [self.run_stage_activity]
        if queue == Q_ENGINE:
            acts += [self.record_run, self.record_stage, self.resolve_run, self.render_run]
        return acts

    def keys(self, status: str | None = None) -> list[str]:
        return [s.key for s in self.stages if status is None or s.status == status]


def arg(options: dict) -> BuildInput:
    rid = str(uuid.uuid4())
    return BuildInput(
        ctx=RunCtx("t", "p", rid, f"build-{rid}"),
        design_version_id="dv",
        design=REF,
        project=REF,
        options=options,
    )


async def hang(inp):
    """A stage that runs until it is cancelled (heartbeating, so the cancel reaches it)."""
    while True:
        activity.heartbeat("working")
        await asyncio.sleep(0.05)


class Workers:
    """`async with Workers([...])`: run several temporalio Workers at once."""

    def __init__(self, workers):
        self.workers = workers

    async def __aenter__(self):
        for w in self.workers:
            await w.__aenter__()
        return self

    async def __aexit__(self, *exc):
        for w in reversed(self.workers):
            await w.__aexit__(*exc)
