"""Platform-specific wire types for Temporal workflows (ADR 0001, 4.3 and 7.3).

The shared contracts (ArtifactRef, ArtifactStore, runs, state machine, ids) have ONE source of truth in the
engine: `hwflow.artifacts`, `hwflow.runs`, `hwflow.ids` (stdlib-only, import-linter enforced). Only the
dataclasses below are the platform's own. ArtifactRef is a frozen dataclass, so it goes over the default
Temporal JSON converter as is (no separate wire `Ref`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hwflow.artifacts import ArtifactRef

# --- workflow wire types (ADR 0001, 4.3 and 7.3) -------------------------------------------------
# Frozen dataclasses on the default Temporal JSON converter. Issue #9 (workflows) uses the same names.


@dataclass(frozen=True)
class RunCtx:
    """In every activity input; the activity opens Database.tx(tenant_id=ctx.tenant_id)."""

    tenant_id: str
    project_id: str
    run_id: str
    workflow_id: str


@dataclass(frozen=True)
class BuildInput:
    """settings_overrides: allowlisted keys only (layout.budget_s, layout.seed0, layout.asic.*)."""

    ctx: RunCtx
    design_version_id: str
    design: ArtifactRef
    project: ArtifactRef
    options: dict
    settings_overrides: dict = field(default_factory=dict)
    parent_run_id: str | None = None


@dataclass(frozen=True)
class DesignInput:
    ctx: RunCtx
    design_version_id: str
    brief: str
    answerer: str = "human"  # "human" | "simulator"
    stages: list[str] = field(default_factory=list)  # empty = DESIGN_STAGES


@dataclass(frozen=True)
class QuestionAnswer:
    """Payload of the signal `answer_question` (ADR 7.3)."""

    question_id: str
    answers: dict
    answered_by: str
