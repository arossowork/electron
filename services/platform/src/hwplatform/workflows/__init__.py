"""Temporal workflows (ADR 0001, 4.4). Deterministic: they import only temporalio, hwplatform.contracts and
hwflow.buildplan (import-linter contract "workflows are deterministic"). `BuildWorkflow` is #9;
`DesignWorkflow`
(#15) and `EvalWorkflow` (#19) come with their issues."""

from .build import BuildWorkflow

WORKFLOWS = [BuildWorkflow]

__all__ = ["WORKFLOWS", "BuildWorkflow"]
