"""Questions (#12): what a design run asks the person, and the answers (ADR 0001, sections 6 and 7.3).

Revision ID: 0006_questions
Revises: 0005_outbox
Create Date: 2026-10-24

One row per question round of a run. The worker (`record_question`, #15) inserts it as `open` and emits
`question.asked`; the API (`POST /questions/{id}/answer`) answers it and signals the workflow. Same tenant RLS
pattern as the other run tables (0004).
"""

from alembic import op

from hwplatform.settings import Settings

revision = "0006_questions"
down_revision = "0005_outbox"
branch_labels = None
depends_on = None


def _app_role() -> str:
    role = op.get_context().config.get_main_option("app_db_role") or Settings().app_db_role
    Settings(app_db_role=role)  # must be a plain identifier: it goes into GRANTs
    return role


def upgrade() -> None:
    role = _app_role()
    op.execute(
        """CREATE TABLE questions (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants ON DELETE CASCADE,
  run_id uuid NOT NULL REFERENCES runs ON DELETE CASCADE,
  stage text NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'expired', 'cancelled')),
  questions jsonb NOT NULL,
  answers jsonb,
  answered_by uuid REFERENCES users ON DELETE SET NULL,
  asked_at timestamptz NOT NULL DEFAULT now(),
  answered_at timestamptz)"""
    )
    op.execute("CREATE INDEX ix_questions_tenant_id ON questions (tenant_id)")
    op.execute("CREATE INDEX ix_questions_run ON questions (run_id, asked_at)")
    op.execute("ALTER TABLE questions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE questions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON questions USING (tenant_id = app_tenant_id()) "
        "WITH CHECK (tenant_id = app_tenant_id())"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON questions TO {role}")


def downgrade() -> None:
    op.drop_table("questions")
