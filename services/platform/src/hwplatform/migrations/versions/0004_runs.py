"""Runs (#7): design_versions, artifacts, runs, run_stages, run_artifacts, stage_cache, project_state.

Revision ID: 0004_runs
Revises: 0003_auth_hardening
Create Date: 2026-10-24

DDL follows ADR 0001, section 3.4. Every table has tenant_id (FK tenants, ON DELETE CASCADE), an index on
it, ENABLE + FORCE row-level security and the policy tenant_isolation on app_tenant_id(), like `projects`.
"""

from alembic import op

from hwplatform.settings import Settings

revision = "0004_runs"
down_revision = "0003_auth_hardening"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "design_versions",
    "artifacts",
    "runs",
    "run_stages",
    "run_artifacts",
    "stage_cache",
    "project_state",
)

DDL = [
    """CREATE TABLE design_versions (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants ON DELETE CASCADE,
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,
  seq integer NOT NULL,
  content_sha256 char(64) NOT NULL, design_sha256 char(64) NOT NULL, requirements_sha256 char(64),
  project_tree_sha256 char(64) NOT NULL,
  source text NOT NULL CHECK (source IN ('upload', 'agent')),
  parent_id uuid REFERENCES design_versions,
  message text NOT NULL DEFAULT '',
  created_by uuid REFERENCES users ON DELETE SET NULL, created_by_run uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, seq), UNIQUE (project_id, content_sha256))""",
    """CREATE TABLE artifacts (
  tenant_id uuid NOT NULL REFERENCES tenants ON DELETE CASCADE,
  sha256 char(64) NOT NULL, size bigint NOT NULL, media_type text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, sha256))""",
    "CREATE TYPE run_kind AS ENUM ('build', 'design', 'eval')",
    "CREATE TYPE run_status AS ENUM ('queued', 'running', 'waiting', 'succeeded', 'failed', 'cancelled')",
    """CREATE TABLE runs (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants ON DELETE CASCADE,
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,
  design_version_id uuid NOT NULL REFERENCES design_versions,
  kind run_kind NOT NULL, status run_status NOT NULL DEFAULT 'queued',
  verdict text CHECK (verdict IN ('PASS', 'FAIL')),
  workflow_id text NOT NULL, options jsonb NOT NULL, options_hash char(12) NOT NULL, idempotency_key text,
  settings jsonb, code jsonb, tools jsonb, error jsonb, artifacts jsonb NOT NULL DEFAULT '{}',
  parent_run_id uuid REFERENCES runs ON DELETE SET NULL,
  requested_by uuid REFERENCES users ON DELETE SET NULL,
  event_seq integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, ended_at timestamptz,
  UNIQUE (tenant_id, idempotency_key))""",
    "CREATE INDEX ix_runs_project ON runs (tenant_id, project_id, id DESC)",
    "CREATE UNIQUE INDEX ux_runs_active_workflow ON runs (workflow_id) "
    "WHERE status IN ('queued', 'running', 'waiting')",
    "CREATE INDEX ix_runs_active_tenant ON runs (tenant_id) WHERE status IN ('queued', 'running', 'waiting')",
    """CREATE TABLE run_stages (
  run_id uuid NOT NULL REFERENCES runs ON DELETE CASCADE,
  tenant_id uuid NOT NULL REFERENCES tenants ON DELETE CASCADE,
  key text NOT NULL, stage text NOT NULL, seq integer NOT NULL, status text NOT NULL, verdict text,
  cached boolean NOT NULL DEFAULT false, cache_key char(64), attempt integer NOT NULL DEFAULT 1,
  summary jsonb NOT NULL DEFAULT '{}', result_sha256 char(64), outputs jsonb NOT NULL DEFAULT '{}',
  error jsonb, started_at timestamptz, ended_at timestamptz, seconds double precision,
  PRIMARY KEY (run_id, key))""",
    """CREATE TABLE run_artifacts (
  run_id uuid NOT NULL REFERENCES runs ON DELETE CASCADE,
  tenant_id uuid NOT NULL REFERENCES tenants ON DELETE CASCADE,
  name text NOT NULL, stage_key text, sha256 char(64) NOT NULL, media_type text NOT NULL, size bigint NOT NULL,
  PRIMARY KEY (run_id, name))""",
    """CREATE TABLE stage_cache (
  tenant_id uuid NOT NULL REFERENCES tenants ON DELETE CASCADE,
  stage text NOT NULL, cache_key char(64) NOT NULL, result jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), last_hit_at timestamptz,
  PRIMARY KEY (tenant_id, stage, cache_key))""",
    """CREATE TABLE project_state (
  tenant_id uuid NOT NULL REFERENCES tenants ON DELETE CASCADE,
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,
  name text NOT NULL, sha256 char(64) NOT NULL, run_id uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, name))""",
]

# Privileges of the app role (the API and the worker). Nothing is deleted by the app: rows go with their
# project or tenant (FK cascades run as the table owner), or by the owner-run purge.
GRANTS = {
    "design_versions": "SELECT, INSERT",
    "artifacts": "SELECT, INSERT",
    "runs": "SELECT, INSERT, UPDATE",
    "run_stages": "SELECT, INSERT, UPDATE",
    "run_artifacts": "SELECT, INSERT, UPDATE",
    "stage_cache": "SELECT, INSERT, UPDATE",
    "project_state": "SELECT, INSERT, UPDATE",
}


def _app_role() -> str:
    role = op.get_context().config.get_main_option("app_db_role") or Settings().app_db_role
    Settings(app_db_role=role)  # must be a plain identifier: it goes into GRANTs
    return role


def upgrade() -> None:
    role = _app_role()
    for stmt in DDL:
        op.execute(stmt)
    for t in TENANT_TABLES:
        op.create_index(f"ix_{t}_tenant_id", t, ["tenant_id"])
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} USING (tenant_id = app_tenant_id()) "
            "WITH CHECK (tenant_id = app_tenant_id())"
        )
        op.execute(f"GRANT {GRANTS[t]} ON {t} TO {role}")


def downgrade() -> None:
    for t in reversed(TENANT_TABLES):
        op.drop_table(t)
    op.execute("DROP TYPE IF EXISTS run_status")
    op.execute("DROP TYPE IF EXISTS run_kind")
