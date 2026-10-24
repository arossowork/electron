"""Runs: ids, the run and stage state machines, the `hwflow.run/1` manifest, the file run store and the build/ views
(ADR 0001, section 3).

Stdlib only (with hwflow.artifacts and hwflow.ids), so hwplatform can import these names: `TRANSITIONS`,
`STAGE_TERMINAL`, `run_to_manifest` and the dataclasses are the one source of truth for the CLI's FileRunStore and the
platform's PgRunStore.

FileRunStore layout (ADR 3.3)::

    <project>/build/runs/<run_id>/manifest.json   rewritten atomically on every change
    <project>/build/runs/<run_id>/events.jsonl    append-only, one CloudEvents envelope (ADR 5.1) per line
    <project>/build/runs/<run_id>/<stage>-<hex>/  the run's scratch dirs while a stage runs (execute_stage)
    <project>/build/runs/state.json               project state refs {name: ref + run_id, "run_id": last writer}
    <project>/build/runs/LATEST                   run_id of the newest started run

Writes take the `build/.status.lock` flock. `checkout` projects the run into build/ (status.json, outputs) only while
the run is LATEST, so an older run never overwrites a newer one and two builds of one project do not mix.
"""

from __future__ import annotations

import dataclasses
import fcntl
import json
import os
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from .artifacts import TREE_MEDIA_TYPE, ArtifactRef, ArtifactStore, get_tree, read_tree, ref_from_dict, ref_to_dict, sha256_file
from .ids import uuid7

MANIFEST_SCHEMA = "hwflow.run/1"
RunKind = Literal["build", "design", "eval"]


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    waiting = "waiting"  # waiting = on a person (question, key)
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"  # terminal: succeeded, failed, cancelled


# The run state machine. hwplatform.runs.PgRunStore imports this table (its UPDATE ... WHERE status = ANY(allowed_from)
# is built from it); FileRunStore checks it under the lock.
TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled", "failed"},
    "running": {"waiting", "succeeded", "failed", "cancelled"},
    "waiting": {"running", "cancelled", "failed"},
}
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


class StageStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"
    cancelled = "cancelled"


STAGE_TERMINAL = frozenset({"succeeded", "failed", "skipped", "cancelled"})
# events of the stage states (ADR 5.2); pending emits nothing
STAGE_EVENT = {"running": "stage.started", "succeeded": "stage.completed", "failed": "stage.failed",
               "skipped": "stage.skipped"}
RUN_EVENT = {("queued", "running"): "run.started", ("running", "waiting"): "run.waiting",
             ("waiting", "running"): "run.resumed"}
USER_VISIBLE = frozenset({"REPORT.md", "board", "fab", "manifest.json"})  # artifact.created: the last path segment


class InvalidTransition(Exception):
    pass


class RunNotFound(KeyError):
    pass


def allowed_from(to: str) -> list[str]:
    """The states a run may move to `to` from (sorted)."""
    return sorted(f for f, tos in TRANSITIONS.items() if str(to) in tos)


def check_transition(cur: str, to: str) -> None:
    if str(to) not in TRANSITIONS.get(str(cur), set()):
        raise InvalidTransition(f"{cur} -> {to} is not allowed")


def stage_update_applies(prior_status: str | None, prior_attempt: int, status: str, attempt: int) -> bool:
    """A late replay of an earlier state must not undo a finished stage (same rule as PgRunStore)."""
    return not (prior_status in STAGE_TERMINAL and status not in STAGE_TERMINAL and attempt <= prior_attempt)


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
    def transition(self, run_id: str, to: RunStatus, *, error: dict | None = None,
                   verdict: str | None = None) -> Run: ...  # raises InvalidTransition
    def upsert_stage(self, run_id: str, rec: StageRecord) -> None: ...  # idempotent on (run_id, key)
    def set_artifacts(self, run_id: str, artifacts: Mapping[str, ArtifactRef]) -> None: ...
    def append_event(self, run_id: str, type: str, data: dict) -> int: ...  # returns seq
    def project_state(self, project_id: str) -> dict[str, ArtifactRef]: ...
    def set_project_state(self, project_id: str, state: Mapping[str, ArtifactRef], run_id: str) -> None: ...


