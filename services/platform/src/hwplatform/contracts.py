"""Platform wire types and names for Temporal workflows and activities (ADR 0001, 4.2 to 4.5 and 7.3).

The shared contracts (ArtifactRef, ArtifactStore, runs, state machine, ids) have ONE source of truth in the
engine: `hwflow.artifacts`, `hwflow.runs`, `hwflow.ids` (stdlib-only, import-linter enforced). Only the names
and dataclasses below are the platform's own. Everything here is stdlib (plus hwflow.artifacts), because the
workflow sandbox imports it (import-linter: "workflows are deterministic"). ArtifactRef is a frozen dataclass,
so it goes over the default Temporal JSON converter as is (no separate wire `Ref`).

Payload rules (ADR 4.3): no secret in any payload (Temporal history is plaintext in Postgres); payloads stay
under 64 KiB, a stage `summary` is capped at 4 KiB.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hwflow.artifacts import ArtifactRef

# --- names (ADR 4.2, 4.4) ---

BUILD_WORKFLOW = "BuildWorkflow"
DESIGN_WORKFLOW = "DesignWorkflow"  # issue #15
EVAL_WORKFLOW = "EvalWorkflow"  # issue #19
SIGNAL_ANSWER = "answer_question"

Q_ENGINE = "hwflow-engine"
Q_SIM = "hwflow-sim"
Q_LAYOUT = "hwflow-layout"
Q_VERIFY = "hwflow-verify"
Q_AGENT = "hwflow-agent"
QUEUES = (Q_ENGINE, Q_SIM, Q_LAYOUT, Q_VERIFY, Q_AGENT)
QUEUE_OF = {
    "check": Q_ENGINE,
    "sch": Q_VERIFY,
    "sim": Q_SIM,
    "layout_attempt": Q_LAYOUT,
    "layout_select": Q_LAYOUT,
    "post": Q_SIM,
    "fab": Q_VERIFY,
    "em": Q_VERIFY,
}
# Worker slots of the reduced stack (ADR 4.2, 8: one pod, 2 vCPU / 4 GB, one layout at a time).
SLOTS = {
    Q_ENGINE: {"workflows": 4, "activities": 2},
    Q_SIM: {"activities": 1},
    Q_LAYOUT: {"activities": 1},
    Q_VERIFY: {"activities": 1},
    Q_AGENT: {"activities": 1},
}

A_RECORD_RUN = "record_run"
A_RECORD_STAGE = "record_stage"
A_RESOLVE_RUN = "resolve_run"
A_RENDER_RUN = "render_run"
A_STAGE = "run_stage_activity"

# --- errors (ADR 4.5): ApplicationError.type ---


class errors:  # noqa: N801 - a namespace of names, as the ADR writes them (hwplatform.contracts.errors)
    DesignInvalid = "DesignInvalid"
    ToolMissing = "ToolMissing"
    ToolCrashed = "ToolCrashed"
    WorkerLost = "WorkerLost"
    StoreUnavailable = "StoreUnavailable"
    LlmRateLimited = "LlmRateLimited"
    LlmKeyInvalid = "LlmKeyInvalid"
    QuestionExpired = "QuestionExpired"
    NON_RETRYABLE = ("DesignInvalid", "ToolMissing", "LlmKeyInvalid", "QuestionExpired")
    RETRYABLE = ("ToolCrashed", "WorkerLost", "StoreUnavailable", "LlmRateLimited")


# --- timeouts and retries (ADR 4.5): seconds; retry = (initial, backoff, max interval, attempts) ---


@dataclass(frozen=True)
class Policy:
    start_to_close_s: float
    heartbeat_s: float | None
    initial_s: float
    backoff: float
    max_interval_s: float
    attempts: int


RECORD_POLICY = Policy(30, None, 1, 2, 30, 10)
ENGINE_POLICY = Policy(120, None, 2, 2, 60, 5)  # resolve_run, render_run, create_design_version
STAGE_POLICY = {
    "check": Policy(300, 30, 5, 2, 120, 3),
    "sch": Policy(600, 30, 5, 2, 120, 3),
    "fab": Policy(600, 30, 5, 2, 120, 3),
    "sim": Policy(3600, 60, 10, 2, 300, 3),
    "post": Policy(3600, 60, 10, 2, 300, 3),
    "layout_attempt": Policy(600, 60, 10, 2, 300, 2),  # start_to_close: layout.budget_s + 10 min
    "layout_select": Policy(900, 60, 10, 2, 120, 3),
    "em": Policy(7200, 60, 30, 2, 300, 2),
}
HEARTBEAT_EVERY_S = 15.0  # the stage activity heartbeats this often with the child's last progress line
KILL_GRACE_S = 10.0  # SIGTERM to the stage's process group, SIGKILL after this

# --- workflow wire types (ADR 4.3 and 7.3) ---
# Frozen dataclasses on the default Temporal JSON converter.


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


@dataclass(frozen=True)
class StageOut:
    """Wire form of hwflow.pipeline.spec.StageResult (ADR 2.5), plus the Temporal activity attempt that made
    it."""

    stage: str
    verdict: str  # pass | fail | blocked | error
    cache_key: str
    cached: bool
    result: ArtifactRef
    outputs: dict[str, ArtifactRef] = field(default_factory=dict)
    state: dict[str, ArtifactRef] = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    seconds: float = 0.0
    tool_versions: dict = field(default_factory=dict)
    error: dict | None = None
    attempt: int = 1


@dataclass(frozen=True)
class StageActivityInput:
    """Wire form of hwflow.pipeline.spec.StageInput (ADR 2.5) with ctx, the record key and rebuild."""

    ctx: RunCtx
    key: str  # "check", "layout_attempt.3", ...
    stage: str
    design: ArtifactRef
    project: ArtifactRef
    upstream: dict[str, StageOut] = field(default_factory=dict)
    state: dict[str, ArtifactRef | None] = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    rebuild: bool = False


@dataclass(frozen=True)
class ResolveInput:
    ctx: RunCtx
    design: ArtifactRef
    project: ArtifactRef
    options: dict
    settings_overrides: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedRun:
    """resolve_run output: the settings snapshot, code and tool versions, project state refs, and the layout
    result when the project's layout history blocks this structure (the layout is then not scheduled)."""

    settings: dict
    code: dict
    tools: dict
    state: dict[str, ArtifactRef] = field(default_factory=dict)
    layout_blocked: StageOut | None = None


