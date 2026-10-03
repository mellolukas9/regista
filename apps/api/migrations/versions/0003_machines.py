"""machines: pools, machines, enrollment keys, machine events, Procrastinate schema

Revision ID: 0003
Revises: 0002

Also adds the two lookup functions used before a tenant is known in agent requests (enrollment
key and machine credential), the purge function for `auth_rate_limits` (ADR 0018) and the schema
of the Procrastinate job queue, applied from a frozen snapshot of the pinned version.
"""

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.util import await_only

from regista_api.core.rls import tenant_rls_statements

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
NOW = sa.text("now()")

# Same SECURITY DEFINER pattern as 0002 (see the comment there).
DEFINER = "SECURITY DEFINER SET search_path = pg_catalog, public"
FLAG_ON = "PERFORM set_config('app.platform_admin', 'on', true);"
FLAG_RESTORE = "PERFORM set_config('app.platform_admin', coalesce(prev, ''), true);"

FUNCTIONS = [
    "app.lookup_enrollment_key(bytea)",
    "app.lookup_machine_credential(uuid)",
    "app.rate_limit_purge(integer)",
]

TENANT_TABLES = ("pools", "machines", "enrollment_keys", "machine_events")

# Frozen copy of `procrastinate.schema.SchemaManager.get_schema()` for the pinned version. A
# library upgrade adds a new migration with the library's own SQL migrations; this file never
# changes (docs/adr/0018).
PROCRASTINATE_SCHEMA = Path(__file__).parent.parent / "sql" / "procrastinate_3_10_0.sql"


def _id() -> sa.Column[object]:
    return sa.Column("id", UUID, server_default=sa.text("uuidv7()"), nullable=False)


def _tenant_id(table: str) -> sa.Column[object]:
    return sa.Column(
        "tenant_id",
        UUID,
        sa.ForeignKey("tenants.id", name=f"fk_{table}_tenant_id_tenants"),
        nullable=False,
    )


def _ts(name: str, *, default: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=NOW if default else None,
        nullable=not default,
    )


def _user_ref(table: str, column: str) -> sa.Column[object]:
    return sa.Column(column, UUID, sa.ForeignKey("users.id", name=f"fk_{table}_{column}_users"))


def _execute_script(sql: str) -> None:
    """Run a multi-statement script with asyncpg's simple query protocol.

    `op.execute` goes through a prepared statement, which asyncpg refuses for several commands,
    and SQLAlchemy's `text()` would read `:name` inside the script as a bind parameter.
    """
    raw = op.get_bind().connection.driver_connection
    if raw is None:
        raise RuntimeError("the migration connection has no driver connection")
    await_only(raw.execute(sql))