# ------------------------------------------------------------------ manifest (ADR 3.2)

def rfc3339(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _plain(v: Any) -> Any:
    """asdict leftovers -> JSON: datetimes as RFC 3339 UTC, enums as their value."""
    if isinstance(v, datetime):
        return rfc3339(v)
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_plain(x) for x in v]
    return v


def run_to_manifest(run: Run) -> dict:
    """The Run serialized with dataclasses.asdict plus "schema": "hwflow.run/1"; stages in seq order."""
    d = dataclasses.asdict(run)
    d["stages"] = sorted(d["stages"], key=lambda s: (s["seq"], s["key"]))
    return {"schema": MANIFEST_SCHEMA, **_plain(d)}


def _dt(s: str | None) -> datetime | None:
    return None if s is None else datetime.fromisoformat(s.replace("Z", "+00:00"))


def _ref(d: dict | None) -> ArtifactRef | None:
    return None if d is None else ref_from_dict(d)


def run_from_manifest(m: Mapping[str, Any]) -> Run:
    if m.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"not a {MANIFEST_SCHEMA} manifest: {m.get('schema')!r}")
    dv = m["design_version"]
    stages = [StageRecord(key=s["key"], stage=s["stage"], seq=s["seq"], status=StageStatus(s["status"]),
                          verdict=s.get("verdict"), cached=bool(s.get("cached")), cache_key=s.get("cache_key"),
                          attempt=int(s.get("attempt", 1)), started_at=_dt(s.get("started_at")),
                          ended_at=_dt(s.get("ended_at")), seconds=s.get("seconds"), summary=s.get("summary") or {},
                          result=_ref(s.get("result")),
                          outputs={k: ref_from_dict(v) for k, v in (s.get("outputs") or {}).items()},
                          error=s.get("error")) for s in m.get("stages") or []]
    return Run(run_id=m["run_id"], kind=m["kind"], tenant_id=m.get("tenant_id"), project_id=m["project_id"],
               design_version=DesignVersionRef(id=dv["id"], content_sha256=dv["content_sha256"],
                                               design=ref_from_dict(dv["design"]), requirements=_ref(dv.get("requirements")),
                                               project=ref_from_dict(dv["project"])),
               status=RunStatus(m["status"]), verdict=m.get("verdict"), options=m.get("options") or {},
               options_hash=m.get("options_hash") or "", settings=m.get("settings") or {}, code=m.get("code") or {},
               tools=m.get("tools") or {}, stages=stages,
               artifacts={k: ref_from_dict(v) for k, v in (m.get("artifacts") or {}).items()},
               created_at=_dt(m["created_at"]), started_at=_dt(m.get("started_at")), ended_at=_dt(m.get("ended_at")),
               error=m.get("error"), parent_run_id=m.get("parent_run_id"), workflow_id=m.get("workflow_id"))


def envelope(run: Run, seq: int, type: str, subject: str, data: dict, source: str = "hwflow/cli") -> dict:
    """A CloudEvents 1.0 envelope (ADR 5.1), as events.jsonl and the platform outbox hold it."""
    return {"specversion": "1.0", "id": str(uuid7()), "type": type, "source": source, "subject": subject,
            "time": rfc3339(now()), "datacontenttype": "application/json",
            "dataschema": f"hwflow.events/{type}/1", "tenantid": run.tenant_id, "runid": run.run_id, "seq": seq,
            "data": data}


# ------------------------------------------------------------------ the file store (ADR 3.3)

