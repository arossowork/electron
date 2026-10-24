"""Evaluation baselines (#19): which eval run is a suite's baseline.

Revision ID: 0008_eval_baselines
Revises: 0007_credential_type
Create Date: 2026-10-24

A baseline is promoted on purpose (`POST /evals/{id}/promote-baseline`), never automatically. A row names the
`eval.json` artifact (by hash) of an evaluation run; the newest row of a suite is its baseline, the older rows are
the history. Insert-only: the app role cannot change or delete a promotion. Same tenant RLS pattern as 0004/0006.
"""

from alembic import op

from hwplatform.settings import Settings

revision = "0008_eval_baselines"
down_revision = "0007_credential_type"
branch_labels = None
depends_on = None


def _app_role() -> str:
    role = op.get_context().config.get_main_option("app_db_role") or Settings().app_db_role
    Settings(app_db_role=role)  # must be a plain identifier: it goes into GRANTs
    return role


def upgrade() -> None:
    role = _app_role()
    op.execute(
        """CREATE TABLE eval_baselines (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants ON DELETE CASCADE,
  suite text NOT NULL,
  run_id uuid REFERENCES runs ON DELETE SET NULL,
  sha256 text NOT NULL,
  name text NOT NULL DEFAULT '',
  promoted_by uuid REFERENCES users ON DELETE SET NULL,
  promoted_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute("CREATE INDEX ix_eval_baselines_tenant_id ON eval_baselines (tenant_id)")
    op.execute("CREATE INDEX ix_eval_baselines_suite ON eval_baselines (tenant_id, suite, promoted_at DESC)")
    op.execute("ALTER TABLE eval_baselines ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eval_baselines FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON eval_baselines USING (tenant_id = app_tenant_id()) "
        "WITH CHECK (tenant_id = app_tenant_id())"
    )
    op.execute(f"GRANT SELECT, INSERT ON eval_baselines TO {role}")


def downgrade() -> None:
    op.drop_table("eval_baselines")
