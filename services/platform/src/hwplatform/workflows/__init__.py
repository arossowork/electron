"""Temporal workflows (ADR 0001, 4.4 and 7). Deterministic: they import only temporalio, hwplatform.contracts
and hwflow.buildplan (import-linter contract "workflows are deterministic"). `BuildWorkflow` is #9,
`EngineToolWorkflow` is #15 (the agent's engine tools); `DesignWorkflow` follows, `EvalWorkflow` is #19."""

from .build import BuildWorkflow
from .tool import EngineToolWorkflow

WORKFLOWS = [BuildWorkflow, EngineToolWorkflow]

__all__ = ["WORKFLOWS", "BuildWorkflow", "EngineToolWorkflow"]
