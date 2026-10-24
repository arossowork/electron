"""Agent chat and defaulted answers (#54).

Revision ID: 0009_chat_and_defaults
Revises: 0008_eval_baselines
Create Date: 2026-10-24

`chat_messages`: the free chat of a design run. A user message is stored by the API (`POST /runs/{id}/chat`) with
`delivered_at` NULL; the agent activity takes the undelivered ones at its next turn boundary and sets
`delivered_at`. The agent's replies are rows with role `agent`. Same tenant RLS pattern as 0004/0006.

`questions` gains the defaulting columns: a non-critical question nobody answered within the timeout is answered
by the workflow with the agent's recommended option (`auto_answered`, `default_answers`); a person's later
answer overrides it (`overridden_at`, `auto_answered` back to false).
"""

from alembic import op

from hwplatform.settings import Settings

revision = "0009_chat_and_defaults"
down_revision = "0008_eval_baselines"
branch_labels = None
depends_on = None


def _app_role() -> str:
    role = op.get_context().config.get_main_option("app_db_role") or Settings().app_db_role
    Settings(app_db_role=role)  # must be a plain identifier: it goes into GRANTs
    return role


def upgrade() -> None:
    role = _app_role()
    op.execute(
        """CREATE TABLE chat_messages (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants ON DELETE CASCADE,
  run_id uuid NOT NULL REFERENCES runs ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('user', 'agent')),
  body text NOT NULL CHECK (length(body) BETWEEN 1 AND 20000),
  user_id uuid REFERENCES users ON DELETE SET NULL,
  stage text,
  created_at timestamptz NOT NULL DEFAULT now(),
  delivered_at timestamptz)"""
    )
    op.execute("CREATE INDEX ix_chat_messages_tenant_id ON chat_messages (tenant_id)")
    op.execute("CREATE INDEX ix_chat_messages_run ON chat_messages (run_id, created_at, id)")
    op.execute(
        "CREATE INDEX ix_chat_messages_pending ON chat_messages (run_id, created_at) "
        "WHERE role = 'user' AND delivered_at IS NULL"
    )
    op.execute("ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_messages FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON chat_messages USING (tenant_id = app_tenant_id()) "
        "WITH CHECK (tenant_id = app_tenant_id())"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON chat_messages TO {role}")
    op.execute(
        "ALTER TABLE questions ADD COLUMN auto_answered boolean NOT NULL DEFAULT false, "
        "ADD COLUMN default_answers jsonb, ADD COLUMN overridden_at timestamptz"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE questions DROP COLUMN overridden_at, DROP COLUMN default_answers, DROP COLUMN auto_answered"
    )
    op.drop_table("chat_messages")
