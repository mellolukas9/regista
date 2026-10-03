"""jobs: bots, jobs, job_logs (monthly partitions), artifacts (docs/specs/data-model.md, ADR 0020)

Revision ID: 0004
Revises: 0003

Also adds the NOTIFY trigger that wakes the long-polling agents, and two functions in the same
SECURITY DEFINER pattern as 0002 and 0003: one that creates the monthly partitions of `job_logs`
(fixed DDL, the app role gets no DDL rights) and one that reports whether they exist.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from regista_api.core.rls import tenant_rls_statements

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
NOW = sa.text("now()")

DEFINER = "SECURITY DEFINER SET search_path = pg_catalog, public"
FUNCTIONS = [
    "app.ensure_job_log_partitions(integer)",
    "app.job_logs_partition_status()",
]

ERROR_CODES = (
    "machine_lost",
    "machine_revoked",
    "timeout",
    "robot_failed",
    "robot_not_found",
    "cancelled",
    "internal",
)


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


def upgrade() -> None:
    # --- bots ------------------------------------------------------------------------------
    op.create_table(
        "bots",
        _id(),
        _tenant_id("bots"),
        sa.Column("pool_id", UUID, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        # The robot's folder in development and its package in M4. Never changes.
        sa.Column("package_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("concurrency", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _user_ref("bots", "created_by"),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_bots"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_bots_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pool_id"], ["pools.tenant_id", "pools.id"], name="fk_bots_tenant_pool"
        ),
        sa.CheckConstraint("package_name ~ '^[a-z][a-z0-9_]{0,62}$'", name="package_name_format"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 120", name="name_length"),
        sa.CheckConstraint("concurrency >= 1", name="concurrency"),
    )
    op.execute("CREATE UNIQUE INDEX uq_bots_tenant_name ON bots (tenant_id, lower(name))")
    op.create_index("uq_bots_tenant_package", "bots", ["tenant_id", "package_name"], unique=True)
    op.create_index("ix_bots_tenant_id", "bots", ["tenant_id", "pool_id"])

    # --- jobs ------------------------------------------------------------------------------
    op.create_table(
        "jobs",
        _id(),
        _tenant_id("jobs"),
        sa.Column("bot_id", UUID, nullable=False),
        # No foreign key yet: `bot_versions` arrives in M4. Null in development.
        sa.Column("bot_version_id", UUID),
        sa.Column("pool_id", UUID, nullable=False),
        sa.Column("machine_id", UUID),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("trigger", sa.Text(), server_default="manual", nullable=False),
        _user_ref("jobs", "triggered_by"),
        sa.Column(
            "params", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("short_code", sa.Text(), nullable=False),
        _ts("assigned_at"),
        _ts("started_at"),
        _ts("finished_at"),
        sa.Column("error_code", sa.Text()),
        sa.Column("error_message", sa.Text()),
        _ts("cancel_requested_at"),
        sa.Column("items_successful", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("items_failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("items_abandoned", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("items_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_jobs_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "short_code", name="uq_jobs_tenant_short_code"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "bot_id"], ["bots.tenant_id", "bots.id"], name="fk_jobs_tenant_bot_bots"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pool_id"], ["pools.tenant_id", "pools.id"], name="fk_jobs_tenant_pool"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "machine_id"],
            ["machines.tenant_id", "machines.id"],
            name="fk_jobs_tenant_machine_machines",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'assigned', 'running', 'completed', 'failed', 'cancelled')",
            name="status",
        ),
        sa.CheckConstraint("trigger IN ('manual', 'schedule', 'api')", name="trigger"),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ("
            + ", ".join(f"'{c}'" for c in ERROR_CODES)
            + ")",
            name="error_code",
        ),
        sa.CheckConstraint(
            "error_message IS NULL OR length(error_message) <= 1000", name="error_message_length"
        ),
        sa.CheckConstraint("octet_length(params::text) <= 8192", name="params_size"),
        sa.CheckConstraint(
            "(status IN ('completed', 'failed', 'cancelled')) = (finished_at IS NOT NULL)",
            name="finished",
        ),
        sa.CheckConstraint(
            "status NOT IN ('assigned', 'running') OR machine_id IS NOT NULL", name="machine"
        ),
        sa.CheckConstraint("short_code ~ '^exec-[0-9a-f]{6}$'", name="short_code_format"),
    )
    # The database itself guarantees one execution per machine at a time.
    op.execute(
        "CREATE UNIQUE INDEX uq_jobs_one_active_per_machine ON jobs (machine_id) "
        "WHERE status IN ('assigned', 'running')"
    )
    op.execute(
        "CREATE INDEX ix_jobs_pending ON jobs (tenant_id, pool_id, created_at) "
        "WHERE status = 'pending'"
    )
    op.execute("CREATE INDEX ix_jobs_tenant_created ON jobs (tenant_id, created_at DESC)")
    op.execute(
        "CREATE INDEX ix_jobs_tenant_bot_created ON jobs (tenant_id, bot_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_jobs_tenant_machine ON jobs (tenant_id, machine_id, created_at DESC)"
    )

    # Wakes the agents that wait for work (ADR 0020). Only ids travel in the payload.
    op.execute(
        """
        CREATE FUNCTION app.notify_job_created() RETURNS trigger
          LANGUAGE plpgsql AS
        $$
        BEGIN
          PERFORM pg_notify('regista_jobs', NEW.tenant_id::text || ':' || NEW.pool_id::text);
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER jobs_notify AFTER INSERT ON jobs "
        "FOR EACH ROW EXECUTE FUNCTION app.notify_job_created()"
    )

    # --- job_logs (partitioned by month) -----------------------------------------------------
    op.execute(
        """
        CREATE TABLE job_logs (
          tenant_id uuid NOT NULL,
          job_id uuid NOT NULL,
          seq bigint NOT NULL,
          ts timestamptz NOT NULL,
          level text NOT NULL,
          message text NOT NULL,
          item_id uuid,
          item_ref text,
          attempt integer,
          extra jsonb,
          CONSTRAINT fk_job_logs_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
          CONSTRAINT fk_job_logs_tenant_job_jobs FOREIGN KEY (tenant_id, job_id)
            REFERENCES jobs (tenant_id, id),
          CONSTRAINT ck_job_logs_level CHECK (level IN ('INFO', 'WARN', 'ERROR')),
          CONSTRAINT ck_job_logs_message_size CHECK (octet_length(message) <= 8192),
          CONSTRAINT ck_job_logs_extra_size
            CHECK (extra IS NULL OR octet_length(extra::text) <= 4096),
          -- Resending a batch does not duplicate lines. The key holds the partition column.
          CONSTRAINT uq_job_logs_job_seq_ts UNIQUE (job_id, seq, ts)
        ) PARTITION BY RANGE (ts)
        """
    )
    op.execute("CREATE INDEX ix_job_logs_tenant_id ON job_logs (tenant_id, job_id, seq)")
    for statement in tenant_rls_statements("job_logs"):
        op.execute(statement)

    # Creates the partitions from the current month up to `p_months_ahead` months ahead. Each
    # one gets the same forced RLS and policies as the parent: a partition read directly is
    # still isolated. Idempotent. No DEFAULT partition on purpose (ADR 0020).
    op.execute(
        f"""
        CREATE FUNCTION app.ensure_job_log_partitions(p_months_ahead integer)
          RETURNS integer
          LANGUAGE plpgsql {DEFINER} AS
        $$
        DECLARE
          first_month timestamptz :=
            date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
          lo timestamptz;
          hi timestamptz;
          part text;
          created integer := 0;
          i integer;
        BEGIN
          IF p_months_ahead IS NULL OR p_months_ahead < 0 OR p_months_ahead > 24 THEN
            RAISE EXCEPTION 'p_months_ahead must be between 0 and 24';
          END IF;
          FOR i IN 0..p_months_ahead LOOP
            lo := first_month + make_interval(months => i);
            hi := first_month + make_interval(months => i + 1);
            part := 'job_logs_' || to_char(lo AT TIME ZONE 'UTC', 'YYYYMM');
            IF to_regclass(format('public.%I', part)) IS NULL THEN
              EXECUTE format(
                'CREATE TABLE public.%I PARTITION OF public.job_logs FOR VALUES FROM (%L) TO (%L)',
                part, lo, hi);
              EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', part);
              EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', part);
              EXECUTE format(
                'CREATE POLICY tenant_select ON public.%I FOR SELECT '
                'USING (tenant_id = app.current_tenant_id() OR app.is_platform_admin())', part);
              EXECUTE format(
                'CREATE POLICY tenant_insert ON public.%I FOR INSERT '
                'WITH CHECK (tenant_id = app.current_tenant_id())', part);
              EXECUTE format(
                'CREATE POLICY tenant_update ON public.%I FOR UPDATE '
                'USING (tenant_id = app.current_tenant_id()) '
                'WITH CHECK (tenant_id = app.current_tenant_id())', part);
              EXECUTE format(
                'CREATE POLICY tenant_delete ON public.%I FOR DELETE '
                'USING (tenant_id = app.current_tenant_id())', part);
              created := created + 1;
            END IF;
          END LOOP;
          RETURN created;
        END $$
        """
    )
    # Read-only: do the partitions of this month and the next one exist? (`/health` uses it.)
    op.execute(
        f"""
        CREATE FUNCTION app.job_logs_partition_status()
          RETURNS TABLE (current_month boolean, next_month boolean)
          LANGUAGE sql STABLE {DEFINER} AS
        $$
          SELECT
            to_regclass('public.job_logs_' || to_char(now() AT TIME ZONE 'UTC', 'YYYYMM'))
              IS NOT NULL,
            to_regclass('public.job_logs_'
              || to_char((now() AT TIME ZONE 'UTC') + interval '1 month', 'YYYYMM')) IS NOT NULL
        $$
        """
    )

    # --- artifacts ---------------------------------------------------------------------------
    op.create_table(
        "artifacts",
        _id(),
        _tenant_id("artifacts"),
        sa.Column("job_id", UUID, nullable=False),
        # Items and attempts exist from M5; no foreign keys until then.
        sa.Column("item_id", UUID),
        sa.Column("attempt_id", UUID),
        sa.Column("kind", sa.Text(), server_default="screenshot", nullable=False),
        # Built by the server only: tenants/<tenant>/jobs/<job>/<artifact>.<ext>
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        # Null until the agent confirms the upload and the server sees the object.
        _ts("uploaded_at"),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_artifacts"),
        sa.UniqueConstraint("storage_key", name="uq_artifacts_storage_key"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["jobs.tenant_id", "jobs.id"],
            name="fk_artifacts_tenant_job_jobs",
        ),
        sa.CheckConstraint("kind IN ('screenshot', 'file')", name="kind"),
        sa.CheckConstraint("content_type IN ('image/png', 'image/jpeg')", name="content_type"),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 50000000", name="size_bytes"),
    )
    op.create_index("ix_artifacts_tenant_id", "artifacts", ["tenant_id", "job_id"])

    # --- RLS and grants (explicit, no DEFAULT PRIVILEGES) -------------------------------------
    for table in ("bots", "jobs", "artifacts"):
        for statement in tenant_rls_statements(table):
            op.execute(statement)
    op.execute("GRANT SELECT, INSERT, UPDATE ON bots, jobs, artifacts TO regista_app")
    # Logs are append-only and reached through the parent; the partitions get no grant.
    op.execute("GRANT SELECT, INSERT ON job_logs TO regista_app")
    for signature in FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO regista_app")

    # This month and the next three, so a fresh database can take logs at once.
    op.execute("SELECT app.ensure_job_log_partitions(3)")


def downgrade() -> None:
    for signature in FUNCTIONS:
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("DROP TABLE artifacts")
    op.execute("DROP TABLE job_logs")  # takes the partitions with it
    op.execute("DROP TRIGGER jobs_notify ON jobs")
    op.execute("DROP FUNCTION app.notify_job_created()")
    op.execute("DROP TABLE jobs")
    op.execute("DROP TABLE bots")
