"""BYOK: tenant_llm_keys (envelope-encrypted API keys) and llm_usage, both tenant-scoped with RLS.

Revision ID: 0002_byok
Revises: 0001_tenancy
Create Date: 2026-10-24

The master-key rotation command (`hwplatform-rotate-master`) must re-wrap the DEKs of all tenants. It runs
as the schema owner, which FORCE RLS would blind, so the owner role gets an explicit SELECT/UPDATE policy on
tenant_llm_keys only. The API role (hwflow_app) has no such policy and sees its own tenant's row only.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_byok"
down_revision = "0001_tenancy"
branch_labels = None
depends_on = None


def _app_role() -> str:
    from hwplatform.settings import Settings

    role = op.get_context().config.get_main_option("app_db_role") or Settings().app_db_role
    Settings(app_db_role=role)
    return role


def upgrade() -> None:
    role = _app_role()
    owner = op.get_bind().execute(sa.text("SELECT current_user")).scalar_one()
    Settings = __import__("hwplatform.settings", fromlist=["Settings"]).Settings
    Settings(app_db_role=owner)  # the owner name goes into DDL below: must be a plain identifier

    op.create_table(
        "tenant_llm_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("last4", sa.String(4), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "provider"),
        sa.CheckConstraint("provider IN ('anthropic')", name="ck_tenant_llm_keys_provider"),
        sa.CheckConstraint("status IN ('active', 'invalid')", name="ck_tenant_llm_keys_status"),
    )
    op.create_index("ix_tenant_llm_keys_tenant_id", "tenant_llm_keys", ["tenant_id"])
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_llm_usage_tenant_id", "llm_usage", ["tenant_id"])
    op.create_index("ix_llm_usage_tenant_at", "llm_usage", ["tenant_id", "at"])

    for t in ("tenant_llm_keys", "llm_usage"):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} USING (tenant_id = app_tenant_id()) "
            "WITH CHECK (tenant_id = app_tenant_id())"
        )
        op.execute(f"GRANT SELECT, INSERT ON {t} TO {role}")
    op.execute(f"GRANT UPDATE, DELETE ON tenant_llm_keys TO {role}")

    # Master-key rotation (owner role only, not the API role).
    op.execute(f"CREATE POLICY master_rotation_read ON tenant_llm_keys FOR SELECT TO {owner} USING (true)")
    op.execute(
        f"CREATE POLICY master_rotation_write ON tenant_llm_keys FOR UPDATE TO {owner} "
        "USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.drop_table("llm_usage")
    op.drop_table("tenant_llm_keys")
