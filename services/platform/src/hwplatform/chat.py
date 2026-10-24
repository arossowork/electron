"""Free chat of a design run (#54, ADR 0001 7.7): the person writes, the agent replies.

The API stores a user message (`post_user`, event `user.message`) and signals the run's workflow (`chat`).
The agent activity takes the undelivered messages at its next turn boundary (`take_pending`: marks them
delivered, in order) and stores its reply (`post_agent`, event `agent.message`). Messages are per run and per
tenant (RLS, migration 0009); every state change writes its event to the outbox in the same transaction.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime

from hwflow.ids import uuid7
from sqlalchemy import text

from .db import Database
from .events.outbox import emit

BODY_MAX = 4000  # characters of one user message
REPLY_MAX = 20000


@dataclasses.dataclass(frozen=True)
class ChatMessage:
    id: str
    run_id: str
    role: str  # user | agent
    body: str
    user_id: str | None
    stage: str | None
    created_at: datetime
    delivered_at: datetime | None


def _row(r) -> ChatMessage:
    return ChatMessage(
        id=str(r["id"]),
        run_id=str(r["run_id"]),
        role=r["role"],
        body=r["body"],
        user_id=str(r["user_id"]) if r["user_id"] else None,
        stage=r["stage"],
        created_at=r["created_at"],
        delivered_at=r["delivered_at"],
    )


class ChatStore:
    """Bound to one tenant, like QuestionStore."""

    def __init__(self, db: Database, tenant_id: uuid.UUID | str):
        self.db = db
        self.tenant_id = uuid.UUID(str(tenant_id))

    async def post_user(
        self, run_id: str | uuid.UUID, body: str, user_id: str | uuid.UUID | None
    ) -> ChatMessage:
        rid, mid = uuid.UUID(str(run_id)), uuid7()
        body = body.strip()[:BODY_MAX]
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            r = (
                (
                    await s.execute(
                        text(
                            "INSERT INTO chat_messages (id, tenant_id, run_id, role, body, user_id) "
                            "VALUES (:i, :t, :r, 'user', :b, :u) RETURNING *"
                        ),
                        {
                            "i": mid,
                            "t": self.tenant_id,
                            "r": rid,
                            "b": body,
                            "u": uuid.UUID(str(user_id)) if user_id else None,
                        },
                    )
                )
                .mappings()
                .one()
            )
            await emit(
                s,
                self.tenant_id,
                rid,
                "user.message",
                f"runs/{rid}/chat/{mid}",
                {"message_id": str(mid), "text": body, "user_id": str(user_id) if user_id else None},
            )
            return _row(r)

    async def discard(self, message_id: str | uuid.UUID) -> None:
        """Remove an undelivered user message whose workflow could not be signalled."""
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            await s.execute(
                text("DELETE FROM chat_messages WHERE id = :i AND role = 'user' AND delivered_at IS NULL"),
                {"i": uuid.UUID(str(message_id))},
            )

    async def take_pending(self, run_id: str | uuid.UUID) -> list[ChatMessage]:
        """The run's undelivered user messages, oldest first, marked delivered in the same statement (so two
        activity attempts never deliver one message twice)."""
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            rows = (
                (
                    await s.execute(
                        text(
                            "WITH c AS (SELECT id FROM chat_messages WHERE run_id = :r AND role = 'user' "
                            "AND delivered_at IS NULL ORDER BY created_at, id FOR UPDATE SKIP LOCKED) "
                            "UPDATE chat_messages m SET delivered_at = now() FROM c WHERE m.id = c.id "
                            "RETURNING m.*"
                        ),
                        {"r": uuid.UUID(str(run_id))},
                    )
                )
                .mappings()
                .all()
            )
        return sorted((_row(r) for r in rows), key=lambda m: (m.created_at, m.id))

    async def release(self, ids: list[str]) -> None:
        """Mark messages undelivered again (the session failed before the agent saw them)."""
        if not ids:
            return
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            await s.execute(
                text("UPDATE chat_messages SET delivered_at = NULL WHERE id = ANY(:ids) AND role = 'user'"),
                {"ids": [uuid.UUID(i) for i in ids]},
            )

    async def post_agent(
        self, run_id: str | uuid.UUID, body: str, stage: str, reply_to: list[str]
    ) -> ChatMessage:
        rid, mid = uuid.UUID(str(run_id)), uuid7()
        body = body.strip()[:REPLY_MAX] or "(no reply text)"
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            r = (
                (
                    await s.execute(
                        text(
                            "INSERT INTO chat_messages (id, tenant_id, run_id, role, body, stage) "
                            "VALUES (:i, :t, :r, 'agent', :b, :st) RETURNING *"
                        ),
                        {"i": mid, "t": self.tenant_id, "r": rid, "b": body, "st": stage},
                    )
                )
                .mappings()
                .one()
            )
            await emit(
                s,
                self.tenant_id,
                rid,
                "agent.message",
                f"runs/{rid}/chat/{mid}",
                {"message_id": str(mid), "text": body, "stage": stage, "reply_to": reply_to},
            )
            return _row(r)

    async def list_for_run(self, run_id: str | uuid.UUID, *, limit: int = 500) -> list[ChatMessage]:
        async with self.db.tx(tenant_id=self.tenant_id) as s:
            rows = (
                (
                    await s.execute(
                        text(
                            "SELECT * FROM chat_messages WHERE run_id = :r ORDER BY created_at, id LIMIT :n"
                        ),
                        {"r": uuid.UUID(str(run_id)), "n": limit},
                    )
                )
                .mappings()
                .all()
            )
            return [_row(r) for r in rows]
