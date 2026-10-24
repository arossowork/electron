from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    tenant_name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(default="", max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)
    tenant_id: uuid.UUID | None = None  # pick one of the user's tenants; default: the oldest membership


class RefreshIn(BaseModel):
    refresh_token: str = Field(max_length=512)


class SwitchTenantIn(BaseModel):
    refresh_token: str = Field(max_length=512)
    tenant_id: uuid.UUID


class AcceptedOut(BaseModel):
    detail: str


class VerifyIn(BaseModel):
    token: str = Field(max_length=512)


class AcceptInviteIn(BaseModel):
    token: str = Field(max_length=512)


class AcceptInviteOut(BaseModel):
    tenant_id: uuid.UUID
    role: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int
    tenant_id: uuid.UUID
    user_id: uuid.UUID


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    slug: str
    max_concurrent_runs: int
    created_at: datetime


class TenantRef(BaseModel):
    id: uuid.UUID
    name: str
    role: str


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    tenant_id: uuid.UUID
    role: str
    tenants: list[TenantRef]


class MemberIn(BaseModel):
    email: EmailStr
    role: Literal["admin", "member"] = "member"


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    created_at: datetime


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,59}$")


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    created_by: uuid.UUID | None
    created_at: datetime


# --- design versions, runs, artifacts, questions (#12) ------------------------------------------------

T = TypeVar("T")

RunKindT = Literal["build", "design", "eval"]
StartKindT = Literal[
    "build", "design"
]  # kinds a client starts with POST /projects/{id}/runs; evals: POST /evals
RunStatusT = Literal["queued", "running", "waiting", "succeeded", "failed", "cancelled"]


class Page(BaseModel, Generic[T]):
    """Cursor pagination: pass `next_cursor` back as `?cursor=` for the next page (null = last page)."""

    items: list[T]
    next_cursor: str | None = None


class DesignVersionOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    seq: int
    content_sha256: str
    design_sha256: str
    requirements_sha256: str | None
    source: Literal["upload", "agent"]
    parent_id: uuid.UUID | None
    message: str
    created_by: uuid.UUID | None
    created_at: datetime


class TreeFileOut(BaseModel):
    path: str
    sha256: str
    size: int
    mode: int


class DesignVersionDetailOut(DesignVersionOut):
    files: list[TreeFileOut]


class RunIn(BaseModel):
    """`options` for kind build: until, mc, reuse, force, rebuild, auto, attempts, retry_layout, seeds, iters,
    passes, brief (a bool). For kind design: brief (text, required), answerer ("human"), stages."""

    kind: StartKindT = "build"
    design_version_id: uuid.UUID
    options: dict[str, Any] = Field(default_factory=dict)
    settings_overrides: dict[str, Any] = Field(
        default_factory=dict, description="allowlisted: layout.budget_s, layout.seed0, layout.asic.*"
    )


class StageOut(BaseModel):
    key: str
    stage: str
    seq: int
    status: str
    verdict: str | None
    cached: bool
    attempt: int
    started_at: datetime | None
    ended_at: datetime | None
    seconds: float | None
    summary: dict[str, Any]
    error: dict[str, Any] | None


class RunSummaryOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    design_version_id: uuid.UUID
    kind: RunKindT
    status: RunStatusT
    verdict: Literal["PASS", "FAIL"] | None
    options_hash: str
    parent_run_id: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None


class RunOut(RunSummaryOut):
    workflow_id: str
    options: dict[str, Any]
    error: dict[str, Any] | None
    artifacts: list[str]
    stages: list[StageOut]


class ArtifactOut(BaseModel):
    name: str
    stage_key: str | None
    sha256: str
    size: int
    media_type: str


class QuestionOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    stage: str
    status: Literal["open", "answered", "expired", "cancelled"]
    questions: list[dict[str, Any]]
    answers: dict[str, Any] | None
    answered_by: uuid.UUID | None
    asked_at: datetime
    answered_at: datetime | None
    auto_answered: bool = Field(
        default=False, description="nobody answered in time: the agent's recommended options were used"
    )
    default_answers: dict[str, Any] | None = None
    overridden_at: datetime | None = None


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ChatMessageOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    role: Literal["user", "agent"]
    body: str
    user_id: uuid.UUID | None
    stage: str | None
    created_at: datetime
    delivered_at: datetime | None


class AnswerIn(BaseModel):
    answers: dict[str, Any] = Field(description="question id -> answer")


class ErrorOut(BaseModel):
    detail: str


# --- evaluations (#19) -----------------------------------------------------------------------------------


# a keyword is text; fixtures write numbers too (15 in `15 x 15 mm`)
Keyword = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, int | float) else v)]


class ConflictIn(BaseModel):
    id: str = Field(min_length=1, max_length=60)
    desc: str = Field(default="", max_length=500)
    keywords: list[list[Keyword]] = Field(
        description="all groups must hit; a group hits when any keyword is present"
    )


class FixtureIn(BaseModel):
    """One product of an evaluation (evals/fixtures/*.yaml). `hidden_spec` is known only to the simulated product
    person; the agent sees `brief`. `mode: from_requirements` skips the interview (`requirements` is the final
    requirements.yaml) and starts at the partition stage."""

    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$")
    brief: str = Field(min_length=1, max_length=20_000)
    hidden_spec: str = Field(default="", max_length=20_000)
    mode: Literal["full", "from_requirements"] = "full"
    requirements: str = Field(default="", max_length=100_000)
    planted_conflicts: list[ConflictIn] = Field(default_factory=list, max_length=50)
    expected_requirements: list[list[Keyword]] = Field(default_factory=list, max_length=100)
    limits: dict[str, float] = Field(default_factory=dict)


class EvalIn(BaseModel):
    """`fixtures`: fixture documents, or ids of fixtures the operator installed (HWPLATFORM_EVAL_FIXTURES_DIR).
    `baseline_run_id` / `baseline`: compare with an earlier evaluation or an inline eval document (for example
    the stored `evals/baselines/<name>/baseline.json`); both absent: the suite's promoted baseline, if any."""

    suite: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$")
    fixtures: list[FixtureIn | str] = Field(min_length=1, max_length=50)
    models: list[str] = Field(
        default_factory=list, max_length=6, description="model aliases or IDs; empty = the platform's setting"
    )
    repetitions: int = Field(default=1, ge=1, le=10)
    max_parallel: int | None = Field(default=None, ge=1, le=20)
    budget_usd: float | None = Field(
        default=None, gt=0, le=1000, description="cost cap of every agent session"
    )
    baseline_run_id: uuid.UUID | None = None
    baseline: dict[str, Any] | None = None


class EvalSummaryOut(RunSummaryOut):
    suite: str


class EvalOut(RunOut):
    suite: str
    children: list[RunSummaryOut]


class BaselineIn(BaseModel):
    name: str = Field(default="", max_length=200)


class BaselineOut(BaseModel):
    id: uuid.UUID
    suite: str
    run_id: uuid.UUID | None
    sha256: str
    name: str
    promoted_by: uuid.UUID | None
    promoted_at: datetime
