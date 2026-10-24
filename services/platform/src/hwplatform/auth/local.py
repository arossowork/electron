"""Built-in auth: argon2id passwords, 15-minute JWT access tokens, rotating opaque refresh tokens."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .. import security
from ..db import Database
from ..models import Membership, RefreshToken, Role, Tenant, User
from ..settings import Settings
from ..tenancy import slugify
from .backend import AuthBackend, AuthError, Principal


class SignupConflict(Exception):
    pass


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

    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.s = settings
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
    async def signup(self, email: str, password: str, tenant_name: str, display_name: str = "") -> TokenPair:
        email = normalize_email(email)
        user = User(
            id=uuid.uuid4(),
            email=email,
            display_name=display_name,
            password_hash=security.hash_password(password),
        )
        tenant = Tenant(
            id=uuid.uuid4(),
            name=tenant_name,
            slug=f"{slugify(tenant_name, 50)}-{secrets.token_hex(3)}",
            max_concurrent_runs=self.s.default_max_concurrent_runs,
        )
        try:
            async with self.db.tx(tenant_id=tenant.id, user_id=user.id) as s:
                s.add(user)
                s.add(tenant)
                await s.flush()
                s.add(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.owner))
                await s.flush()
                pair = await self._new_session(s, user.id, tenant.id, Role.owner)
        except IntegrityError as e:
            raise SignupConflict("e-mail already registered") from e
        return pair

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
