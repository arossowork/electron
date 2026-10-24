"""Questions of a run (ADR 0001, sections 6 and 7.3): the person is asked, the answer wakes the workflow.

The worker (`record_question`, #15) calls `ask`; the API calls `answer`. Every state change writes its event to
the outbox in the same transaction (`question.asked`, `question.answered`, and for the timeout policy `question.auto_answered`,
`question.waiting_critical`).
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime

from hwflow.ids import uuid7
from sqlalchemy import text

from .db import Database
from .events.outbox import emit


class QuestionNotFound(KeyError):
    pass


class QuestionNotOpen(Exception):
    """Already answered, expired or cancelled (API: 409)."""


@dataclasses.dataclass(frozen=True)
class Question:
    id: str
    run_id: str
    stage: str
    status: str  # open | answered | expired | cancelled
    questions: list[dict]  # [{id, text, options, recommended, critical, default, why?, change?}]
    answers: dict | None
    answered_by: str | None
    asked_at: datetime
    answered_at: datetime | None
    auto_answered: bool = False  # answered by the workflow with the recommended options after the timeout
    default_answers: dict | None = None  # what the default was (kept after a person overrides it)
    overridden_at: datetime | None = None


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _row(r) -> Question:
    return Question(
        id=str(r["id"]),
        run_id=str(r["run_id"]),
        stage=r["stage"],
        status=r["status"],
        questions=_j(r["questions"]) or [],
        answers=_j(r["answers"]),
        answered_by=str(r["answered_by"]) if r["answered_by"] else None,
        asked_at=r["asked_at"],
        answered_at=r["answered_at"],
        auto_answered=bool(r["auto_answered"]),
        default_answers=_j(r["default_answers"]),
        overridden_at=r["overridden_at"],
    )


class QuestionStore:
    """Bound to one tenant, like PgRunStore; every transaction goes through Database.tx(tenant_id=)."""

    def __init__(self, db: Database, tenant_id: uuid.UUID | str):
        self.db = db
        self.tenant_id = uuid.UUID(str(tenant_id))

    async def ask(
        self,
        run_id: str | uuid.UUID,
        stage: str,
        questions: list[dict],
        *,
        question_id: str | uuid.UUID | None = None,
    ) -> Question:
        """Insert an `open` question row and emit `question.asked`. (The run's `waiting` transition is the
        workflow's, through PgRunStore.transition.) With `question_id` (the workflow's), a repeated call returns
        the existing row and emits nothing."""
        rid = uuid.UUID(str(run_id))
        qid = uuid.UUID(str(question_id)) if question_id else uuid7()
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            inserted = (
                await s.execute(
                    text(
                        "INSERT INTO questions (id, tenant_id, run_id, stage, questions) "
                        "VALUES (:id, :t, :r, :stage, CAST(:q AS jsonb)) ON CONFLICT (id) DO NOTHING RETURNING id"
                    ),
                    {"id": qid, "t": self.tenant_id, "r": rid, "stage": stage, "q": json.dumps(questions)},
                )
            ).first()
            if inserted is None:
                return await self._get(s, qid)
            await emit(
                s,
                self.tenant_id,
                rid,
                "question.asked",
                f"runs/{rid}/questions/{qid}",
                {"question_id": str(qid), "stage": stage, "questions": questions},
            )
            return await self._get(s, qid)

    async def _get(self, s, qid: uuid.UUID) -> Question:
        r = (await s.execute(text("SELECT * FROM questions WHERE id = :i"), {"i": qid})).mappings().first()
        if r is None:
            raise QuestionNotFound(str(qid))
        return _row(r)

    async def get(self, qid: str | uuid.UUID) -> Question:
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            return await self._get(s, uuid.UUID(str(qid)))

    async def list_for_run(self, run_id: str | uuid.UUID, *, status: str | None = None) -> list[Question]:
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            rows = (
                (
                    await s.execute(
                        text(
                            "SELECT * FROM questions WHERE run_id = :r AND (CAST(:st AS text) IS NULL OR status = :st) "
                            "ORDER BY asked_at, id"
                        ),
                        {"r": uuid.UUID(str(run_id)), "st": status},
                    )
                )
                .mappings()
                .all()
            )
            return [_row(r) for r in rows]

    async def answer(
        self, qid: str | uuid.UUID, answers: dict, answered_by: str | uuid.UUID | None
    ) -> Question:
        """open -> answered in one UPDATE (a second answer finds 0 rows: QuestionNotOpen) and emit
        `question.answered`. QuestionNotFound for an unknown id or another tenant's. `answered_by` None: the
        simulated product person of an evaluation (#19); the row and the event name no user."""
        q = uuid.UUID(str(qid))
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "UPDATE questions SET status = 'answered', answers = CAST(:a AS jsonb), "
                        "answered_by = :by, answered_at = now() WHERE id = :i AND status = 'open' RETURNING run_id"
                    ),
                    {
                        "a": json.dumps(answers),
                        "by": uuid.UUID(str(answered_by)) if answered_by else None,
                        "i": q,
                    },
                )
            ).first()
            if row is None:
                await self._get(s, q)  # raises QuestionNotFound
                raise QuestionNotOpen(str(q))
            await emit(
                s,
                self.tenant_id,
                row.run_id,
                "question.answered",
                f"runs/{row.run_id}/questions/{q}",
                {
                    "question_id": str(q),
                    "answered_by": str(answered_by) if answered_by else "simulator",
                    "answers": answers,
                },
            )
            return await self._get(s, q)

    async def reopen(self, qid: str | uuid.UUID) -> None:
        """Undo `answer` when the workflow could not be signalled, so the person can answer again."""
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            await s.execute(
                text(
                    "UPDATE questions SET status = 'open', answers = NULL, answered_by = NULL, answered_at = NULL "
                    "WHERE id = :i AND status = 'answered'"
                ),
                {"i": uuid.UUID(str(qid))},
            )

    async def set_status(self, qid: str | uuid.UUID, status: str) -> None:
        """open -> expired | cancelled (the workflow's `record_question`); emits `question.expired` for expiry."""
        if status not in ("expired", "cancelled"):
            raise ValueError("status must be expired or cancelled")
        q = uuid.UUID(str(qid))
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "UPDATE questions SET status = :st WHERE id = :i AND status = 'open' RETURNING run_id"
                    ),
                    {"st": status, "i": q},
                )
            ).first()
            if row is None:
                return
            if status == "expired":
                await emit(
                    s,
                    self.tenant_id,
                    row.run_id,
                    "question.expired",
                    f"runs/{row.run_id}/questions/{q}",
                    {"question_id": str(q)},
                )

    async def auto_answer(self, qid: str | uuid.UUID, answers: dict) -> Question | None:
        """open -> answered with the agent's recommended options, because nobody answered within the timeout
        (non-critical question). Emits `question.auto_answered`. None when the question is no longer open (a person
        answered at the same moment: theirs counts)."""
        q = uuid.UUID(str(qid))
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "UPDATE questions SET status = 'answered', answers = CAST(:a AS jsonb), "
                        "default_answers = CAST(:a AS jsonb), auto_answered = true, answered_at = now() "
                        "WHERE id = :i AND status = 'open' RETURNING run_id, stage"
                    ),
                    {"a": json.dumps(answers), "i": q},
                )
            ).first()
            if row is None:
                return None
            await emit(
                s,
                self.tenant_id,
                row.run_id,
                "question.auto_answered",
                f"runs/{row.run_id}/questions/{q}",
                {"question_id": str(q), "stage": row.stage, "answers": answers},
            )
            return await self._get(s, q)

    async def waiting_critical(self, qid: str | uuid.UUID) -> bool:
        """Emit `question.waiting_critical` for a critical question still open after the timeout, so the UI can
        highlight it. False when it is no longer open."""
        q = uuid.UUID(str(qid))
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            row = (
                await s.execute(
                    text("SELECT run_id, stage FROM questions WHERE id = :i AND status = 'open'"), {"i": q}
                )
            ).first()
            if row is None:
                return False
            await emit(
                s,
                self.tenant_id,
                row.run_id,
                "question.waiting_critical",
                f"runs/{row.run_id}/questions/{q}",
                {"question_id": str(q), "stage": row.stage},
            )
            return True

    async def override(
        self, qid: str | uuid.UUID, answers: dict, answered_by: str | uuid.UUID | None
    ) -> Question:
        """A person's late answer to a question that was answered by default: records it and emits
        `question.answered` with `overrides_default: true` (the workflow then reruns the affected stages).
        QuestionNotOpen when the question was not answered by default (or already overridden)."""
        q = uuid.UUID(str(qid))
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "UPDATE questions SET answers = CAST(:a AS jsonb), answered_by = :by, auto_answered = false, "
                        "overridden_at = now(), answered_at = now() "
                        "WHERE id = :i AND status = 'answered' AND auto_answered RETURNING run_id, default_answers"
                    ),
                    {
                        "a": json.dumps(answers),
                        "by": uuid.UUID(str(answered_by)) if answered_by else None,
                        "i": q,
                    },
                )
            ).first()
            if row is None:
                await self._get(s, q)  # raises QuestionNotFound
                raise QuestionNotOpen(str(q))
            await emit(
                s,
                self.tenant_id,
                row.run_id,
                "question.answered",
                f"runs/{row.run_id}/questions/{q}",
                {
                    "question_id": str(q),
                    "answered_by": str(answered_by) if answered_by else "simulator",
                    "answers": answers,
                    "overrides_default": True,
                    "default_answers": _j(row.default_answers),
                },
            )
            return await self._get(s, q)

    async def revert_override(self, qid: str | uuid.UUID) -> None:
        """Undo `override` when the workflow could not be signalled."""
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            await s.execute(
                text(
                    "UPDATE questions SET answers = default_answers, answered_by = NULL, auto_answered = true, "
                    "overridden_at = NULL WHERE id = :i AND status = 'answered' AND overridden_at IS NOT NULL"
                ),
                {"i": uuid.UUID(str(qid))},
            )
