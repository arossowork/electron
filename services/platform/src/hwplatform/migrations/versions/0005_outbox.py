"""Transactional outbox (#11): outbox, consumed_events, NOTIFY trigger, relay role grants.

Revision ID: 0005_outbox
Revises: 0004_runs
Create Date: 2026-10-24

DDL follows ADR 0001, section 5.3. The app role inserts and reads its tenant's events (tenant_isolation).
The relay role `hwflow_relay` (LOGIN, no BYPASSRLS, created by deploy/compose/postgres-init.sh) reads every
tenant's rows and may set published_at, nothing else (policies relay_read/relay_mark, same pattern as the
owner policies of 0002). The purge job runs as the owner and may delete published rows only.
"""

import sqlalchemy as sa
from alembic import op

from hwplatform.settings import Settings

revision = "0005_outbox"
down_revision = "0004_runs"
branch_labels = None
depends_on = None

RELAY_ROLE = "hwflow_relay"


def _app_role() -> str:
    role = op.get_context().config.get_main_option("app_db_role") or Settings().app_db_role
    Settings(app_db_role=role)
    return role


def upgrade() -> None:
    role = _app_role()
    owner = op.get_bind().execute(sa.text("SELECT current_user")).scalar_one()
    Settings(app_db_role=owner)  # goes into DDL below: must be a plain identifier

    op.execute(
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RELAY_ROLE}') "
        f"THEN CREATE ROLE {RELAY_ROLE} NOLOGIN; END IF; END $$"
    )
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RELAY_ROLE}' "
        f"AND (rolsuper OR rolbypassrls)) THEN RAISE EXCEPTION 'relay role {RELAY_ROLE} must not be "
        f"superuser or BYPASSRLS'; END IF; END $$"
    )

    op.execute(
        """CREATE TABLE outbox (
  id bigserial PRIMARY KEY,
  event_id uuid NOT NULL UNIQUE,
  tenant_id uuid NOT NULL REFERENCES tenants ON DELETE CASCADE,
  run_id uuid, seq integer,
  type text NOT NULL, subject text NOT NULL,
  envelope jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz)"""
    )
    op.execute("CREATE INDEX ix_outbox_unpublished ON outbox (id) WHERE published_at IS NULL")
    op.execute("CREATE INDEX ix_outbox_run ON outbox (tenant_id, run_id, seq)")
    op.execute(
        """CREATE TABLE consumed_events (
  consumer text NOT NULL, event_id uuid NOT NULL,
  tenant_id uuid NOT NULL REFERENCES tenants ON DELETE CASCADE,
  consumed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (consumer, event_id))"""
    )
    op.execute("CREATE INDEX ix_consumed_events_tenant_id ON consumed_events (tenant_id)")

    # Wake the relay on commit of new events (it also polls, so a lost NOTIFY only costs latency).
    op.execute(
        "CREATE FUNCTION outbox_notify() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN PERFORM pg_notify('outbox', ''); RETURN NULL; END $$"
    )
    op.execute(
        "CREATE TRIGGER outbox_notify AFTER INSERT ON outbox FOR EACH STATEMENT EXECUTE FUNCTION outbox_notify()"
    )

    for t in ("outbox", "consumed_events"):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} USING (tenant_id = app_tenant_id()) "
            "WITH CHECK (tenant_id = app_tenant_id())"
        )
        op.execute(f"GRANT SELECT, INSERT ON {t} TO {role}")
    op.execute(f"GRANT USAGE ON SEQUENCE outbox_id_seq TO {role}")

    # Relay: every tenant's rows, may only mark them published.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {RELAY_ROLE}")
    op.execute(f"GRANT SELECT ON outbox TO {RELAY_ROLE}")
    op.execute(f"GRANT UPDATE (published_at) ON outbox TO {RELAY_ROLE}")
    op.execute(f"CREATE POLICY relay_read ON outbox FOR SELECT TO {RELAY_ROLE} USING (true)")
    op.execute(
        f"CREATE POLICY relay_mark ON outbox FOR UPDATE TO {RELAY_ROLE} USING (true) WITH CHECK (true)"
    )

    # Purge (hwplatform-purge, owner role): published rows only, never an event still to be relayed.
    op.execute(f"CREATE POLICY purge_read ON outbox FOR SELECT TO {owner} USING (published_at IS NOT NULL)")
    op.execute(f"CREATE POLICY purge_delete ON outbox FOR DELETE TO {owner} USING (published_at IS NOT NULL)")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS outbox_notify ON outbox")
    op.execute("DROP FUNCTION IF EXISTS outbox_notify()")
    op.drop_table("consumed_events")
    op.drop_table("outbox")
