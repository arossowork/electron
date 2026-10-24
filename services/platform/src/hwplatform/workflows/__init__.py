"""Temporal workflows (ADR 0001, 4.4 and 7). Deterministic: they import only temporalio, hwplatform.contracts
and hwflow.buildplan (import-linter contract "workflows are deterministic"). `BuildWorkflow` is #9,
`DesignWorkflow` and `EngineToolWorkflow` are #15; `EvalWorkflow` is #19."""

from .build import BuildWorkflow
from .design import DesignWorkflow
from .eval import EvalWorkflow
from .tool import EngineToolWorkflow

WORKFLOWS = [BuildWorkflow, DesignWorkflow, EngineToolWorkflow, EvalWorkflow]

__all__ = ["WORKFLOWS", "BuildWorkflow", "DesignWorkflow", "EngineToolWorkflow", "EvalWorkflow"]
