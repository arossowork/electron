"""The platform side of the agent's tools (ADR 0001, 7.2 and 7.6): linked builds and engine tool workflows.

`build`: store the workspace as a design version (source "agent"), create the build's run row (parent_run_id =
the design run, so it does not count against the tenant's quota, ADR 4.8), start BuildWorkflow under the ADR
4.4 ID rule (ALLOW_DUPLICATE + USE_EXISTING: a retried tool call joins the build already running) and wait for
it. Linked, not child, workflows: an activity cannot own children; the design run cancels them by ID (4.6).

`engine`: run one EngineToolWorkflow (one activity on ENGINE_TOOL_QUEUE[tool]) and wait for it. The agent
worker needs no KiCad, ngspice or internet egress for that.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from hwflow.artifacts import ArtifactRef
from hwflow.runs import Run, RunStatus
from sqlalchemy import text

from .. import run_options
from ..contracts import (
    BUILD_WORKFLOW,
    ENGINE_TOOL_WORKFLOW,
    Q_ENGINE,
    BuildInput,
    BuildOutput,
    EngineToolInput,
    EngineToolOutput,
    RunCtx,
)
from ..db import Database
from ..designs import version_from_tree
from ..gateway import build_workflow_id, run_attributes, search_attributes
from ..runs import PgRunStore
from ..storage import S3ArtifactStore
from . import workspace
from .tools import BuildResult, EngineResult

log = logging.getLogger("hwplatform.agent")
REPORT_MAX = 200_000


class PlatformBackend:
    def __init__(
        self,
        db: Database,
        store: S3ArtifactStore,
        client,
        ctx: RunCtx,
        *,
        on_wait: Callable[[str], None] = lambda line: None,
    ):
        self.db, self.store, self.client, self.ctx = db, store, client, ctx
        self.on_wait = on_wait
        self.parent_version: str | None = None

    def _read(self, ref: ArtifactRef | None, limit: int = REPORT_MAX) -> bytes | None:
        if ref is None:
            return None
        with self.store.open(ref.sha256) as f:
            return f.read(limit)

    async def save_version(self, ws: Path, message: str):
        tree = await asyncio.to_thread(workspace.version_tree, self.store, ws)
        dv, _ = await version_from_tree(
            self.db,
            self.store,
            self.ctx.tenant_id,
            self.ctx.project_id,
            tree,
            source="agent",
            created_by_run=self.ctx.run_id,
            parent_id=self.parent_version,
            message=message,
        )
        self.parent_version = dv.id
        return dv

    async def _run_row(self, dv, options: dict, ohash: str, wf_id: str) -> str:
        """The build's run id: the active run of this workflow ID, else a new queued row (run.queued)."""
        async with self.db.tx(tenant_id=self.ctx.tenant_id) as s:
            active = (
                await s.execute(
                    text(
                        "SELECT id FROM runs WHERE workflow_id = :w"
                        " AND status IN ('queued', 'running', 'waiting')"
                    ),
                    {"w": wf_id},
                )
            ).scalar_one_or_none()
        if active is not None:
            return str(active)
        run = Run(
            run_id="",
            kind="build",
            tenant_id=self.ctx.tenant_id,
            project_id=self.ctx.project_id,
            design_version=dv,
            status=RunStatus.queued,
            verdict=None,
            options=options,
            options_hash=ohash,
            settings={},
            code={},
            tools={},
            stages=[],
            artifacts={},
            created_at=datetime.now(UTC),
            parent_run_id=self.ctx.run_id,
            workflow_id=wf_id,
        )
        return (await PgRunStore(self.db, self.ctx.tenant_id).create(run)).run_id

    async def build(self, ws: Path, options: dict) -> BuildResult:
        from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

        opts = run_options.normalize_build_options(options)
        dv = await self.save_version(ws, f"agent build until {opts['until']}")
        ohash = run_options.options_hash(opts)
        wf_id = build_workflow_id(self.ctx.tenant_id, self.ctx.project_id, dv.content_sha256, ohash)
        run_id = await self._run_row(dv, opts, ohash, wf_id)
        arg = BuildInput(
            ctx=RunCtx(self.ctx.tenant_id, self.ctx.project_id, run_id, wf_id),
            design_version_id=dv.id,
            design=dv.design,
            project=dv.project,
            options=opts,
            parent_run_id=self.ctx.run_id,
        )
        handle = await self.client.start_workflow(
            BUILD_WORKFLOW,
            arg,
            id=wf_id,
            task_queue=Q_ENGINE,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            search_attributes=search_attributes(
                run_attributes(self.ctx.tenant_id, self.ctx.project_id, run_id, "build")
            ),
            result_type=BuildOutput,
        )
        self.on_wait(f"build {run_id} until {opts['until']}")
        try:
            out: BuildOutput = await handle.result()
        except asyncio.CancelledError:
            await self.cancel_builds([wf_id])
            raise
        except Exception as e:  # noqa: BLE001 - a failed build is a tool result for the agent, not a crash
            return BuildResult(
                run_id, wf_id, "failed", None, f"the build failed: {type(e).__name__}: {e}"[:2000]
            )
        report = await asyncio.to_thread(self._read, out.report)
        status = await asyncio.to_thread(self._read, out.status_json, 16 * 1024 * 1024)
        return BuildResult(
            run_id=out.run_id,
            workflow_id=wf_id,
            status=out.status,
            verdict=out.verdict,
            report=(report or b"").decode("utf-8", "replace"),
            status_json=out.status_json,
            status_bytes=status,
        )

    async def engine(
        self, tool: str, args: dict, ws: Path | None, status_json: ArtifactRef | None
    ) -> EngineResult:
        tree = await asyncio.to_thread(workspace.snapshot, self.store, ws) if ws is not None else None
        out: EngineToolOutput = await self.client.execute_workflow(
            ENGINE_TOOL_WORKFLOW,
            EngineToolInput(ctx=self.ctx, tool=tool, args=args, workspace=tree, status_json=status_json),
            id=f"tool-{self.ctx.run_id}-{uuid.uuid4().hex[:12]}",
            task_queue=Q_ENGINE,
            result_type=EngineToolOutput,
        )
        return EngineResult(out.text, out.ok, out.workspace, dict(out.files))

    async def apply(self, tree: ArtifactRef, ws: Path) -> list[str]:
        return await asyncio.to_thread(workspace.apply_tree, self.store, tree, ws)

    async def fetch_file(self, ref: ArtifactRef, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self.store.get_file, ref.sha256, dest)

    async def cancel_builds(self, workflow_ids: list[str]) -> None:
        for wf in workflow_ids:
            try:
                await self.client.get_workflow_handle(wf).cancel()
            except Exception as e:  # noqa: BLE001 - already finished or gone
                log.info("cancel of linked build %s: %s", wf, type(e).__name__)
