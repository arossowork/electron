"""Contracts shared with the engine (ADR 0001, sections 2.1 and 3.1).

The engine-side modules `hwflow.artifacts` and `hwflow.runs` are added by #6/#7. Until they land, the
names and signatures below are copied from the ADR verbatim so platform code can be written and tested.
Once they land, replace the definitions in this file by `from hwflow.artifacts import ...` and
`from hwflow.runs import ...` (same names); nothing else in hwplatform changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Literal, Protocol

# --- artifacts (hwflow/artifacts.py, ADR 2.1) ---------------------------------------------------

TREE_MEDIA_TYPE = "application/vnd.hwflow.tree+json"
DEFAULT_MEDIA_TYPE = "application/octet-stream"


@dataclass(frozen=True)
class ArtifactRef:
    """sha256: 64 lowercase hex of the bytes; name: logical, e.g. "board", "fab", "sim.result.json"."""

    sha256: str
    size: int
    media_type: str = DEFAULT_MEDIA_TYPE
    name: str = ""


class ArtifactNotFound(KeyError):
    pass


class StoreUnavailable(OSError):
    """Retryable (network, 5xx); anything else is a bug."""


class ArtifactStore(Protocol):
    """Content-addressed bytes. Idempotent puts; the store keeps bytes only, the ref carries the metadata."""

    def put_file(self, path: Path, *, media_type: str = ..., name: str = "") -> ArtifactRef: ...
    def put_bytes(self, data: bytes, *, media_type: str = ..., name: str = "") -> ArtifactRef: ...
    def open(self, sha256: str) -> AbstractContextManager[BinaryIO]: ...
    def get_file(self, sha256: str, dest: Path) -> Path: ...
    def exists(self, sha256: str) -> bool: ...
    def stat(self, sha256: str) -> ArtifactRef | None: ...


# --- runs (hwflow/runs.py, ADR 3.1) -------------------------------------------------------------

RunKind = Literal["build", "design", "eval"]


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    waiting = "waiting"  # waiting = on a person (question, key)
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"  # terminal: succeeded, failed, cancelled


TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled", "failed"},
    "running": {"waiting", "succeeded", "failed", "cancelled"},
    "waiting": {"running", "cancelled", "failed"},
}


class StageStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"
    cancelled = "cancelled"


class InvalidTransition(Exception):
    pass


@dataclass
class StageRecord:
    """key: "check", "layout_attempt.3", "layout_select", "agent.parts"; seq: scheduling order."""

    key: str
    stage: str
    seq: int
    status: StageStatus
    verdict: str | None = None  # StageResult.verdict
    cached: bool = False
    cache_key: str | None = None
    attempt: int = 1  # Temporal activity attempt
    started_at: datetime | None = None
    ended_at: datetime | None = None
    seconds: float | None = None
    summary: dict = field(default_factory=dict)
    result: ArtifactRef | None = None
    outputs: dict[str, ArtifactRef] = field(default_factory=dict)
    error: dict | None = None  # {"type", "message", "retryable"}


@dataclass(frozen=True)
class DesignVersionRef:
    """id: UUIDv7 (platform) or content_sha256 (local); content_sha256 = hash of the tree."""

    id: str
    content_sha256: str
    design: ArtifactRef
    requirements: ArtifactRef | None
    project: ArtifactRef


@dataclass
class Run:
    run_id: str  # UUIDv7 string
    kind: RunKind
    tenant_id: str | None  # None and project = dir name for local runs
    project_id: str
    design_version: DesignVersionRef
    status: RunStatus
    verdict: Literal["PASS", "FAIL"] | None  # REPORT.md verdict, None until rendered
    options: dict  # BuildOptions (or Design/EvalOptions)
    options_hash: str  # 12 hex of result-relevant options + key_dump
    settings: dict  # Settings.snapshot()
    code: dict  # {"hwflow", "git_sha", "stage_versions"}
    tools: dict  # tool versions
    stages: list[StageRecord]
    artifacts: dict[str, ArtifactRef]  # run level: "status.json", "REPORT.md", "manifest.json", "score.json"
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: dict | None = None
    parent_run_id: str | None = None
    workflow_id: str | None = None


class RunStore(Protocol):
    def create(self, run: Run) -> Run: ...
    def get(self, run_id: str) -> Run: ...
    def list(self, project_id: str, *, limit: int = 50, before: str | None = None) -> list[Run]: ...
    def transition(
        self, run_id: str, to: RunStatus, *, error: dict | None = None, verdict: str | None = None
    ) -> Run: ...  # raises InvalidTransition
    def upsert_stage(self, run_id: str, rec: StageRecord) -> None: ...  # idempotent on (run_id, key)
    def set_artifacts(self, run_id: str, artifacts: Mapping[str, ArtifactRef]) -> None: ...
    def append_event(self, run_id: str, type: str, data: dict) -> int: ...  # returns seq
    def project_state(self, project_id: str) -> dict[str, ArtifactRef]: ...
    def set_project_state(self, project_id: str, state: Mapping[str, ArtifactRef], run_id: str) -> None: ...


class AsyncRunStore(Protocol):
    """Mirror of RunStore as coroutines (ADR 3.4); implemented by hwplatform.runs.PgRunStore."""

    async def create(self, run: Run) -> Run: ...
    async def get(self, run_id: str) -> Run: ...
    async def list(self, project_id: str, *, limit: int = 50, before: str | None = None) -> list[Run]: ...
    async def transition(
        self, run_id: str, to: RunStatus, *, error: dict | None = None, verdict: str | None = None
    ) -> Run: ...
    async def upsert_stage(self, run_id: str, rec: StageRecord) -> None: ...
    async def set_artifacts(self, run_id: str, artifacts: Mapping[str, ArtifactRef]) -> None: ...
    async def append_event(self, run_id: str, type: str, data: dict) -> int: ...
    async def project_state(self, project_id: str) -> dict[str, ArtifactRef]: ...
    async def set_project_state(
        self, project_id: str, state: Mapping[str, ArtifactRef], run_id: str
    ) -> None: ...


# --- workflow wire types (ADR 0001, 4.3 and 7.3) -------------------------------------------------
# Frozen dataclasses on the default Temporal JSON converter. Issue #9 (workflows) uses the same names.


@dataclass(frozen=True)
class Ref:
    """Wire form of ArtifactRef."""

    sha256: str
    size: int
    media_type: str
    name: str = ""


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
    design: Ref
    project: Ref
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