@dataclass(frozen=True)
class RecordRun:
    """record_run: one run transition and its run.* event, in one transaction."""

    ctx: RunCtx
    to: str  # RunStatus value
    error: dict | None = None
    verdict: str | None = None
    by: str | None = None  # run.cancelled: who


@dataclass(frozen=True)
class RecordStage:
    """record_stage: one stage row change and its stage.* event, in one transaction."""

    ctx: RunCtx
    key: str
    stage: str
    seq: int
    status: str  # StageStatus value
    started_at: str | None = None  # RFC 3339 (workflow time)
    ended_at: str | None = None
    out: StageOut | None = None
    reason: str | None = None  # skipped / cancelled
    error: dict | None = None
    attempt: int = 1


@dataclass(frozen=True)
class RecordedRun:
    """record_run's answer: the run's status after the call (another writer, e.g. a cancel of a queued run,
    can
    have finished it first)."""

    status: str
    applied: bool


@dataclass(frozen=True)
class RenderInput:
    ctx: RunCtx


@dataclass(frozen=True)
class RenderOutput:
    verdict: str | None
    report: ArtifactRef | None
    status_json: ArtifactRef | None


@dataclass(frozen=True)
class BuildOutput:
    run_id: str
    status: str
    verdict: str | None
    report: ArtifactRef | None
    status_json: ArtifactRef | None
    stages: dict[str, dict] = field(default_factory=dict)  # key -> {"verdict", "cached", "summary"}
