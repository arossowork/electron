"""Agent credentials (#15): tenant_llm_keys.credential_type (api_key | subscription_token).

Revision ID: 0007_credential_type
Revises: 0006_questions
Create Date: 2026-10-24

The same envelope-encrypted row holds either an Anthropic API key or a Claude subscription OAuth token
(`claude setup-token`); still one active credential per tenant and provider (the unique constraint of 0002).
Which tenants may use a subscription token is an operator setting (HWPLATFORM_SUBSCRIPTION_TENANT_IDS),
checked by hwplatform.credentials on PUT and on every resolve, not by the schema.
"""

from alembic import op

revision = "0007_credential_type"
down_revision = "0006_questions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tenant_llm_keys ADD COLUMN credential_type varchar(32) NOT NULL DEFAULT 'api_key' "
        "CONSTRAINT ck_tenant_llm_keys_credential_type CHECK (credential_type IN ('api_key', 'subscription_token'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tenant_llm_keys DROP COLUMN credential_type")
