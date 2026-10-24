"""Built-in auth: argon2id passwords, 15-minute JWT access tokens, rotating opaque refresh tokens."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from .. import security
from ..db import Database
from ..mail import Email, EmailSender, LoggingEmailSender
from ..models import Membership, PendingSignup, RefreshToken, Role, Tenant, User
from ..settings import Settings
from ..tenancy import slugify
from .backend import AuthBackend, AuthError, Principal


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    tenant_id: uuid.UUID
    user_id: uuid.UUID


def normalize_email(email: str) -> str:
    return email.strip().lower()


class LocalAuthBackend(AuthBackend):
    name = "local"
    issues_tokens = True

    def __init__(self, db: Database, settings: Settings, mailer: EmailSender | None = None):
        self.db = db
        self.s = settings
        self.mailer = mailer or LoggingEmailSender()
        self._key = settings.require_jwt_secret()

    # --- AuthBackend -------------------------------------------------------------------------
    async def authenticate(self, bearer_token: str) -> Principal:
        try:
            c = security.decode_access_token(
                bearer_token, self._key, issuer=self.s.jwt_issuer, algorithm=self.s.jwt_algorithm
            )
        except security.TokenError as e:
            raise AuthError("invalid access token") from e
        return Principal(user_id=c.user_id, tenant_id=c.tenant_id)

    # --- token issuing -----------------------------------------------------------------------
    async def signup(self, email: str, password: str, tenant_name: str, display_name: str = "") -> None:
        """Start a sign-up. Returns nothing, and does the same work whether or not the e-mail is registered
        (argon2 hash, one lookup, one e-mail), so neither the response nor the timing tells them apart.

        New e-mail: a pending sign-up is stored and a verification token is mailed; the user, the tenant
        and the owner membership are created when the token is presented (`verify_signup`). Registered
        e-mail: a notice is mailed to the existing owner of the address and nothing is created.
        """
        email = normalize_email(email)
        pw_hash = security.hash_password(password)
        async with self.db.tx(lookup_email=email) as s:
            exists = (await s.execute(select(User.id).where(User.email == email))).first() is not None
        if exists:
            await self.mailer.send(
                Email(
                    to=email,
                    subject="Sign-up attempt for your hwflow account",
                    body="Someone tried to sign up with this address, which already has an account. "
                    "If it was you, log in instead. If not, ignore this message.",
                    kind="verify_notice",
                )
            )
            return
        if self.s.dev_auto_verify:
            try:
                await self._create_account(email, pw_hash, tenant_name, display_name)
            except IntegrityError:
                pass  # lost a race with a parallel sign-up of the same e-mail: nothing to do
            return
        token = security.new_verify_token()
        now = datetime.now(UTC)
        values = {
            "id": uuid.uuid4(),
            "email": email,
            "password_hash": pw_hash,
            "tenant_name": tenant_name,
            "display_name": display_name,
            "token_hash": security.hash_token(token),
            "created_at": now,
            "expires_at": now + timedelta(seconds=self.s.verify_token_ttl_s),
        }
        async with self.db.tx() as s:
            stmt = pg_insert(PendingSignup).values(**values)
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=[PendingSignup.email],
                    set_={k: stmt.excluded[k] for k in values if k not in ("id", "email")},
                )
            )
        hours = self.s.verify_token_ttl_s // 3600
        await self.mailer.send(
            Email(
                to=email,
                subject="Confirm your hwflow sign-up",
                body=f"Confirm your e-mail address with this token (valid {hours} h):\n"
                f'{token}\n{self.s.public_url}/api/v1/auth/verify (POST {{"token": ...}})',
                kind="verify",
                token=token,
            )
        )

    async def verify_signup(self, token: str) -> TokenPair:
        """Complete a sign-up with the e-mailed token: creates user, tenant, owner membership; logs in."""
        h = security.hash_token(token)
        async with self.db.tx() as s:
            # Consumed by the DELETE, so a token works once even under concurrent requests.
            row = (
                await s.execute(
                    delete(PendingSignup).where(PendingSignup.token_hash == h).returning(PendingSignup)
                )
            ).first()
        if row is None or row[0].expires_at <= datetime.now(UTC):
            raise AuthError("invalid or expired token")
        p = row[0]
        try:
            return await self._create_account(p.email, p.password_hash, p.tenant_name, p.display_name)
        except IntegrityError as e:  # the address got registered in the meantime
            raise AuthError("invalid or expired token") from e

    async def _create_account(
        self, email: str, password_hash: str, tenant_name: str, display_name: str
    ) -> TokenPair:
        user = User(id=uuid.uuid4(), email=email, display_name=display_name, password_hash=password_hash)
        tenant = Tenant(
            id=uuid.uuid4(),
            name=tenant_name,
            slug=f"{slugify(tenant_name, 50)}-{secrets.token_hex(3)}",
            max_concurrent_runs=self.s.default_max_concurrent_runs,
        )
        async with self.db.tx(tenant_id=tenant.id, user_id=user.id) as s:
            s.add(user)
            s.add(tenant)
            await s.flush()
            s.add(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.owner))
            await s.flush()
            return await self._new_session(s, user.id, tenant.id, Role.owner)

    async def login(self, email: str, password: str, tenant_id: uuid.UUID | None = None) -> TokenPair:
        email = normalize_email(email)
        async with self.db.tx(lookup_email=email) as s:
            user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        ok = security.verify_password(user.password_hash if user else None, password)
        if not ok or user is None:
            raise AuthError("invalid e-mail or password")
        async with self.db.tx(user_id=user.id) as s:
            q = select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at)
            memberships = list((await s.execute(q)).scalars())
        chosen = next((m for m in memberships if tenant_id is None or m.tenant_id == tenant_id), None)
        if chosen is None:
            raise AuthError("no membership in the requested tenant")
        async with self.db.tx(tenant_id=chosen.tenant_id, user_id=user.id) as s:
            if user.password_hash and security.needs_rehash(user.password_hash):
                await s.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(password_hash=security.hash_password(password))
                )
            return await self._new_session(s, user.id, chosen.tenant_id, chosen.role)

    async def refresh(self, refresh_token: str) -> TokenPair:
        try:
            tenant_id = security.parse_refresh_token(refresh_token)
        except security.TokenError as e:
            raise AuthError("invalid refresh token") from e
        now = datetime.now(UTC)
        h = security.hash_token(refresh_token)
        result: TokenPair | None = None
        async with self.db.tx(tenant_id=tenant_id) as s:
            q = (
                select(RefreshToken)
                .where(RefreshToken.tenant_id == tenant_id, RefreshToken.token_hash == h)
                .with_for_update()
            )
            rt = (await s.execute(q)).scalar_one_or_none()
            if rt is None:
                raise AuthError("invalid refresh token")
            if rt.revoked_at is not None:
                # Reuse of a rotated token: someone else may hold the family. Revoke all of it.
                await self._revoke_family(s, rt.family_id, now)
            elif rt.expires_at <= now:
                pass
            else:
                q = select(Membership).where(
                    Membership.tenant_id == tenant_id, Membership.user_id == rt.user_id
                )
                m = (await s.execute(q)).scalar_one_or_none()
                rt.revoked_at = now
                if m is not None:
                    result = await self._new_session(s, rt.user_id, tenant_id, m.role, family_id=rt.family_id)
        if result is None:  # raised after the commit, so a family revocation sticks
            raise AuthError("invalid refresh token")
        return result

    async def logout(self, refresh_token: str) -> None:
        """Revoke the whole login session (the token's family). Unknown tokens are ignored."""
        try:
            tenant_id = security.parse_refresh_token(refresh_token)
        except security.TokenError:
            return
        async with self.db.tx(tenant_id=tenant_id) as s:
            q = select(RefreshToken.family_id).where(
                RefreshToken.tenant_id == tenant_id,
                RefreshToken.token_hash == security.hash_token(refresh_token),
            )
            fam = (await s.execute(q)).scalar_one_or_none()
            if fam is not None:
                await self._revoke_family(s, fam, datetime.now(UTC))

    # --- helpers -----------------------------------------------------------------------------
    async def _revoke_family(self, s, family_id: uuid.UUID, now: datetime) -> None:
        await s.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    async def _new_session(
        self, s, user_id: uuid.UUID, tenant_id: uuid.UUID, role: Role, family_id: uuid.UUID | None = None
    ) -> TokenPair:
        raw = security.new_refresh_token(tenant_id)
        s.add(
            RefreshToken(
                tenant_id=tenant_id,
                user_id=user_id,
                token_hash=security.hash_token(raw),
                family_id=family_id or uuid.uuid4(),
                expires_at=datetime.now(UTC) + timedelta(seconds=self.s.refresh_token_ttl_s),
            )
        )
        await s.flush()
        access = security.issue_access_token(
            self._key,
            user_id=user_id,
            tenant_id=tenant_id,
            role=role.value,
            ttl_s=self.s.access_token_ttl_s,
            issuer=self.s.jwt_issuer,
            algorithm=self.s.jwt_algorithm,
        )
        return TokenPair(
            access_token=access,
            refresh_token=raw,
            expires_in=self.s.access_token_ttl_s,
            tenant_id=tenant_id,
            user_id=user_id,
        )