def _write_atomic(p: Path, text: str):
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class FileRunStore:
    """RunStore on files under `<project>/build/runs/` (CLI and dev)."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    # -- helpers
    @contextmanager
    def lock(self) -> Iterator[None]:
        """The build/.status.lock flock (shared with status.json writers)."""
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root.parent / ".status.lock", "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def run_dir(self, run_id: str) -> Path:
        if not run_id or "/" in run_id or run_id.startswith("."):
            raise ValueError(f"bad run id {run_id!r}")
        return self.root / run_id

    def _read(self, run_id: str) -> Run:
        p = self.run_dir(run_id) / "manifest.json"
        try:
            return run_from_manifest(json.loads(p.read_text()))
        except FileNotFoundError:
            raise RunNotFound(run_id) from None

    def _save(self, run: Run):
        _write_atomic(self.run_dir(run.run_id) / "manifest.json",
                      json.dumps(run_to_manifest(run), indent=1, sort_keys=True))

    def _emit(self, run: Run, type: str, subject: str, data: dict) -> int:
        p = self.run_dir(run.run_id) / "events.jsonl"
        seq = sum(1 for _ in open(p)) + 1 if p.exists() else 1
        with open(p, "a") as fh:
            fh.write(json.dumps(envelope(run, seq, type, subject, data), sort_keys=True, default=str) + "\n")
        return seq

    def latest(self) -> str | None:
        try:
            return (self.root / "LATEST").read_text().strip() or None
        except OSError:
            return None

    def events(self, run_id: str) -> list[dict]:
        p = self.run_dir(run_id) / "events.jsonl"
        return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []

    # -- RunStore
    def create(self, run: Run) -> Run:
        if run.status != RunStatus.queued:
            raise ValueError("a run is created in state queued")
        if not run.run_id:
            run = dataclasses.replace(run, run_id=str(uuid7()))
        with self.lock():
            rd = self.run_dir(run.run_id)
            if (rd / "manifest.json").exists():
                return self._read(run.run_id)
            rd.mkdir(parents=True)
            self._save(run)
            self._emit(run, "run.queued", f"runs/{run.run_id}",
                       {"kind": run.kind, "project_id": run.project_id, "design_version_id": run.design_version.id,
                        "options": run.options, "requested_by": None})
        return run

    def get(self, run_id: str) -> Run:
        return self._read(run_id)

    def list(self, project_id: str, *, limit: int = 50, before: str | None = None) -> list[Run]:
        """Newest first (UUIDv7 order); `before` = run_id of the last row of the previous page."""
        ids = sorted((p.name for p in self.root.iterdir() if (p / "manifest.json").exists()) if self.root.is_dir()
                     else [], reverse=True)
        out = []
        for i in ids:
            if before is not None and i >= before:
                continue
            r = self._read(i)
            if r.project_id == project_id:
                out.append(r)
            if len(out) >= limit:
                break
        return out

    def transition(self, run_id: str, to: RunStatus, *, error: dict | None = None, verdict: str | None = None) -> Run:
        to = RunStatus(to)
        if verdict is not None and verdict not in ("PASS", "FAIL"):
            raise ValueError(f"verdict must be PASS or FAIL, not {verdict!r}")
        with self.lock():
            run = self._read(run_id)
            prev = run.status.value
            check_transition(prev, to.value)
            run.status = to
            if error is not None:
                run.error = error
            if verdict is not None:
                run.verdict = verdict
            if to == RunStatus.running and run.started_at is None:
                run.started_at = now()
            if to.value in TERMINAL:
                run.ended_at = now()
            self._save(run)
            if to == RunStatus.running:
                cur = self.latest()
                if cur is None or run_id > cur:
                    _write_atomic(self.root / "LATEST", run_id + "\n")
            if to == RunStatus.succeeded:
                rep = run.artifacts.get("REPORT.md")
                etype, data = "run.completed", {"status": "succeeded", "verdict": run.verdict,
                                                "report": ref_to_dict(rep) if rep else None,
                                                "artifacts": sorted(run.artifacts)}
            elif to == RunStatus.failed:
                err = run.error or {}
                etype, data = "run.failed", {"status": "failed",
                                             "error": {"type": err.get("type"), "message": err.get("message")}}
            elif to == RunStatus.cancelled:
                etype, data = "run.cancelled", {"status": "cancelled", "by": None}
            elif RUN_EVENT[(prev, to.value)] == "run.started":
                etype, data = "run.started", {"kind": run.kind, "project_id": run.project_id,
                                              "design_version_id": run.design_version.id,
                                              "workflow_id": run.workflow_id, "code": run.code}
            else:
                etype, data = RUN_EVENT[(prev, to.value)], {"reason": None, "question_id": None}
            self._emit(run, etype, f"runs/{run_id}", data)
        return run

    def upsert_stage(self, run_id: str, rec: StageRecord) -> None:
        with self.lock():
            run = self._read(run_id)
            prior = next((s for s in run.stages if s.key == rec.key), None)
            status = StageStatus(rec.status).value
            if prior is not None and not stage_update_applies(prior.status.value, prior.attempt, status, rec.attempt):
                return
            run.stages = [s for s in run.stages if s.key != rec.key] + [dataclasses.replace(rec,
                                                                                         status=StageStatus(status))]
            run.stages.sort(key=lambda s: (s.seq, s.key))
            self._save(run)
            etype = STAGE_EVENT.get(status)
            if etype is None or (prior is not None and prior.status.value == status and prior.attempt == rec.attempt):
                return
            base = {"key": rec.key, "stage": rec.stage}
            data = {"stage.started": {**base, "attempt": rec.attempt},
                    "stage.completed": {**base, "verdict": rec.verdict, "cached": rec.cached, "seconds": rec.seconds,
                                        "summary": rec.summary or {}},
                    "stage.failed": {**base, "error": rec.error, "attempt": rec.attempt},
                    "stage.skipped": {"key": rec.key, "reason": (rec.summary or {}).get("reason", "")}}[etype]
            self._emit(run, etype, f"runs/{run_id}/stages/{rec.key}", data)

    def set_artifacts(self, run_id: str, artifacts: Mapping[str, ArtifactRef]) -> None:
        with self.lock():
            run = self._read(run_id)
            for name, ref in artifacts.items():
                prior = run.artifacts.get(name)
                run.artifacts[name] = dataclasses.replace(ref, name=name)
                if (prior is None or prior.sha256 != ref.sha256) and name.rsplit("/", 1)[-1] in USER_VISIBLE:
                    self._emit(run, "artifact.created", f"runs/{run_id}/artifacts/{name}",
                               {"name": name, "ref": ref_to_dict(dataclasses.replace(ref, name=name))})
            self._save(run)

    def append_event(self, run_id: str, type: str, data: dict) -> int:
        with self.lock():
            return self._emit(self._read(run_id), type, f"runs/{run_id}", data)

    def _state_doc(self) -> dict:
        try:
            return json.loads((self.root / "state.json").read_text())
        except (OSError, ValueError):
            return {}

    def project_state(self, project_id: str) -> dict[str, ArtifactRef]:
        return {k: ref_from_dict(v) for k, v in self._state_doc().items() if isinstance(v, dict)}

    def set_project_state(self, project_id: str, state: Mapping[str, ArtifactRef], run_id: str) -> None:
        """An older run never overwrites a newer one (UUIDv7 order), per name."""
        if not state:
            return
        with self.lock():
            doc = self._state_doc()
            for name, ref in state.items():
                cur = doc.get(name)
                if isinstance(cur, dict) and cur.get("run_id") and cur["run_id"] > run_id:
                    continue
                doc[name] = {**ref_to_dict(ref), "run_id": run_id}
            doc["run_id"] = max(run_id, str(doc.get("run_id") or ""))
            _write_atomic(self.root / "state.json", json.dumps(doc, indent=1, sort_keys=True))


# ------------------------------------------------------------------ build/ views (ADR 3.5)

STATUS_STAGES = ("check", "sch", "sim", "layout", "post", "fab")
STATUS_KEY = {"layout_select": "layout"}  # stage -> its key in status.json


def _read_json(store: ArtifactStore, ref: ArtifactRef) -> Any:
    with store.open(ref.sha256) as fh:
        return json.loads(fh.read())


def status_entries(run: Run, store: ArtifactStore) -> dict[str, dict]:
    """The status.json entry of every stage of the run that produced a result: the stage result dict plus
    `inputs_hash` (cache key prefix), `design_hash` (from the stage summary) and `at` (end time, epoch seconds)."""
    out = {}
    for rec in sorted(run.stages, key=lambda s: (s.seq, s.key)):
        key = STATUS_KEY.get(rec.stage, rec.stage)
        if key not in STATUS_STAGES or rec.result is None or rec.status.value not in ("succeeded", "failed"):
            continue
        r = _read_json(store, rec.result)
        r["inputs_hash"] = (rec.cache_key or "")[:12] or rec.summary.get("inputs_hash", "")
        if rec.summary.get("design_hash"):
            r["design_hash"] = rec.summary["design_hash"]
        r["at"] = rec.ended_at.timestamp() if rec.ended_at else time.time()
        out[key] = r
    return out


def render_status(run: Run, store: ArtifactStore) -> dict:
    """status.json as the run alone gives it: settings, the stage entries, updated. `checkout` merges it into the
    project's status.json (whose `trace`, `score` and command entries the report and other commands write)."""
    return {"settings": run.settings, **status_entries(run, store), "updated": time.strftime("%Y-%m-%d %H:%M:%S")}


