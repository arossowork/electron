"""Tenancy: tenants, users, memberships, projects, refresh_tokens; row-level security; app role grants.

Revision ID: 0001_tenancy
Revises:
Create Date: 2026-10-24

Security model
--------------
The migrations run as the schema owner (role hwflow). The API connects as the app role (hwflow_app),
which owns nothing, is not superuser and has no BYPASSRLS, so every policy below applies to it.
FORCE ROW LEVEL SECURITY makes the policies apply to the owner as well; only a superuser bypasses them.

The policies read three transaction-local settings, set by hwplatform.db.Database.tx():
app.tenant_id, app.user_id, app.lookup_email. Unset or empty means "no access".
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_tenancy"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ("projects", "refresh_tokens")


def _app_role() -> str:
    from hwplatform.settings import Settings

    role = op.get_context().config.get_main_option("app_db_role") or Settings().app_db_role
    Settings(app_db_role=role)  # validates it is a plain identifier before it goes into SQL
    return role


def _ts():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    role = _app_role()

    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=False, server_default="2"),
        _ts(),
        sa.CheckConstraint("max_concurrent_runs >= 0", name="ck_tenants_max_concurrent_runs"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("password_hash", sa.String(255), nullable=True),
        _ts(),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_lower"),
    )
    role_enum = pg.ENUM("owner", "admin", "member", name="membership_role", create_type=False)
    role_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", role_enum, nullable=False),
        _ts(),
        sa.UniqueConstraint("tenant_id", "user_id"),
    )
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        _ts(),
        sa.UniqueConstraint("tenant_id", "slug"),
    )
    op.create_index("ix_projects_tenant_id", "projects", ["tenant_id"])
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        _ts(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_refresh_tokens_tenant_id", "refresh_tokens", ["tenant_id"])
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_family", "refresh_tokens", ["family_id"])

    # --- context accessors used by the policies --------------------------------------------
    for name, typ in (("tenant_id", "uuid"), ("user_id", "uuid"), ("lookup_email", "text")):
        op.execute(
            f"CREATE FUNCTION app_{name}() RETURNS {typ} LANGUAGE sql STABLE PARALLEL SAFE AS "
            f"$$ SELECT NULLIF(current_setting('app.{name}', true), '')::{typ} $$"
        )

    # --- row-level security ------------------------------------------------------------------
    for t in ("tenants", "users", "memberships", *TENANT_TABLES):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")

    # Tenant-scoped tables: only the current tenant's rows, for every command.
    for t in TENANT_TABLES:
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} USING (tenant_id = app_tenant_id()) "
            "WITH CHECK (tenant_id = app_tenant_id())"
        )

    # Memberships: the current tenant's, plus the caller's own in other tenants (tenant switcher, login).
    op.execute(
        "CREATE POLICY membership_read ON memberships FOR SELECT "
        "USING (tenant_id = app_tenant_id() OR user_id = app_user_id())"
    )
    for cmd in ("INSERT", "UPDATE", "DELETE"):
        using = "" if cmd == "INSERT" else "USING (tenant_id = app_tenant_id()) "
        check = "" if cmd == "DELETE" else "WITH CHECK (tenant_id = app_tenant_id())"
        op.execute(f"CREATE POLICY membership_{cmd.lower()} ON memberships FOR {cmd} {using}{check}")

    # Tenants: the current one, plus those the caller is a member of.
    op.execute(
        "CREATE POLICY tenant_read ON tenants FOR SELECT USING (id = app_tenant_id() "
        "OR id IN (SELECT m.tenant_id FROM memberships m WHERE m.user_id = app_user_id()))"
    )
    op.execute("CREATE POLICY tenant_insert ON tenants FOR INSERT WITH CHECK (id = app_tenant_id())")
    op.execute(
        "CREATE POLICY tenant_update ON tenants FOR UPDATE USING (id = app_tenant_id()) "
        "WITH CHECK (id = app_tenant_id())"
    )

    # Users (global identities): oneself, one looked-up e-mail, and the members of the current tenant.
    op.execute(
        "CREATE POLICY user_read ON users FOR SELECT USING (id = app_user_id() "
        "OR email = app_lookup_email() "
        "OR id IN (SELECT m.user_id FROM memberships m WHERE m.tenant_id = app_tenant_id()))"
    )
    op.execute("CREATE POLICY user_insert ON users FOR INSERT WITH CHECK (id = app_user_id())")
    op.execute(
        "CREATE POLICY user_update ON users FOR UPDATE USING (id = app_user_id()) "
        "WITH CHECK (id = app_user_id())"
    )

    # --- app role ------------------------------------------------------------------------------
    # Normally created with a password by deploy/compose/postgres-init.sh (or the operator); created
    # here NOLOGIN only if missing, which needs CREATEROLE.
    op.execute(
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
        f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$"
    )
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}' "
        f"AND (rolsuper OR rolbypassrls)) THEN RAISE EXCEPTION 'app role {role} must not be superuser "
        f"or BYPASSRLS: row-level security would not apply'; END IF; END $$"
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON projects, refresh_tokens, memberships TO {role}")
    # Tenants: the app may create one and rename it; the quota is set by the operator only.
    op.execute(f"GRANT SELECT, INSERT ON tenants TO {role}")
    op.execute(f"GRANT UPDATE (name) ON tenants TO {role}")
    op.execute(f"GRANT SELECT, INSERT ON users TO {role}")
    op.execute(f"GRANT UPDATE (display_name, password_hash) ON users TO {role}")
    for f in ("tenant_id", "user_id", "lookup_email"):
        op.execute(f"GRANT EXECUTE ON FUNCTION app_{f}() TO {role}")


def downgrade() -> None:
    # These two policies query memberships, so they go before the table.
    op.execute("DROP POLICY IF EXISTS tenant_read ON tenants")
    op.execute("DROP POLICY IF EXISTS user_read ON users")
    for t in ("refresh_tokens", "projects", "memberships", "users", "tenants"):
        op.drop_table(t)
    op.execute("DROP TYPE IF EXISTS membership_role")
    for f in ("tenant_id", "user_id", "lookup_email"):
        op.execute(f"DROP FUNCTION IF EXISTS app_{f}()")
    # The app role is shared infrastructure (created outside the migrations); it is not dropped.
