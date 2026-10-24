"""EngineToolWorkflow (ADR 0001, 7.6): one engine lookup or command of an agent tool, as one activity on the
queue that has the tools (ENGINE_TOOL_QUEUE). An activity cannot schedule another activity, so the agent's MCP
tools start this workflow and wait for its result."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from ..contracts import (
        A_ENGINE_TOOL,
        ENGINE_TOOL_DEFAULT_POLICY,
        ENGINE_TOOL_POLICY,
        ENGINE_TOOL_QUEUE,
        ENGINE_TOOL_WORKFLOW,
        Q_ENGINE,
        EngineToolInput,
        EngineToolOutput,
    )
    from .build import retry


@workflow.defn(name=ENGINE_TOOL_WORKFLOW)
class EngineToolWorkflow:
    @workflow.run
    async def run(self, inp: EngineToolInput) -> EngineToolOutput:
        p = ENGINE_TOOL_POLICY.get(inp.tool, ENGINE_TOOL_DEFAULT_POLICY)
        return await workflow.execute_activity(
            A_ENGINE_TOOL,
            inp,
            result_type=EngineToolOutput,
            task_queue=ENGINE_TOOL_QUEUE.get(inp.tool, Q_ENGINE),
            start_to_close_timeout=timedelta(seconds=p.start_to_close_s),
            heartbeat_timeout=timedelta(seconds=p.heartbeat_s) if p.heartbeat_s else None,
            retry_policy=retry(p),
            summary=inp.tool,
        )
