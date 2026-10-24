"""Periodic cleanup (#26): `hwplatform-purge`, later a k8s CronJob.

Runs as the schema owner (HWPLATFORM_MIGRATION_DATABASE_URL). On the tenant tables the owner has
delete policies for dead rows only (migration 0003), so a bug here cannot remove a live token.

* refresh_tokens: expired ones; revoked ones once `purge_revoked_after_s` has passed. A revoked
  (rotated) token is kept for a while because presenting it again is how token theft is detected.
* invitations: expired or accepted.
* pending_signups: expired.
* auth_events: older than `purge_events_after_s` (must exceed the longest rate-limit window).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, or_, text

from .db import Database, make_engine
from .models import AuthEvent, Invitation, PendingSignup, RefreshToken
from .settings import Settings


async def purge_all(
    settings: Settings, url: str | None = None, now: datetime | None = None
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    db = Database(make_engine(url or settings.migration_database_url, 1))
    try:
        async with db.tx() as s:
            out = {}
            out["refresh_tokens"] = (
                await s.execute(
                    delete(RefreshToken).where(
                        or_(
                            RefreshToken.expires_at < now,
                            and_(
                                RefreshToken.revoked_at.is_not(None),
                                RefreshToken.revoked_at
                                < now - timedelta(seconds=settings.purge_revoked_after_s),
                            ),
                        )
                    )
                )
            ).rowcount
            out["invitations"] = (
                await s.execute(
                    delete(Invitation).where(
                        or_(Invitation.expires_at < now, Invitation.accepted_at.is_not(None))
                    )
                )
            ).rowcount
            out["pending_signups"] = (
                await s.execute(delete(PendingSignup).where(PendingSignup.expires_at < now))
            ).rowcount
            out["auth_events"] = (
                await s.execute(
                    delete(AuthEvent).where(
                        AuthEvent.at < now - timedelta(seconds=settings.purge_events_after_s)
                    )
                )
            ).rowcount
            # Published outbox rows only (policy purge_delete, 0005); unpublished ones are never touched.
            out["outbox"] = (
                await s.execute(
                    text("DELETE FROM outbox WHERE published_at < :cutoff"),
                    {"cutoff": now - timedelta(seconds=settings.outbox_purge_after_s)},
                )
            ).rowcount
        return out
    finally:
        await db.dispose()
