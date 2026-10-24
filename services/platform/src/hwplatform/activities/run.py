"""resolve_run and render_run (ADR 0001, 4.4 steps 2 and 4), on the engine queue.

resolve_run: the run's settings snapshot (the worker's engine Settings plus the allowlisted overrides), code
and tool versions (stored on the run row), the project state refs the stages start from (`placement` only with
`reuse`, as inline), and the layout result when the project's layout history blocks this structure.

render_run: the run's status.json and REPORT.md from its stage rows and outputs
(`hwflow.pipeline.runner.render_view`, the same view the CLI renders), stored as run artifacts with score.json
and the stage outputs (`<key>/<name>`); the state the stages produced becomes the project state. The verdict
goes to the run with record_run(succeeded).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from hwflow.artifacts import ArtifactRef
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..contracts import (
    A_RENDER_RUN,
    A_RESOLVE_RUN,
    RenderInput,
    RenderOutput,
    ResolvedRun,
    ResolveInput,
    StageOut,
    errors,
)
from ..runs import PgRunStore
from .deps import Deps
from .wire import out_from_json

JSON = "application/json"


def apply_overrides(snapshot: dict, overrides: dict) -> dict:
    """`layout.budget_s: 600` -> snapshot["layout"]["budget_s"] = 600 (dotted keys, nested groups)."""
    out = json.loads(json.dumps(snapshot))
    for key, value in sorted(overrides.items()):
        *path, leaf = key.split(".")
        cur = out
        for p in path:
            cur = cur.setdefault(p, {})
        cur[leaf] = value
    return out


def resolve_settings(overrides: dict) -> dict:
    from hwflow.core.settings import Settings, get_settings

    base = get_settings().snapshot()
    try:
        return Settings.from_snapshot(apply_overrides(base, overrides)).snapshot()
    except Exception as e:  # noqa: BLE001 - pydantic validation of an override value
        raise ApplicationError(
            f"settings overrides: {e}", type=errors.DesignInvalid, non_retryable=True
        ) from e


def blocked_out(store, inp: ResolveInput, state: dict[str, ArtifactRef], work: Path) -> StageOut | None:
    """The `layout_select` result standing for a layout the project's layout history blocks, else None."""
    from hwflow import buildplan
    from hwflow.artifacts import get_tree
    from hwflow.core.design import Design
    from hwflow.pipeline.layout import blocked_result
    from hwflow.pipeline.spec import JSON_MEDIA_TYPE, STAGE_SPECS, StageResult
    from hwflow.pipeline.status import _design_hash

    if "layout" not in buildplan.stage_list(inp.options) or "layout_history" not in state:
        return None
    proj = work / (inp.project.name or "project")
    get_tree(store, inp.project, proj)
    (proj / "build").mkdir(exist_ok=True)
    store.get_file(state["layout_history"].sha256, proj / "build" / "layout_history.json")
    blk = blocked_result(proj, bool(inp.options.get("retry_layout")))
    if blk is None:
        return None
    spec = STAGE_SPECS[buildplan.SELECT]
    ref = store.put_bytes(
        json.dumps(blk, sort_keys=True, default=str).encode(),
        media_type=JSON_MEDIA_TYPE,
        name="layout_select.result.json",
    )
    d = Design.load(proj / "design.yaml")
    res = StageResult(
        stage=buildplan.SELECT,
        verdict="blocked",
        cache_key="",
        cached=False,
        result=ref,
        summary={**spec.summarize(blk), "design_hash": _design_hash(d), "inputs_hash": "blocked"},
    )
    return out_from_json(res.to_json())


class RunActivities:
    def __init__(self, deps: Deps):
        self.deps = deps

    @activity.defn(name=A_RESOLVE_RUN)
    async def resolve_run(self, inp: ResolveInput) -> ResolvedRun:
        from hwflow.pipeline.runner import code_info
        from hwflow.pipeline.spec import tool_versions

        settings = await asyncio.to_thread(resolve_settings, inp.settings_overrides)
        code = await asyncio.to_thread(code_info)
        tools = await asyncio.to_thread(tool_versions)
        runs = PgRunStore(self.deps.db, inp.ctx.tenant_id)
        await runs.set_run_info(inp.ctx.run_id, settings=settings, code=code, tools=tools)
        state = await runs.project_state(inp.ctx.project_id)
        if not inp.options.get("reuse"):
            state.pop("placement", None)  # as inline: the last placement is a start only with --reuse
        store = self.deps.store(inp.ctx.tenant_id)

        def blocked():
            with tempfile.TemporaryDirectory(dir=self.deps.scratch()) as t:
                return blocked_out(store, inp, state, Path(t))

        return ResolvedRun(
            settings=settings,
            code=code,
            tools=tools,
            state=state,
            layout_blocked=await asyncio.to_thread(blocked),
        )

    @activity.defn(name=A_RENDER_RUN)
    async def render_run(self, inp: RenderInput) -> RenderOutput:
        from hwflow.pipeline.runner import _verdict, render_view

        runs = PgRunStore(self.deps.db, inp.ctx.tenant_id)
        run = await runs.get(inp.ctx.run_id)
        store = self.deps.store(inp.ctx.tenant_id)

        def render():
            with tempfile.TemporaryDirectory(dir=self.deps.scratch()) as t:
                report, status = render_view(run, store, Path(t))
                arts = {
                    "REPORT.md": store.put_bytes(
                        report.encode(), media_type="text/markdown", name="REPORT.md"
                    ),
                    "status.json": store.put_bytes(
                        json.dumps(status, indent=2, default=str).encode(),
                        media_type=JSON,
                        name="status.json",
                    ),
                }
                if isinstance(status.get("score"), dict):
                    arts["score.json"] = store.put_bytes(
                        json.dumps(status["score"], indent=2, sort_keys=True, default=str).encode(),
                        media_type=JSON,
                        name="score.json",
                    )
                return report, arts

        report, arts = await asyncio.to_thread(render)
        state: dict[str, ArtifactRef] = {}
        for rec in sorted(run.stages, key=lambda r: r.seq):
            if rec.key.startswith("layout_attempt"):
                continue  # their files reach the run through layout_select
            for name, ref in rec.outputs.items():
                if name.startswith("state/"):
                    state[name[len("state/") :]] = ref
                else:
                    arts[f"{rec.key}/{name}"] = ref
        await runs.set_artifacts(inp.ctx.run_id, arts)
        if state:
            await runs.set_project_state(inp.ctx.project_id, state, inp.ctx.run_id)
        return RenderOutput(
            verdict=_verdict(report), report=arts["REPORT.md"], status_json=arts["status.json"]
        )
