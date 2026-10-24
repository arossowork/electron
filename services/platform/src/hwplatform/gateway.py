"""The API's door to Temporal: start, cancel and signal workflows (ADR 0001, sections 4.4, 4.6, 7.3).

The routes depend on the `WorkflowGateway` interface only, so tests inject a fake and the API runs without a
Temporal server for everything that does not touch a workflow. The workflows live in `hwplatform.workflows`
(BuildWorkflow, #9; DesignWorkflow is #15) and run in `hwplatform-worker`; the API never imports them: it
starts them by name with the ADR's workflow IDs, task queue and argument dataclasses (`hwplatform.contracts`).
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from .contracts import (
    BUILD_WORKFLOW,
    DESIGN_WORKFLOW,
    SIGNAL_ANSWER,
    SIGNAL_RETRY,
    BuildInput,
    DesignInput,
    QuestionAnswer,
)
from .settings import Settings


class GatewayError(Exception):
    """The workflow engine could not do what was asked (unreachable, rejected)."""


class WorkflowNotFound(GatewayError):
    """No such workflow (never started, or its history is gone)."""


def build_workflow_id(tenant_id: str, project_id: str, content_sha256: str, options_hash: str) -> str:
    """ADR 4.4: build-<tenant_id>-<project_id>-<content_sha256[:16]>-<options_hash>."""
    return f"build-{tenant_id}-{project_id}-{content_sha256[:16]}-{options_hash}"


def design_workflow_id(tenant_id: str, project_id: str, run_id: str) -> str:
    """ADR 4.4: design-<tenant_id>-<project_id>-<run_id>."""
    return f"design-{tenant_id}-{project_id}-{run_id}"


def search_attributes(attrs: dict[str, str]):
    """TypedSearchAttributes of the Keyword attributes registered by hwplatform-migrate (ADR 4.4)."""
    from temporalio.common import SearchAttributeKey, SearchAttributePair, TypedSearchAttributes

    return TypedSearchAttributes(
        [SearchAttributePair(SearchAttributeKey.for_keyword(k), v) for k, v in attrs.items()]
    )


def run_attributes(tenant_id: str, project_id: str, run_id: str, kind: str) -> dict[str, str]:
    return {"HwTenantId": tenant_id, "HwProjectId": project_id, "HwRunId": run_id, "HwRunKind": kind}


class WorkflowGateway(Protocol):
    async def start_build(self, workflow_id: str, arg: BuildInput) -> None:
        """Start BuildWorkflow on the engine queue. A workflow already running under this ID is kept
        (conflict policy USE_EXISTING); that is not an error."""
        ...

    async def start_design(self, workflow_id: str, arg: DesignInput) -> None: ...

    async def cancel(self, workflow_id: str) -> None:
        """Request cancellation (`handle.cancel()`); the workflow records the `cancelled` state itself."""
        ...

    async def signal_answer(self, workflow_id: str, answer: QuestionAnswer) -> None: ...

    async def signal_retry(self, workflow_id: str) -> None:
        """Signal `retry` (ADR 7.4): a run waiting with llm_key_invalid tries again with the new key."""
        ...

    async def close(self) -> None: ...


class TemporalGateway:
    """The real gateway on the `temporalio` client. Connects on first use, so a Temporal outage does not stop
    the API from serving everything else."""

    def __init__(self, settings: Settings):
        self.address = settings.temporal_address
        self.namespace = settings.temporal_namespace
        self.queue = settings.temporal_engine_queue
        self._client = None
        self._lock = asyncio.Lock()

    async def _get(self):
        from temporalio.client import Client

        async with self._lock:
            if self._client is None:
                try:
                    self._client = await asyncio.wait_for(
                        Client.connect(self.address, namespace=self.namespace), timeout=10
                    )
                except Exception as e:
                    raise GatewayError(f"cannot reach Temporal at {self.address}: {type(e).__name__}") from e
            return self._client

    async def _start(self, name: str, workflow_id: str, arg, ctx_attrs: dict[str, str]) -> None:
        from temporalio.common import (
            SearchAttributeKey,
            SearchAttributePair,
            TypedSearchAttributes,
            WorkflowIDConflictPolicy,
            WorkflowIDReusePolicy,
        )

        client = await self._get()
        attrs = TypedSearchAttributes(
            [SearchAttributePair(SearchAttributeKey.for_keyword(k), v) for k, v in ctx_attrs.items()]
        )
        try:
            await client.start_workflow(
                name,
                arg,
                id=workflow_id,
                task_queue=self.queue,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                search_attributes=attrs,
            )
        except Exception as e:
            raise GatewayError(f"cannot start {name}: {type(e).__name__}: {e}") from e

    async def start_build(self, workflow_id: str, arg: BuildInput) -> None:
        await self._start(
            BUILD_WORKFLOW,
            workflow_id,
            arg,
            {
                "HwTenantId": arg.ctx.tenant_id,
                "HwProjectId": arg.ctx.project_id,
                "HwRunId": arg.ctx.run_id,
                "HwRunKind": "build",
            },
        )

    async def start_design(self, workflow_id: str, arg: DesignInput) -> None:
        await self._start(
            DESIGN_WORKFLOW,
            workflow_id,
            arg,
            {
                "HwTenantId": arg.ctx.tenant_id,
                "HwProjectId": arg.ctx.project_id,
                "HwRunId": arg.ctx.run_id,
                "HwRunKind": "design",
            },
        )

    async def _handle(self, workflow_id: str):
        return (await self._get()).get_workflow_handle(workflow_id)

    async def cancel(self, workflow_id: str) -> None:
        from temporalio.service import RPCError, RPCStatusCode

        handle = await self._handle(workflow_id)
        try:
            await handle.cancel()
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                raise WorkflowNotFound(workflow_id) from e
            raise GatewayError(f"cannot cancel {workflow_id}: {e.status.name}") from e

    async def signal_answer(self, workflow_id: str, answer: QuestionAnswer) -> None:
        from temporalio.service import RPCError, RPCStatusCode

        handle = await self._handle(workflow_id)
        try:
            await handle.signal(SIGNAL_ANSWER, answer)
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                raise WorkflowNotFound(workflow_id) from e
            raise GatewayError(f"cannot signal {workflow_id}: {e.status.name}") from e

    async def signal_retry(self, workflow_id: str) -> None:
        from temporalio.service import RPCError, RPCStatusCode

        handle = await self._handle(workflow_id)
        try:
            await handle.signal(SIGNAL_RETRY)
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                raise WorkflowNotFound(workflow_id) from e
            raise GatewayError(f"cannot signal {workflow_id}: {e.status.name}") from e

    async def close(self) -> None:
        self._client = None  # temporalio clients hold no resource that needs an explicit close
