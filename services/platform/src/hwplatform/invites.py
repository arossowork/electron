"""Invitations to a tenant (#26): created by an owner/admin, accepted by the invited user once logged in.

Creating an invitation returns the same result whether or not the address has an account or is already
a member, and always costs the same work (one lookup, at most one insert, one e-mail), so the response
tells the inviter nothing about the address. The e-mail carries the token; the invited person signs up
(or logs in) with that address and presents the token. Accepting checks that the logged-in user's e-mail
equals the invited address, so a leaked token alone does not grant membership.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from . import security
from .auth.local import normalize_email
from .db import Database
from .mail import Email, EmailSender
from .models import Invitation, Membership, Role, User
from .settings import Settings
from .tenancy import MembershipRepo, TenantContext


async def create_invitation(
    db: Database,
    mailer: EmailSender,
    s: Settings,
    ctx: TenantContext,
    email: str,
    role: Role,
    tenant_name: str,
) -> None:
    email = normalize_email(email)
    token = security.new_invite_token(ctx.tenant_id)
    now = datetime.now(UTC)
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id, lookup_email=email) as sess:
        user = (await sess.execute(select(User).where(User.email == email))).scalar_one_or_none()
        already = user is not None and await MembershipRepo(sess, ctx).get_for_user(user.id) is not None
        if not already:
            sess.add(
                Invitation(
                    tenant_id=ctx.tenant_id,
                    email=email,
                    role=role,
                    token_hash=security.hash_token(token),
                    invited_by=ctx.user_id,
                    created_at=now,
                    expires_at=now + timedelta(seconds=s.invite_ttl_s),
                )
            )
    # Sent in both cases; a current member just gets a harmless message with a token that adds nothing.
    await mailer.send(
        Email(
            to=email,
            subject=f"You are invited to {tenant_name} on hwflow",
            body=f"Sign up or log in with this address, then accept the invitation with this token "
            f"(valid {s.invite_ttl_s // 86400} days):\n{token}\n"
            f"POST {s.public_url}/api/v1/auth/accept-invite " + '{"token": ...}',
            kind="invite",
            token=token,
        )
    )


class InviteError(Exception):
    pass


async def accept_invitation(db: Database, user_id: uuid.UUID, token: str) -> tuple[uuid.UUID, Role]:
    """Add the logged-in user to the invitation's tenant. Returns (tenant_id, role)."""
    try:
        tenant_id = security.parse_invite_token(token)
    except security.TokenError as e:
        raise InviteError("invalid or expired invitation") from e
    async with db.tx(user_id=user_id) as sess:
        me = (await sess.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if me is None:
        raise InviteError("invalid or expired invitation")
    now = datetime.now(UTC)
    async with db.tx(tenant_id=tenant_id, user_id=user_id) as sess:
        inv = (
            await sess.execute(
                select(Invitation)
                .where(Invitation.tenant_id == tenant_id, Invitation.token_hash == security.hash_token(token))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if inv is None or inv.accepted_at is not None or inv.expires_at <= now or inv.email != me.email:
            raise InviteError("invalid or expired invitation")
        inv.accepted_at = now
        existing = (
            await sess.execute(
                select(Membership).where(Membership.tenant_id == tenant_id, Membership.user_id == user_id)
            )
        ).scalar_one_or_none()
        if existing is None:
            sess.add(Membership(tenant_id=tenant_id, user_id=user_id, role=inv.role))
        return tenant_id, (existing.role if existing else inv.role)
