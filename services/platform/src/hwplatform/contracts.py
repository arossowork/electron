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
# EvalWorkflow -> its parent: a child design run waits out a rate limit; the eval starts no new trial
# meanwhile.
SIGNAL_CHILD_RATE_LIMITED = "child_rate_limited"
SIGNAL_ANSWER = "answer_question"
# PUT /tenant/llm-keys/anthropic signals `retry`: a run waiting with llm_key_invalid tries again (ADR 7.4).
SIGNAL_RETRY = "retry"
ENGINE_TOOL_WORKFLOW = "EngineToolWorkflow"  # one engine lookup or command of an agent tool (ADR 7.6)

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
# DesignWorkflow (#15, ADR 7)
A_AGENT_STAGE = "agent_stage"
A_RECORD_QUESTION = "record_question"
A_GET_DESIGN_VERSION = "get_design_version"
A_CREATE_DESIGN_VERSION = "create_design_version"
A_ENGINE_TOOL = "engine_tool"
A_SIMULATE_ANSWER = "simulate_answer"  # #19: the evals product-person simulator (hwflow-agent)
# EvalWorkflow (#19, engine queue)
A_PREPARE_EVAL_TRIAL = "prepare_eval_trial"
A_PREPARE_SCORE_BUILD = "prepare_score_build"
A_SCORE_TRIAL = "score_trial"
A_FINISH_EVAL = "finish_eval"

DESIGN_STAGES = ("requirements", "partition", "parts", "circuit", "review", "layout", "firmware")
# Engine tools of the agent's MCP server (ADR 7.6). The agent image has no KiCad, no ngspice and no internet
# egress, so lookups, datasheet fetches and engine commands run as an activity on these queues.
ENGINE_TOOLS = (
    "lib_sym",
    "lib_show",
    "lib_fp",
    "lib_pads",
    "parts_search",
    "fetch_url",
    "trace",
    "fw",
    "floorplan",
    "optimize",
)
ENGINE_TOOL_QUEUE = {t: Q_ENGINE for t in ENGINE_TOOLS} | {"optimize": Q_SIM}

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
# agent_stage (ADR 4.5): 120 min, heartbeat 120 s, retry 30 s x2 up to 10 min, 5 attempts. LlmRateLimited is
# non-retryable here, like LlmKeyInvalid: the DesignWorkflow handles both (a backoff timer, a wait for
# `retry`), so a long rate limit or a key fix never uses up the retry budget of a worker crash (ADR 7.6).
AGENT_POLICY = Policy(7200, 120, 30, 2, 600, 5)
AGENT_NON_RETRYABLE = (*errors.NON_RETRYABLE, errors.LlmRateLimited)
ENGINE_TOOL_POLICY = {"optimize": Policy(3600, 60, 10, 2, 300, 3)}
ENGINE_TOOL_DEFAULT_POLICY = Policy(600, 60, 5, 2, 120, 3)
# The workflow's rate-limit timer: the first wait and the cap (it doubles in between).
RATE_LIMIT_BACKOFF_S = (60.0, 1800.0)
# A known reset time (the API's, or a subscription window's parsed by the credential adapter) is waited
# for up to this long in one timer; a longer limit (a weekly cap) waits again after the next attempt.
RATE_LIMIT_RESET_MAX_S = 86400.0
KEY_RETRY_WAIT_S = 72 * 3600  # ADR 7.4: a run waits this long for `retry` after an invalid key
# agent_stage saves the workspace and the transcript (its heartbeat details) this often.
AGENT_SNAPSHOT_EVERY_S = 300.0
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
    # None: wait for an answer without a limit (ADR 7.6); a number of seconds: then QuestionExpired.
    question_timeout_s: float | None = None
    parent_run_id: str | None = None  # an EvalWorkflow's run (#19)
    # EvalWorkflow trials (#19): `model` replaces the model of every stage; `budget_usd` caps every
    # agent session;
    # `persona` is the fixture's hidden specification, which only `simulate_answer` reads (never the agent);
    # `parent_workflow_id` is told when this run waits out a rate limit.
    model: str = ""
    budget_usd: float | None = None
    persona: str = ""
    parent_workflow_id: str | None = None


@dataclass(frozen=True)
class AgentStageInput:
    """agent_stage (ADR 7.1). `workspace`: the project tree the session starts from; `session`: the saved
    transcript of an earlier session of this stage (resumed when set). `answers`: the product person's answers
    to `questions`; `resume`: why the session goes on ("answers", "retry", "restart")."""

    ctx: RunCtx
    stage: str
    workspace: ArtifactRef
    brief: str
    session: ArtifactRef | None = None
    session_id: str | None = None
    answers: dict | None = None
    questions: list[dict] = field(default_factory=list)
    resume: str | None = None
    round: int = 0  # question rounds so far in this run (the agent cites "PM round N, qX")
    model: str = ""  # replaces the stage's model (an alias or an ID); "" = the platform's setting
    budget_usd: float | None = None  # replaces the session cost cap when lower than the platform's


@dataclass(frozen=True)
class AgentStageOutput:
    """outcome "done" | "needs_answers". `builds`: the linked BuildWorkflow IDs the session started (cancelled
    with the run, ADR 4.6); `verdict`: the last linked build's verdict; `usage`: tokens per model."""

    outcome: str
    workspace: ArtifactRef
    session: ArtifactRef | None
    session_id: str | None
    questions: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    builds: list[str] = field(default_factory=list)
    verdict: str | None = None
    summary: str = ""


