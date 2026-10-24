"""Temporal workflows (ADR 0001, 4.4 and 7). Deterministic: they import only temporalio, hwplatform.contracts
and hwflow.buildplan (import-linter contract "workflows are deterministic"). `BuildWorkflow` is #9,
`DesignWorkflow` and `EngineToolWorkflow` are #15; `EvalWorkflow` comes with #19."""

from .build import BuildWorkflow
from .design import DesignWorkflow
from .tool import EngineToolWorkflow

WORKFLOWS = [BuildWorkflow, DesignWorkflow, EngineToolWorkflow]

__all__ = ["WORKFLOWS", "BuildWorkflow", "DesignWorkflow", "EngineToolWorkflow"]
