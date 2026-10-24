"""PgRunStore: runs, stages, artifact refs and project state in Postgres (ADR 0001, 3.4).

One store is bound to one tenant; every transaction opens with Database.tx(tenant_id=...), so RLS applies
to every statement. State changes write their events to the outbox in the SAME transaction
(events.outbox.emit): a rolled-back change leaves no event, a committed one always has it.

State machine: `transition` is a single UPDATE ... WHERE status = ANY(allowed_from); zero rows means
InvalidTransition (or RunNotFound). Concurrent transitions are serialized by the row lock.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import (
    TRANSITIONS,
    ArtifactRef,
    ArtifactStore,
    DesignVersionRef,
    InvalidTransition,
    Run,
    RunStatus,
    StageRecord,
    StageStatus,
)
from .db import Database
from .events import rfc3339
from .events.outbox import emit
from .ids import uuid7

MANIFEST_SCHEMA = "hwflow.run/1"
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
STAGE_TERMINAL = frozenset({"succeeded", "failed", "skipped", "cancelled"})
# run artifacts a person looks at (ADR 5.2 artifact.created): the last path segment decides
USER_VISIBLE = frozenset({"REPORT.md", "board", "fab", "manifest.json"})
STAGE_EVENT = {
    "running": "stage.started",
    "succeeded": "stage.completed",
    "failed": "stage.failed",
    "skipped": "stage.skipped",
}
RUN_EVENT = {
    ("queued", "running"): "run.started",
    ("running", "waiting"): "run.waiting",
    ("waiting", "running"): "run.resumed",
}


class RunNotFound(KeyError):
    pass


# --- (de)serialization helpers ------------------------------------------------------------------------


def _j(v: Any) -> Any:
    """jsonb comes back as str or already parsed, depending on the driver codec."""
    return json.loads(v) if isinstance(v, str) else v


def _dump(v: Any) -> str | None:
    return None if v is None else json.dumps(v, separators=(",", ":"), default=str)


def ref_to_dict(r: ArtifactRef) -> dict:
    return {"sha256": r.sha256, "size": r.size, "media_type": r.media_type, "name": r.name}


def ref_from_dict(d: dict) -> ArtifactRef:
    return ArtifactRef(
        d["sha256"], int(d["size"]), d.get("media_type", "application/octet-stream"), d.get("name", "")
    )


def _uuid(v: str | uuid.UUID | None) -> uuid.UUID | None:
    return None if v is None else uuid.UUID(str(v))


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
    """ADR 3.2: the Run serialized with dataclasses.asdict plus "schema": "hwflow.run/1"; stages in seq order."""
    d = dataclasses.asdict(run)
    d["stages"] = sorted(d["stages"], key=lambda s: (s["seq"], s["key"]))
    return {"schema": MANIFEST_SCHEMA, **_plain(d)}


# --- the store -----------------------------------------------------------------------------------------


class PgRunStore:
    def __init__(self, db: Database, tenant_id: uuid.UUID | str):
        self.db = db
        self.tenant_id = uuid.UUID(str(tenant_id))

    def _tx(self):
        return self.db.tx(tenant_id=self.tenant_id)

    # -- artifacts table ---------------------------------------------------------------------------
    async def _register(self, s: AsyncSession, refs: list[ArtifactRef]) -> None:
        """artifacts(tenant_id, sha256, size, media_type): what exists in S3 (the first put wins)."""
        for r in {r.sha256: r for r in refs}.values():
            await s.execute(
                text(
                    "INSERT INTO artifacts (tenant_id, sha256, size, media_type) VALUES (:t, :sha, :size, :mt) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"t": self.tenant_id, "sha": r.sha256, "size": r.size, "mt": r.media_type},
            )

    async def register_artifacts(self, refs: list[ArtifactRef]) -> None:
        async with self._tx() as s:
            await self._register(s, refs)

    # -- design versions ---------------------------------------------------------------------------
    async def create_design_version(
        self,
        project_id: str | uuid.UUID,
        *,
        content_sha256: str,
        design: ArtifactRef,
        project: ArtifactRef,
        requirements: ArtifactRef | None = None,
        source: str = "upload",
        parent_id: str | uuid.UUID | None = None,
        message: str = "",
        created_by: str | uuid.UUID | None = None,
        created_by_run: str | uuid.UUID | None = None,
    ) -> DesignVersionRef:
        """Idempotent on (project, content_sha256): the same content returns the existing version."""
        pid = uuid.UUID(str(project_id))
        async with self._tx() as s:
            locked = (
                await s.execute(text("SELECT id FROM projects WHERE id = :p FOR UPDATE"), {"p": pid})
            ).first()
            if locked is None:
                raise LookupError(f"project {project_id} not found")
            await self._register(s, [design, project] + ([requirements] if requirements else []))
            existing = (
                await s.execute(
                    text("SELECT id FROM design_versions WHERE project_id = :p AND content_sha256 = :c"),
                    {"p": pid, "c": content_sha256},
                )
            ).scalar_one_or_none()
            if existing is not None:
                return await self._design_version(s, existing)
            did = uuid7()
            seq = (
                await s.execute(
                    text("SELECT COALESCE(max(seq), 0) + 1 FROM design_versions WHERE project_id = :p"),
                    {"p": pid},
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO design_versions (id, tenant_id, project_id, seq, content_sha256, design_sha256, "
                    "requirements_sha256, project_tree_sha256, source, parent_id, message, created_by, created_by_run) "
                    "VALUES (:id, :t, :p, :seq, :c, :d, :rq, :pt, :src, :par, :msg, :cb, :cbr)"
                ),
                {
                    "id": did,
                    "t": self.tenant_id,
                    "p": pid,
                    "seq": seq,
                    "c": content_sha256,
                    "d": design.sha256,
                    "rq": requirements.sha256 if requirements else None,
                    "pt": project.sha256,
                    "src": source,
                    "par": _uuid(parent_id),
                    "msg": message,
                    "cb": _uuid(created_by),
                    "cbr": _uuid(created_by_run),
                },
            )
            await emit(
                s,
                self.tenant_id,
                None,
                "design_version.created",
                f"projects/{pid}/design_versions/{did}",
                {
                    "design_version_id": str(did),
                    "seq": seq,
                    "source": source,
                    "content_sha256": content_sha256,
                },
            )
            return await self._design_version(s, did)

    async def _refs(self, s: AsyncSession, shas: set[str]) -> dict[str, ArtifactRef]:
        if not shas:
            return {}
        rows = await s.execute(
            text("SELECT sha256, size, media_type FROM artifacts WHERE sha256 = ANY(CAST(:shas AS text[]))"),
            {"shas": sorted(shas)},
        )
        return {r.sha256: ArtifactRef(r.sha256, r.size, r.media_type) for r in rows}

    async def _design_version(self, s: AsyncSession, did: uuid.UUID) -> DesignVersionRef:
        r = (
            (await s.execute(text("SELECT * FROM design_versions WHERE id = :i"), {"i": did}))
            .mappings()
            .one()
        )
        shas = {r["design_sha256"], r["project_tree_sha256"]} | (
            {r["requirements_sha256"]} if r["requirements_sha256"] else set()
        )
        refs = await self._refs(s, shas)

        def ref(sha: str, name: str) -> ArtifactRef:
            base = refs.get(sha, ArtifactRef(sha, 0))
            return dataclasses.replace(base, name=name)

        return DesignVersionRef(
            id=str(r["id"]),
            content_sha256=r["content_sha256"],
            design=ref(r["design_sha256"], "design.yaml"),
            requirements=ref(r["requirements_sha256"], "requirements.yaml")
            if r["requirements_sha256"]
            else None,
            project=ref(r["project_tree_sha256"], "project"),
        )

    # -- reading runs ------------------------------------------------------------------------------
    async def _get(self, s: AsyncSession, rid: uuid.UUID) -> Run:
        r = (await s.execute(text("SELECT * FROM runs WHERE id = :i"), {"i": rid})).mappings().first()
        if r is None:
            raise RunNotFound(str(rid))
        dv = await self._design_version(s, r["design_version_id"])
        st_rows = (
            (
                await s.execute(
                    text("SELECT * FROM run_stages WHERE run_id = :i ORDER BY seq, key"), {"i": rid}
                )
            )
            .mappings()
            .all()
        )
        results = await self._refs(s, {x["result_sha256"] for x in st_rows if x["result_sha256"]})
        stages = [
            StageRecord(
                key=x["key"],
                stage=x["stage"],
                seq=x["seq"],
                status=StageStatus(x["status"]),
                verdict=x["verdict"],
                cached=x["cached"],
                cache_key=x["cache_key"],
                attempt=x["attempt"],
                started_at=x["started_at"],
                ended_at=x["ended_at"],
                seconds=x["seconds"],
                summary=_j(x["summary"]) or {},
                result=results.get(x["result_sha256"]) if x["result_sha256"] else None,
                outputs={k: ref_from_dict(v) for k, v in (_j(x["outputs"]) or {}).items()},
                error=_j(x["error"]),
            )
            for x in st_rows
        ]
        arts = (
            await s.execute(
                text(
                    "SELECT name, sha256, media_type, size FROM run_artifacts WHERE run_id = :i ORDER BY name"
                ),
                {"i": rid},
            )
        ).all()
        return Run(
            run_id=str(r["id"]),
            kind=r["kind"],
            tenant_id=str(r["tenant_id"]),
            project_id=str(r["project_id"]),
            design_version=dv,
            status=RunStatus(r["status"]),
            verdict=r["verdict"],
            options=_j(r["options"]),
            options_hash=r["options_hash"],
            settings=_j(r["settings"]) or {},
            code=_j(r["code"]) or {},
            tools=_j(r["tools"]) or {},
            stages=stages,
            artifacts={a.name: ArtifactRef(a.sha256, a.size, a.media_type, a.name) for a in arts},
            created_at=r["created_at"],
            started_at=r["started_at"],
            ended_at=r["ended_at"],
            error=_j(r["error"]),
            parent_run_id=str(r["parent_run_id"]) if r["parent_run_id"] else None,
            workflow_id=r["workflow_id"],
        )

    async def get(self, run_id: str) -> Run:
        async with self._tx() as s:
            return await self._get(s, uuid.UUID(str(run_id)))

    async def list(self, project_id: str, *, limit: int = 50, before: str | None = None) -> list[Run]:
        """Newest first (UUIDv7 order); `before` = run_id of the last row of the previous page."""
        async with self._tx() as s:
            ids = (
                (
                    await s.execute(
                        text(
                            "SELECT id FROM runs WHERE project_id = :p AND (CAST(:b AS uuid) IS NULL OR id < CAST(:b AS uuid)) "
                            "ORDER BY id DESC LIMIT :n"
                        ),
                        {"p": uuid.UUID(str(project_id)), "b": _uuid(before), "n": limit},
                    )
                )
                .scalars()
                .all()
            )
            return [await self._get(s, i) for i in ids]

    # -- create / transition -----------------------------------------------------------------------
    async def create(
        self,
        run: Run,
        *,
        idempotency_key: str | None = None,
        requested_by: str | uuid.UUID | None = None,
    ) -> Run:
        """Insert the run as `queued` (UUIDv7 id generated when run.run_id is empty) and emit run.queued.
        With an idempotency_key, a second create with the same key returns the first run."""
        if run.status != RunStatus.queued:
            raise ValueError("a run is created in state queued")
        rid = _uuid(run.run_id) if run.run_id else uuid7()
        async with self._tx() as s:
            inserted = (
                await s.execute(
                    text(
                        "INSERT INTO runs (id, tenant_id, project_id, design_version_id, kind, workflow_id, options, "
                        "options_hash, idempotency_key, settings, code, tools, parent_run_id, requested_by, created_at) "
                        "VALUES (:id, :t, :p, :dv, CAST(:kind AS run_kind), :wf, CAST(:opt AS jsonb), :oh, :ik, "
                        "CAST(:set AS jsonb), CAST(:code AS jsonb), CAST(:tools AS jsonb), :par, :rb, :ca) "
                        "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING id"
                    ),
                    {
                        "id": rid,
                        "t": self.tenant_id,
                        "p": uuid.UUID(str(run.project_id)),
                        "dv": uuid.UUID(run.design_version.id),
                        "kind": run.kind,
                        "wf": run.workflow_id or f"run-{rid}",
                        "opt": _dump(run.options),
                        "oh": run.options_hash,
                        "ik": idempotency_key,
                        "set": _dump(run.settings),
                        "code": _dump(run.code),
                        "tools": _dump(run.tools),
                        "par": _uuid(run.parent_run_id),
                        "rb": _uuid(requested_by),
                        "ca": run.created_at or datetime.now(UTC),
                    },
                )
            ).scalar_one_or_none()
            if inserted is None:  # idempotent replay
                existing = (
                    await s.execute(
                        text("SELECT id FROM runs WHERE tenant_id = :t AND idempotency_key = :k"),
                        {"t": self.tenant_id, "k": idempotency_key},
                    )
                ).scalar_one()
                return await self._get(s, existing)
            for rec in run.stages:
                await self._upsert_stage(s, rid, rec)
            await emit(
                s,
                self.tenant_id,
                rid,
                "run.queued",
                f"runs/{rid}",
                {
                    "kind": run.kind,
                    "project_id": str(run.project_id),
                    "design_version_id": run.design_version.id,
                    "options": run.options,
                    "requested_by": str(requested_by) if requested_by else None,
                },
            )
            return await self._get(s, rid)

    async def transition(
        self,
        run_id: str,
        to: RunStatus,
        *,
        error: dict | None = None,
        verdict: str | None = None,
        event_data: dict | None = None,
    ) -> Run:
        """queued/running/waiting -> ... per TRANSITIONS. Raises InvalidTransition (illegal move, incl. out of a
        terminal state) or RunNotFound. `event_data` adds fields to the event (reason, by, ...)."""
        to = RunStatus(to)
        if verdict is not None and verdict not in ("PASS", "FAIL"):
            raise ValueError(f"verdict must be PASS or FAIL, not {verdict!r}")
        rid = uuid.UUID(str(run_id))
        allowed_from = sorted(f for f, tos in TRANSITIONS.items() if to.value in tos)
        async with self._tx() as s:
            row = (
                await s.execute(
                    text(
                        "UPDATE runs SET status = CAST(:to AS run_status), "
                        "error = COALESCE(CAST(:err AS jsonb), error), verdict = COALESCE(CAST(:v AS text), verdict), "
                        "started_at = CASE WHEN CAST(:to AS run_status) = 'running' AND started_at IS NULL "
                        "THEN now() ELSE started_at END, "
                        "ended_at = CASE WHEN CAST(:to AS run_status) IN ('succeeded', 'failed', 'cancelled') "
                        "THEN now() ELSE ended_at END "
                        "WHERE id = :id AND status::text = ANY(CAST(:af AS text[])) "
                        "RETURNING (SELECT status::text FROM runs WHERE id = :id) AS prev"
                    ),
                    {"to": to.value, "err": _dump(error), "v": verdict, "id": rid, "af": allowed_from},
                )
            ).first()
            if row is None:
                cur = (
                    await s.execute(text("SELECT status::text FROM runs WHERE id = :i"), {"i": rid})
                ).scalar_one_or_none()
                if cur is None:
                    raise RunNotFound(str(rid))
                raise InvalidTransition(f"run {rid}: {cur} -> {to.value} is not allowed")
            prev = row.prev
            await self._emit_transition(s, rid, prev, to, error, event_data or {})
            return await self._get(s, rid)

    async def _emit_transition(
        self, s: AsyncSession, rid: uuid.UUID, prev: str, to: RunStatus, error: dict | None, extra: dict
    ) -> None:
        r = (await s.execute(text("SELECT * FROM runs WHERE id = :i"), {"i": rid})).mappings().one()
        if to == RunStatus.succeeded or to == RunStatus.failed or to == RunStatus.cancelled:
            names = (
                (
                    await s.execute(
                        text("SELECT name FROM run_artifacts WHERE run_id = :i ORDER BY name"), {"i": rid}
                    )
                )
                .scalars()
                .all()
            )
            report = (
                await s.execute(
                    text(
                        "SELECT sha256, size, media_type FROM run_artifacts WHERE run_id = :i AND name = 'REPORT.md'"
                    ),
                    {"i": rid},
                )
            ).first()
            report_ref = (
                ref_to_dict(ArtifactRef(report.sha256, report.size, report.media_type, "REPORT.md"))
                if report
                else None
            )
            if to == RunStatus.succeeded:
                etype, data = (
                    "run.completed",
                    {
                        "status": "succeeded",
                        "verdict": r["verdict"],
                        "report": report_ref,
                        "artifacts": list(names),
                    },
                )
            elif to == RunStatus.failed:
                err = _j(r["error"]) or {}
                etype, data = (
                    "run.failed",
                    {"status": "failed", "error": {"type": err.get("type"), "message": err.get("message")}},
                )
            else:
                etype, data = "run.cancelled", {"status": "cancelled", "by": extra.get("by")}
        else:
            etype = RUN_EVENT[(prev, to.value)]
            if etype == "run.started":
                data = {
                    "kind": r["kind"],
                    "project_id": str(r["project_id"]),
                    "design_version_id": str(r["design_version_id"]),
                    "workflow_id": r["workflow_id"],
                    "code": _j(r["code"]),
                }
            else:
                data = {"reason": extra.get("reason"), "question_id": extra.get("question_id")}
        data = {**data, **{k: v for k, v in extra.items() if k not in data}}
        await emit(s, self.tenant_id, rid, etype, f"runs/{rid}", data)

    # -- stages ------------------------------------------------------------------------------------
    async def upsert_stage(self, run_id: str, rec: StageRecord) -> None:
        async with self._tx() as s:
            await self._upsert_stage(s, uuid.UUID(str(run_id)), rec)

    async def _upsert_stage(self, s: AsyncSession, rid: uuid.UUID, rec: StageRecord) -> None:
        locked = (await s.execute(text("SELECT id FROM runs WHERE id = :i FOR UPDATE"), {"i": rid})).first()
        if locked is None:
            raise RunNotFound(str(rid))
        prior = (
            await s.execute(
                text("SELECT status, attempt FROM run_stages WHERE run_id = :i AND key = :k"),
                {"i": rid, "k": rec.key},
            )
        ).first()
        status = StageStatus(rec.status).value
        if (
            prior
            and prior.status in STAGE_TERMINAL
            and status not in STAGE_TERMINAL
            and rec.attempt <= prior.attempt
        ):
            return  # a late replay of an earlier state must not undo a finished stage
        refs = list(rec.outputs.values()) + ([rec.result] if rec.result else [])
        await self._register(s, refs)
        await s.execute(
            text(
                "INSERT INTO run_stages (run_id, tenant_id, key, stage, seq, status, verdict, cached, cache_key, attempt, "
                "summary, result_sha256, outputs, error, started_at, ended_at, seconds) "
                "VALUES (:r, :t, :k, :stage, :seq, :st, :v, :cached, :ck, :att, CAST(:sum AS jsonb), :res, "
                "CAST(:out AS jsonb), CAST(:err AS jsonb), :sa, :ea, :sec) "
                "ON CONFLICT (run_id, key) DO UPDATE SET stage = EXCLUDED.stage, seq = EXCLUDED.seq, "
                "status = EXCLUDED.status, verdict = EXCLUDED.verdict, cached = EXCLUDED.cached, "
                "cache_key = EXCLUDED.cache_key, attempt = EXCLUDED.attempt, summary = EXCLUDED.summary, "
                "result_sha256 = EXCLUDED.result_sha256, outputs = EXCLUDED.outputs, error = EXCLUDED.error, "
                "started_at = EXCLUDED.started_at, ended_at = EXCLUDED.ended_at, seconds = EXCLUDED.seconds"
            ),
            {
                "r": rid,
                "t": self.tenant_id,
                "k": rec.key,
                "stage": rec.stage,
                "seq": rec.seq,
                "st": status,
                "v": rec.verdict,
                "cached": rec.cached,
                "ck": rec.cache_key,
                "att": rec.attempt,
                "sum": _dump(rec.summary or {}),
                "res": rec.result.sha256 if rec.result else None,
                "out": _dump({k: ref_to_dict(v) for k, v in rec.outputs.items()}),
                "err": _dump(rec.error),
                "sa": rec.started_at,
                "ea": rec.ended_at,
                "sec": rec.seconds,
            },
        )
        etype = STAGE_EVENT.get(status)
        if etype is None or (prior and prior.status == status and prior.attempt == rec.attempt):
            return
        base = {"key": rec.key, "stage": rec.stage}
        data = {
            "stage.started": {**base, "attempt": rec.attempt},
            "stage.completed": {
                **base,
                "verdict": rec.verdict,
                "cached": rec.cached,
                "seconds": rec.seconds,
                "summary": rec.summary or {},
            },
            "stage.failed": {**base, "error": rec.error, "attempt": rec.attempt},
            "stage.skipped": {"key": rec.key, "reason": (rec.summary or {}).get("reason", "")},
        }[etype]
        await emit(s, self.tenant_id, rid, etype, f"runs/{rid}/stages/{rec.key}", data)

    # -- run artifacts -----------------------------------------------------------------------------
    async def set_artifacts(self, run_id: str, artifacts: Mapping[str, ArtifactRef]) -> None:
        rid = uuid.UUID(str(run_id))
        async with self._tx() as s:
            locked = (
                await s.execute(text("SELECT id FROM runs WHERE id = :i FOR UPDATE"), {"i": rid})
            ).first()
            if locked is None:
                raise RunNotFound(str(rid))
            await self._register(s, list(artifacts.values()))
            for name, ref in artifacts.items():
                prior = (
                    await s.execute(
                        text("SELECT sha256 FROM run_artifacts WHERE run_id = :i AND name = :n"),
                        {"i": rid, "n": name},
                    )
                ).scalar_one_or_none()
                await s.execute(
                    text(
                        "INSERT INTO run_artifacts (run_id, tenant_id, name, stage_key, sha256, media_type, size) "
                        "VALUES (:r, :t, :n, :sk, :sha, :mt, :sz) ON CONFLICT (run_id, name) DO UPDATE SET "
                        "stage_key = EXCLUDED.stage_key, sha256 = EXCLUDED.sha256, media_type = EXCLUDED.media_type, "
                        "size = EXCLUDED.size"
                    ),
                    {
                        "r": rid,
                        "t": self.tenant_id,
                        "n": name,
                        "sk": name.split("/")[0] if "/" in name else None,
                        "sha": ref.sha256,
                        "mt": ref.media_type,
                        "sz": ref.size,
                    },
                )
                if prior != ref.sha256 and name.rsplit("/", 1)[-1] in USER_VISIBLE:
                    await emit(
                        s,
                        self.tenant_id,
                        rid,
                        "artifact.created",
                        f"runs/{rid}/artifacts/{name}",
                        {"name": name, "ref": ref_to_dict(dataclasses.replace(ref, name=name))},
                    )
            await s.execute(
                text("UPDATE runs SET artifacts = artifacts || CAST(:patch AS jsonb) WHERE id = :i"),
                {
                    "i": rid,
                    "patch": _dump(
                        {n: ref_to_dict(dataclasses.replace(r, name=n)) for n, r in artifacts.items()}
                    ),
                },
            )

    # -- events, project state ---------------------------------------------------------------------
    async def append_event(self, run_id: str, type: str, data: dict) -> int:
        """Emit a catalogue event for the run in its own transaction; returns its seq."""
        rid = uuid.UUID(str(run_id))
        async with self._tx() as s:
            ev = await emit(s, self.tenant_id, rid, type, f"runs/{rid}", data)
            return ev.seq or 0

    async def project_state(self, project_id: str) -> dict[str, ArtifactRef]:
        async with self._tx() as s:
            rows = await s.execute(
                text(
                    "SELECT ps.name, ps.sha256, a.size, a.media_type FROM project_state ps "
                    "LEFT JOIN artifacts a ON a.tenant_id = ps.tenant_id AND a.sha256 = ps.sha256 "
                    "WHERE ps.project_id = :p"
                ),
                {"p": uuid.UUID(str(project_id))},
            )
            return {
                r.name: ArtifactRef(r.sha256, r.size or 0, r.media_type or "application/octet-stream", r.name)
                for r in rows
            }

    async def set_project_state(self, project_id: str, state: Mapping[str, ArtifactRef], run_id: str) -> None:
        """An older run never overwrites a newer one (UUIDv7 order), so late writers cannot go back in time."""
        async with self._tx() as s:
            await self._register(s, list(state.values()))
            for name, ref in state.items():
                await s.execute(
                    text(
                        "INSERT INTO project_state (tenant_id, project_id, name, sha256, run_id) "
                        "VALUES (:t, :p, :n, :sha, :r) ON CONFLICT (project_id, name) DO UPDATE SET "
                        "sha256 = EXCLUDED.sha256, run_id = EXCLUDED.run_id, updated_at = now() "
                        "WHERE project_state.run_id <= EXCLUDED.run_id"
                    ),
                    {
                        "t": self.tenant_id,
                        "p": uuid.UUID(str(project_id)),
                        "n": name,
                        "sha": ref.sha256,
                        "r": uuid.UUID(str(run_id)),
                    },
                )

    # -- manifest ----------------------------------------------------------------------------------
    async def manifest(self, run_id: str) -> dict:
        return run_to_manifest(await self.get(run_id))

    async def store_manifest(self, run_id: str, store: ArtifactStore) -> ArtifactRef:
        """Render the manifest from the rows and keep it as run artifact `manifest.json` (ADR 3.4)."""
        data = json.dumps(await self.manifest(run_id), sort_keys=True, indent=2).encode()
        ref = await asyncio.to_thread(
            store.put_bytes, data, media_type="application/json", name="manifest.json"
        )
        await self.set_artifacts(run_id, {"manifest.json": ref})
        return ref