@dataclass(frozen=True)
class RecordQuestion:
    """record_question (ADR 7.3), a local activity. op "ask": the questions row (id = question_id, so a
    retried call inserts nothing new) with question.asked, then running -> waiting (reason question).
    "resumed": waiting -> running (run.resumed). "expired" / "cancelled": the row's status (question.expired).
    "key_wait": running -> waiting (reason llm_key_invalid); "key_resumed": back to running."""

    ctx: RunCtx
    op: str
    question_id: str | None = None
    stage: str = ""
    questions: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class SimulateInput:
    """simulate_answer (#19): the simulated product person answers `questions` from `persona`, the fixture's
    hidden specification. The credential is the tenant's, like the agent's."""

    ctx: RunCtx
    question_id: str
    stage: str
    questions: list[dict]
    persona: str
    model: str = ""
    round: int = 0


@dataclass(frozen=True)
class DesignVersionInput:
    """get_design_version (design_version_id) or create_design_version (a workspace tree, source "agent")."""

    ctx: RunCtx
    design_version_id: str | None = None
    workspace: ArtifactRef | None = None
    parent_id: str | None = None
    message: str = ""


@dataclass(frozen=True)
class DesignVersionOut:
    id: str
    content_sha256: str
    design: ArtifactRef
    project: ArtifactRef
    created: bool = False


@dataclass(frozen=True)
class DesignOutput:
    run_id: str
    status: str
    verdict: str | None
    design_version_id: str
    stages: dict[str, dict] = field(default_factory=dict)  # stage -> {"rounds", "summary", "verdict"}


@dataclass(frozen=True)
class EngineToolInput:
    """One engine tool call of an agent session (ADR 7.6). `workspace`: the project tree, for the commands
    that read the project (trace, fw, floorplan, optimize); `status_json`: the last linked build's
    status.json."""

    ctx: RunCtx
    tool: str
    args: dict = field(default_factory=dict)
    workspace: ArtifactRef | None = None
    status_json: ArtifactRef | None = None


@dataclass(frozen=True)
class EngineToolOutput:
    text: str
    ok: bool = True
    workspace: ArtifactRef | None = (
        None  # the project tree after a command that writes (fw --scaffold, optimize)
    )
    files: dict[str, ArtifactRef] = field(default_factory=dict)  # fetch_url: name -> stored file


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
    can have finished it first)."""

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


# --- EvalWorkflow (#19, ADR 7.7) ---


@dataclass(frozen=True)
class EvalInput:
    """`fixtures`: the fixture documents (evals/fixtures/*.yaml: id, brief, hidden_spec, mode, requirements,
    planted_conflicts, expected_requirements, limits). `baseline`: an eval document (`hwflow.eval/1`)
    stored as an
    artifact. `models`: model aliases or IDs, "" = the platform's setting. `max_parallel`: trials at
    once, already
    capped by the tenant's `max_concurrent_runs` by the API. `budget_usd`: cost cap of every agent session."""

    ctx: RunCtx
    suite: str
    fixtures: list[dict]
    baseline: ArtifactRef | None = None
    baseline_name: str = ""
    models: list[str] = field(default_factory=lambda: [""])
    repetitions: int = 1
    max_parallel: int = 1
    budget_usd: float | None = None
    question_timeout_s: float | None = None
    options: dict = field(default_factory=dict)  # echoed into the report


@dataclass(frozen=True)
class PrepareTrial:
    """prepare_eval_trial: the project, design version and run row of one trial (fixture x model x
    repetition). `ctx` is the eval run's."""

    ctx: RunCtx
    key: str
    fixture: dict
    model: str
    trial: int
    budget_usd: float | None = None
    question_timeout_s: float | None = None


@dataclass(frozen=True)
class PreparedTrial:
    key: str
    design: DesignInput  # ready to start: its ctx is the trial's own project and run
    fixture_id: str = ""
    model: str = ""
    trial: int = 1


@dataclass(frozen=True)
class ScoreTrialInput:
    """score_trial: reads the trial's design run (manifest, events), its final design version and the scoring
    build; `build_run_id` is that build's run ("" when none ran). `design_status` / `design_error`:
    how the design
    run ended."""

    ctx: RunCtx  # the eval run's
    trial: PreparedTrial
    fixture: dict
    build_run_id: str = ""
    design_status: str = "succeeded"
    design_error: dict | None = None


@dataclass(frozen=True)
class ScoreBuildPlan:
    """prepare_score_build: the build that produces the metrics of a trial (on its final design version), with
    its run row created; None when the design run left no design version to build."""

    build: BuildInput | None = None


@dataclass(frozen=True)
class TrialResult:
    key: str
    card: dict
    card_ref: ArtifactRef


@dataclass(frozen=True)
class FinishEvalInput:
    """finish_eval: aggregate the cards, compare with the baseline, store eval.json and REPORT.md as artifacts
    of the eval run. `errors`: trials that produced no card."""

    ctx: RunCtx
    suite: str
    cards: list[dict]
    errors: list[dict]
    baseline: ArtifactRef | None = None
    baseline_name: str = ""
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FinishEvalOut:
    verdict: str
    report: ArtifactRef
    eval_json: ArtifactRef


@dataclass(frozen=True)
class EvalOutput:
    run_id: str
    status: str
    verdict: str | None
    trials: int = 0
    errors: int = 0
    report: ArtifactRef | None = None
    eval_json: ArtifactRef | None = None
