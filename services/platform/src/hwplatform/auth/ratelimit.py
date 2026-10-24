"""Rate limiting and temporary lockout for login, signup and refresh (#26), stored in Postgres.

Every request on those endpoints appends an 'attempt' event for its e-mail and its client IP in
`auth_events`; a failed login appends a 'fail' event. Nothing here is tenant-scoped: the table is global
and has no RLS, and this module is the only writer.

* Window limit (sliding): more than `max` attempts for the same e-mail or IP within the window are
  rejected with 429 and a Retry-After. The rejected attempt is not recorded, so it does not extend the wait.
* Lockout: `lockout_threshold` failed logins for one e-mail (or `lockout_ip_threshold` for one IP) within
  `lockout_window_s` lock it, even for the right password. The lock ends when the failures age out of
  the window, i.e. `lockout_window_s` after the N-th most recent failure. Locked requests are not
  recorded as failures, so they do not extend the lock. A successful login clears the e-mail's failures.

Both apply the same way whether or not the e-mail exists, so they reveal nothing about accounts.
Known trade-off: an attacker can lock a victim's e-mail out of login for the window by failing on
purpose; the per-IP limits bound how many e-mails one source can lock.
Counting is check-after-insert inside one transaction; concurrent requests can overshoot a limit by the
number of requests in flight, which is acceptable for brute-force protection.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from ..db import Database
from ..models import AuthEvent
from ..settings import Settings


class RateLimited(Exception):
    def __init__(self, retry_after_s: int, reason: str = "rate"):
        super().__init__("too many attempts")
        self.retry_after_s = max(1, retry_after_s)
        self.reason = reason


@dataclass(frozen=True)
class Policy:
    window_s: int
    email_max: int | None
    ip_max: int | None


def subject_hash(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


class AuthGuard:
    def __init__(self, db: Database, s: Settings):
        self.db, self.s = db, s
        self.policies = {
            "login": Policy(s.rate_window_s, s.rate_login_email_max, s.rate_login_ip_max),
            "refresh": Policy(s.rate_window_s, None, s.rate_refresh_ip_max),
            "switch": Policy(s.rate_window_s, None, s.rate_refresh_ip_max),
            "verify": Policy(s.rate_window_s, None, s.rate_refresh_ip_max),
            "signup": Policy(s.rate_signup_window_s, s.rate_signup_email_max, s.rate_signup_ip_max),
        }

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _subjects(self, email: str | None, ip: str) -> list[tuple[str, str]]:
        out = [("ip", subject_hash(ip or "unknown"))]
        if email is not None:
            out.append(("email", subject_hash(email)))
        return out

    async def hit(self, action: str, *, ip: str, email: str | None = None) -> None:
        """Record one attempt and enforce the window limit. Raises RateLimited (nothing is recorded then)."""
        pol = self.policies[action]
        now = self._now()
        since = now - timedelta(seconds=pol.window_s)
        async with self.db.tx() as s:
            for scope, subject in self._subjects(email, ip):
                s.add(AuthEvent(action=action, scope=scope, subject=subject, kind="attempt", at=now))
            await s.flush()
            for scope, subject in self._subjects(email, ip):
                limit = pol.email_max if scope == "email" else pol.ip_max
                if limit is None:
                    continue
                n, oldest = (
                    await s.execute(
                        select(func.count(), func.min(AuthEvent.at)).where(
                            AuthEvent.action == action,
                            AuthEvent.scope == scope,
                            AuthEvent.subject == subject,
                            AuthEvent.kind == "attempt",
                            AuthEvent.at > since,
                        )
                    )
                ).one()
                if n > limit:
                    wait = math.ceil((oldest + timedelta(seconds=pol.window_s) - now).total_seconds())
                    raise RateLimited(wait)  # leaving the block rolls the inserts back

    async def check_lockout(self, *, ip: str, email: str) -> None:
        now = self._now()
        since = now - timedelta(seconds=self.s.lockout_window_s)
        async with self.db.tx() as s:
            for scope, subject in self._subjects(email, ip):
                threshold = self.s.lockout_threshold if scope == "email" else self.s.lockout_ip_threshold
                q = (
                    select(AuthEvent.at)
                    .where(
                        AuthEvent.action == "login",
                        AuthEvent.scope == scope,
                        AuthEvent.subject == subject,
                        AuthEvent.kind == "fail",
                        AuthEvent.at > since,
                    )
                    .order_by(AuthEvent.at.desc())
                    .limit(threshold)
                )
                times = list((await s.execute(q)).scalars())
                if len(times) >= threshold:
                    ends = times[-1] + timedelta(seconds=self.s.lockout_window_s)
                    raise RateLimited(math.ceil((ends - now).total_seconds()), reason="lockout")

    async def record_failure(self, *, ip: str, email: str) -> None:
        now = self._now()
        async with self.db.tx() as s:
            for scope, subject in self._subjects(email, ip):
                s.add(AuthEvent(action="login", scope=scope, subject=subject, kind="fail", at=now))

    async def clear_failures(self, *, email: str) -> None:
        async with self.db.tx() as s:
            await s.execute(
                delete(AuthEvent).where(
                    AuthEvent.action == "login",
                    AuthEvent.scope == "email",
                    AuthEvent.subject == subject_hash(email),
                    AuthEvent.kind == "fail",
                )
            )