def upgrade() -> None:
    # --- pools -----------------------------------------------------------------------------
    op.create_table(
        "pools",
        _id(),
        _tenant_id("pools"),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), server_default="on_prem", nullable=False),
        sa.Column("description", sa.Text()),
        _user_ref("pools", "created_by"),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_pools"),
        # Parent key for the composite foreign keys that tie children to the same tenant.
        sa.UniqueConstraint("tenant_id", "id", name="uq_pools_tenant_id_id"),
        sa.CheckConstraint("kind IN ('on_prem', 'client_cloud', 'internal')", name="kind"),
    )
    op.execute("CREATE UNIQUE INDEX uq_pools_tenant_name ON pools (tenant_id, lower(name))")

    # --- machines --------------------------------------------------------------------------
    op.create_table(
        "machines",
        _id(),
        _tenant_id("machines"),
        sa.Column("pool_id", UUID, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("public_key", postgresql.BYTEA()),
        sa.Column("credential_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("mode", sa.Text(), server_default="service", nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        _ts("last_seen_at"),
        sa.Column("agent_version", sa.Text()),
        sa.Column("os_info", postgresql.JSONB()),
        sa.Column("max_concurrency", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("challenge_hash", postgresql.BYTEA()),
        _ts("challenge_expires_at"),
        _ts("enrolled_at"),
        _ts("revoked_at"),
        _user_ref("machines", "revoked_by"),
        _user_ref("machines", "created_by"),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_machines"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_machines_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pool_id"],
            ["pools.tenant_id", "pools.id"],
            name="fk_machines_tenant_pool_pools",
        ),
        sa.CheckConstraint("mode IN ('service', 'session', 'oneshot')", name="mode"),
        sa.CheckConstraint("status IN ('pending', 'online', 'offline', 'revoked')", name="status"),
        sa.CheckConstraint("name ~ '^[a-z0-9][a-z0-9-]{0,62}$'", name="name_format"),
        sa.CheckConstraint("public_key IS NULL OR length(public_key) = 32", name="public_key_size"),
        sa.CheckConstraint("(status = 'revoked') = (revoked_at IS NOT NULL)", name="revoked"),
        sa.CheckConstraint("status = 'pending' OR public_key IS NOT NULL", name="enrolled_key"),
        sa.CheckConstraint("max_concurrency >= 1", name="max_concurrency"),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_machines_tenant_name ON machines (tenant_id, name) "
        "WHERE status <> 'revoked'"
    )
    op.create_index("ix_machines_tenant_id", "machines", ["tenant_id", "pool_id", "status"])

    # --- enrollment_keys -------------------------------------------------------------------
    op.create_table(
        "enrollment_keys",
        _id(),
        _tenant_id("enrollment_keys"),
        sa.Column("machine_id", UUID, nullable=False),
        sa.Column("key_hash", postgresql.BYTEA(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _ts("used_at"),
        _ts("revoked_at"),
        _user_ref("enrollment_keys", "created_by"),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_enrollment_keys"),
        sa.UniqueConstraint("key_hash", name="uq_enrollment_keys_key_hash"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "machine_id"],
            ["machines.tenant_id", "machines.id"],
            name="fk_enrollment_keys_tenant_machine_machines",
        ),
    )
    # One live key per machine: generating a new one revokes the previous one first.
    op.execute(
        "CREATE UNIQUE INDEX uq_enrollment_keys_live ON enrollment_keys (machine_id) "
        "WHERE used_at IS NULL AND revoked_at IS NULL"
    )
    op.create_index("ix_enrollment_keys_tenant_id", "enrollment_keys", ["tenant_id", "machine_id"])

    # --- machine_events (append-only) ------------------------------------------------------
    op.create_table(
        "machine_events",
        _id(),
        _tenant_id("machine_events"),
        sa.Column("machine_id", UUID, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_machine_events"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "machine_id"],
            ["machines.tenant_id", "machines.id"],
            name="fk_machine_events_tenant_machine_machines",
        ),
        sa.CheckConstraint(
            "kind IN ('enrolled', 're_enrolled', 'first_signal', 'went_offline', 'came_back',"
            " 'agent_updated', 'revoked')",
            name="kind",
        ),
    )
    op.execute(
        "CREATE INDEX ix_machine_events_tenant_id "
        "ON machine_events (tenant_id, machine_id, created_at DESC)"
    )

    # --- RLS and grants (explicit, no DEFAULT PRIVILEGES) -----------------------------------
    for table in TENANT_TABLES:
        for statement in tenant_rls_statements(table):
            op.execute(statement)
    op.execute("GRANT SELECT, INSERT, UPDATE ON pools, machines, enrollment_keys TO regista_app")
    # Append-only: events can be read and inserted, never changed or deleted.
    op.execute("GRANT SELECT, INSERT ON machine_events TO regista_app")

    # --- lookup functions used before a tenant is known -------------------------------------
    op.execute(
        f"""
        CREATE FUNCTION app.lookup_enrollment_key(p_key_hash bytea)
          RETURNS TABLE (
            key_id uuid, tenant_id uuid, machine_id uuid, expires_at timestamptz,
            used_at timestamptz, revoked_at timestamptz, machine_status text)
          LANGUAGE plpgsql {DEFINER} AS
        $$
        DECLARE prev text := current_setting('app.platform_admin', true);
        BEGIN
          {FLAG_ON}
          RETURN QUERY
            SELECT k.id, k.tenant_id, k.machine_id, k.expires_at, k.used_at, k.revoked_at,
                   m.status
            FROM public.enrollment_keys k
            JOIN public.machines m ON m.tenant_id = k.tenant_id AND m.id = k.machine_id
            WHERE k.key_hash = p_key_hash;
          {FLAG_RESTORE}
        END $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION app.lookup_machine_credential(p_machine_id uuid)
          RETURNS TABLE (
            tenant_id uuid, public_key bytea, credential_version integer, status text)
          LANGUAGE plpgsql {DEFINER} AS
        $$
        DECLARE prev text := current_setting('app.platform_admin', true);
        BEGIN
          {FLAG_ON}
          RETURN QUERY
            SELECT m.tenant_id, m.public_key, m.credential_version, m.status
            FROM public.machines m
            WHERE m.id = p_machine_id;
          {FLAG_RESTORE}
        END $$
        """
    )
    # auth_rate_limits is reachable only through app.rate_limit_hit; the worker purges old
    # windows through this function. Returns the number of rows removed.
    op.execute(
        f"""
        CREATE FUNCTION app.rate_limit_purge(p_older_than_seconds integer)
          RETURNS integer
          LANGUAGE plpgsql {DEFINER} AS
        $$
        DECLARE
          n integer;
          prev text := current_setting('app.rate_limit', true);
        BEGIN
          PERFORM set_config('app.rate_limit', 'on', true);
          DELETE FROM public.auth_rate_limits r
            WHERE r.window_start < now() - make_interval(secs => p_older_than_seconds);
          GET DIAGNOSTICS n = ROW_COUNT;
          PERFORM set_config('app.rate_limit', coalesce(prev, ''), true);
          RETURN n;
        END $$
        """
    )
    for signature in FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO regista_app")

    # --- Procrastinate job queue (platform data: no tenant_id, only ids in task arguments) --
    _execute_script(PROCRASTINATE_SCHEMA.read_text(encoding="utf-8"))
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "procrastinate_jobs, procrastinate_events, procrastinate_periodic_defers, "
        "procrastinate_workers TO regista_app"
    )
    op.execute(
        "GRANT USAGE, SELECT ON SEQUENCE "
        "procrastinate_jobs_id_seq, procrastinate_events_id_seq, "
        "procrastinate_periodic_defers_id_seq, procrastinate_workers_id_seq TO regista_app"
    )
    op.execute(
        """
        DO $$
        DECLARE f record;
        BEGIN
          FOR f IN
            SELECT p.oid::regprocedure AS sig FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname LIKE 'procrastinate\\_%'
          LOOP
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO regista_app', f.sig);
          END LOOP;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE f record;
        BEGIN
          FOR f IN
            SELECT p.oid::regprocedure AS sig FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname LIKE 'procrastinate\\_%'
          LOOP
            EXECUTE format('DROP FUNCTION %s CASCADE', f.sig);
          END LOOP;
        END $$
        """
    )
    for table in (
        "procrastinate_events",
        "procrastinate_periodic_defers",
        "procrastinate_jobs",
        "procrastinate_workers",
    ):
        op.execute(f"DROP TABLE {table} CASCADE")
    op.execute("DROP TYPE procrastinate_job_to_defer_v1")
    op.execute("DROP TYPE procrastinate_job_event_type")
    op.execute("DROP TYPE procrastinate_job_status")
    for signature in FUNCTIONS:
        op.execute(f"DROP FUNCTION {signature}")
    for table in ("machine_events", "enrollment_keys", "machines", "pools"):
        op.drop_table(table)
