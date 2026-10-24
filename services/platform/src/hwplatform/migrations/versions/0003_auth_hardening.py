"""Auth hardening (#26): auth_events (rate limits), pending_signups (e-mail verification), invitations.

Revision ID: 0003_auth_hardening
Revises: 0002_byok
Create Date: 2026-10-24

auth_events and pending_signups are global, not tenant-scoped (the subject is an e-mail or an IP, and the
user and tenant do not exist yet), so they have no RLS. The app role gets only the privileges the auth
service needs (auth_events: no UPDATE). invitations is tenant-scoped with the usual policy.

The purge job (`hwplatform-purge`) runs as the schema owner, which FORCE RLS blinds on the tenant tables.
The owner role therefore gets SELECT/DELETE policies on refresh_tokens and invitations limited to rows
that are already expired, revoked or accepted: it can never delete a live token or invitation.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from hwplatform.settings import Settings

revision = "0003_auth_hardening"
down_revision = "0002_byok"
branch_labels = None
depends_on = None


def _app_role() -> str:
    role = op.get_context().config.get_main_option("app_db_role") or Settings().app_db_role
    Settings(app_db_role=role)
    return role


def upgrade() -> None:
    role = _app_role()
    owner = op.get_bind().execute(sa.text("SELECT current_user")).scalar_one()
    Settings(app_db_role=owner)  # goes into DDL below: must be a plain identifier

    op.create_table(
        "auth_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(8), nullable=False),
        sa.Column("subject", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("scope IN ('email', 'ip')", name="ck_auth_events_scope"),
        sa.CheckConstraint("kind IN ('attempt', 'fail')", name="ck_auth_events_kind"),
    )
    op.create_index("ix_auth_events_lookup", "auth_events", ["action", "scope", "subject", "kind", "at"])
    op.create_index("ix_auth_events_at", "auth_events", ["at"])

    op.create_table(
        "pending_signups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("tenant_name", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("email = lower(email)", name="ck_pending_signups_email_lower"),
    )

    role_enum = pg.ENUM("owner", "admin", "member", name="membership_role", create_type=False)
    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", role_enum, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("invited_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role <> 'owner'", name="ck_invitations_not_owner"),
    )
    op.create_index("ix_invitations_tenant_id", "invitations", ["tenant_id"])

    op.execute("ALTER TABLE invitations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invitations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON invitations USING (tenant_id = app_tenant_id()) "
        "WITH CHECK (tenant_id = app_tenant_id())"
    )

    # --- app role: only what the auth service needs ---------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, DELETE ON auth_events TO {role}")  # no UPDATE: events are append-only
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON pending_signups TO {role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON invitations TO {role}")

    # --- purge job (owner role): dead rows only -------------------------------------------------
    for table, dead in (
        ("refresh_tokens", "expires_at < now() OR revoked_at IS NOT NULL"),
        ("invitations", "expires_at < now() OR accepted_at IS NOT NULL"),
    ):
        op.execute(f"CREATE POLICY purge_read ON {table} FOR SELECT TO {owner} USING ({dead})")
        op.execute(f"CREATE POLICY purge_delete ON {table} FOR DELETE TO {owner} USING ({dead})")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS purge_read ON refresh_tokens")
    op.execute("DROP POLICY IF EXISTS purge_delete ON refresh_tokens")
    op.drop_table("invitations")
    op.drop_table("pending_signups")
    op.drop_table("auth_events")
