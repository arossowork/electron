from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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

RunKindT = Literal["build", "design"]
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

    kind: RunKindT = "build"
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


class AnswerIn(BaseModel):
    answers: dict[str, Any] = Field(description="question id -> answer")


class ErrorOut(BaseModel):
    detail: str