def _same_file(p: Path, ref: ArtifactRef) -> bool:
    return p.is_file() and p.stat().st_size == ref.size and sha256_file(p)[0] == ref.sha256


def place(store: ArtifactStore, ref: ArtifactRef, dest: Path):
    """An artifact at `dest` (a tree replaces the folder), skipping what is already there; hardlinks from LocalCAS
    when possible."""
    if ref.media_type == TREE_MEDIA_TYPE:
        files = read_tree(store, ref)
        if dest.is_dir() and not dest.is_symlink() and len([f for f in dest.rglob("*") if f.is_file()]) == len(files) \
                and all(_same_file(dest / rel, ArtifactRef(e["sha256"], e["size"])) for rel, e in files.items()):
            return
        if dest.is_dir() and not dest.is_symlink():
            import shutil
            shutil.rmtree(dest)
        elif dest.exists() or dest.is_symlink():
            dest.unlink()
        get_tree(store, ref, dest)
        return
    if _same_file(dest, ref):
        return
    if dest.is_dir() and not dest.is_symlink():
        import shutil
        shutil.rmtree(dest)
    store.get_file(ref.sha256, dest)


def checkout(run: Run, project_dir: Path, store: ArtifactStore, *,
             output_paths: Mapping[str, Mapping[str, str]] | None = None,
             report: Callable[[], Any] | None = None, runs: FileRunStore | None = None) -> bool:
    """Project the run into `<project>/build/` if it is the LATEST run: each stage's outputs at their canonical paths
    (`output_paths[stage][name]`, build-relative), its entries in status.json (a cached stage whose entry already has
    the same inputs keeps it), then `report()` (REPORT.md with history). Returns False, writing nothing, when a newer
    run has started."""
    build = Path(project_dir) / "build"
    runs = runs or FileRunStore(build / "runs")
    with runs.lock():
        if runs.latest() != run.run_id:
            return False
        for rec in run.stages:
            paths = (output_paths or {}).get(rec.stage) or {}
            for name, ref in rec.outputs.items():
                if name in paths:
                    place(store, ref, build / paths[name])
        sp = build / "status.json"
        try:
            cur = json.loads(sp.read_text())
        except (OSError, ValueError):
            cur = {}
        view = render_status(run, store)
        cached = {STATUS_KEY.get(r.stage, r.stage) for r in run.stages if r.cached}
        for k, v in view.items():
            old = cur.get(k)
            if k in cached and isinstance(old, dict) and old.get("inputs_hash") == v.get("inputs_hash"):
                continue
            cur[k] = v
        _write_atomic(sp, json.dumps(cur, indent=2, default=str))
    if report is not None:
        report()
    return True
