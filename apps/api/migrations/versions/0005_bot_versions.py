"""bot_versions: signed robot packages (docs/specs/data-model.md, ADR 0021)

Revision ID: 0005
Revises: 0004

Besides the table, this wires the versions into the rest of the model:

* `bots.current_version_id` and `jobs.bot_version_id` become composite foreign keys that carry the
  tenant and the bot, so the database itself refuses to put another bot's (or another client's)
  version in use or on a run;
* a trigger keeps a published version immutable and only lets `uploading` move on;
* a trigger refuses a `current_version_id` that is not published;
* `jobs.error_code` accepts the four codes of M4 and `jobs.error_reason` holds the closed list of
  reasons of `package_invalid` (the panel shows a fixed text per reason);
* `machines.paused_locally` is the indication of the local kill switch.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from regista_api.core.rls import tenant_rls_statements

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)

OLD_ERROR_CODES = (
    "machine_lost",
    "machine_revoked",
    "timeout",
    "robot_failed",
    "robot_not_found",
    "cancelled",
    "internal",
)
NEW_ERROR_CODES = (
    *OLD_ERROR_CODES,
    "package_invalid",
    "robot_not_allowed",
    "runtime_missing",
    "environment_failed",
)
ERROR_REASONS = (
    "hash_mismatch",
    "signature_invalid",
    "unknown_key",
    "wrong_client",
    "wrong_package",
    "wrong_version",
    "unsafe_archive",
    "too_large",
    "malformed_package",
)


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_table(
        "bot_versions",
        sa.Column("id", UUID, server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("tenants.id", name="fk_bot_versions_tenant_id_tenants"),
            nullable=False,
        ),
        sa.Column("bot_id", UUID, nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("package_sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("signature", postgresql.BYTEA(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        # The signed manifest (client, package, version, runtime), kept as the server checked it.
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        # Built by the server only: tenants/<tenant>/bots/<bot>/versions/<id>.rgpkg
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="uploading", nullable=False),
        sa.Column("upload_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("release_note", sa.Text()),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", name="fk_bot_versions_created_by_users")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bot_versions"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_bot_versions_tenant_id_id"),
        # The target of the composite foreign keys of `bots` and `jobs`.
        sa.UniqueConstraint("tenant_id", "bot_id", "id", name="uq_bot_versions_tenant_bot_id"),
        sa.UniqueConstraint("storage_key", name="uq_bot_versions_storage_key"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "bot_id"],
            ["bots.tenant_id", "bots.id"],
            name="fk_bot_versions_tenant_bot_bots",
        ),
        sa.CheckConstraint("version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'", name="version_format"),
        sa.CheckConstraint("length(version) <= 32", name="version_length"),
        sa.CheckConstraint("package_sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 524288000", name="size_bytes"),
        sa.CheckConstraint("octet_length(signature) = 64", name="signature_length"),
        sa.CheckConstraint("key_id ~ '^[0-9a-f]{16}$'", name="key_id_format"),
        sa.CheckConstraint("octet_length(manifest::text) <= 16384", name="manifest_size"),
        sa.CheckConstraint("status IN ('uploading', 'published', 'expired')", name="status"),
        sa.CheckConstraint(
            "(status = 'published') = (published_at IS NOT NULL)", name="published_at"
        ),
        sa.CheckConstraint("release_note IS NULL OR length(release_note) <= 2000", name="note"),
    )
    # One live version number per bot. An expired upload does not hold the number.
    op.execute(
        "CREATE UNIQUE INDEX uq_bot_versions_bot_version ON bot_versions"
        " (tenant_id, bot_id, version) WHERE status <> 'expired'"
    )
    op.execute(
        "CREATE INDEX ix_bot_versions_tenant_bot ON bot_versions"
        " (tenant_id, bot_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_bot_versions_uploading ON bot_versions (upload_expires_at)"
        " WHERE status = 'uploading'"
    )

    # Published versions never change; an upload only moves to published or expired.
    op.execute(
        """
        CREATE FUNCTION app.bot_versions_guard() RETURNS trigger
          LANGUAGE plpgsql AS
        $$
        BEGIN
          IF OLD.status <> 'uploading' THEN
            RAISE EXCEPTION 'bot version % is % and cannot change', OLD.id, OLD.status
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER bot_versions_guard BEFORE UPDATE ON bot_versions"
        " FOR EACH ROW EXECUTE FUNCTION app.bot_versions_guard()"
    )

    # --- bots.current_version_id -------------------------------------------------------------
    op.add_column("bots", sa.Column("current_version_id", UUID))
    # (tenant, bot, version): a version of another bot or client cannot be put in use.
    op.create_foreign_key(
        "fk_bots_tenant_current_version",
        "bots",
        "bot_versions",
        ["tenant_id", "id", "current_version_id"],
        ["tenant_id", "bot_id", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION app.bots_current_version_published() RETURNS trigger
          LANGUAGE plpgsql AS
        $$
        BEGIN
          IF NEW.current_version_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM bot_versions v WHERE v.id = NEW.current_version_id
              AND v.tenant_id = NEW.tenant_id AND v.bot_id = NEW.id AND v.status = 'published'
          ) THEN
            RAISE EXCEPTION 'the version in use must be a published version of the bot'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER bots_current_version_published"
        " BEFORE INSERT OR UPDATE OF current_version_id ON bots"
        " FOR EACH ROW EXECUTE FUNCTION app.bots_current_version_published()"
    )

    # --- jobs ---------------------------------------------------------------------------------
    op.create_foreign_key(
        "fk_jobs_tenant_bot_version",
        "jobs",
        "bot_versions",
        ["tenant_id", "bot_id", "bot_version_id"],
        ["tenant_id", "bot_id", "id"],
    )
    # The naming convention prefixes check names (`ck_<table>_<name>`), so the short name is used.
    op.drop_constraint("error_code", "jobs", type_="check")
    op.create_check_constraint(
        "error_code",
        "jobs",
        f"error_code IS NULL OR error_code IN ({_in(NEW_ERROR_CODES)})",
    )
    op.add_column("jobs", sa.Column("error_reason", sa.Text()))
    op.create_check_constraint(
        "error_reason",
        "jobs",
        f"error_reason IS NULL OR (coalesce(error_code = 'package_invalid', false)"
        f" AND error_reason IN ({_in(ERROR_REASONS)}))",
    )

    # --- machines -----------------------------------------------------------------------------
    op.add_column(
        "machines",
        sa.Column("paused_locally", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    # --- RLS and grants -----------------------------------------------------------------------
    for statement in tenant_rls_statements("bot_versions"):
        op.execute(statement)
    # No DELETE: the history stays. The only columns that ever change are the state of the upload.
    op.execute("GRANT SELECT, INSERT ON bot_versions TO regista_app")
    op.execute("GRANT UPDATE (status, published_at) ON bot_versions TO regista_app")


def downgrade() -> None:
    op.drop_column("machines", "paused_locally")
    op.drop_constraint("error_reason", "jobs", type_="check")
    op.drop_column("jobs", "error_reason")
    op.execute(
        "UPDATE jobs SET error_code = 'internal' WHERE error_code NOT IN ("
        + _in(OLD_ERROR_CODES)
        + ")"
    )
    op.drop_constraint("error_code", "jobs", type_="check")
    op.create_check_constraint(
        "error_code",
        "jobs",
        f"error_code IS NULL OR error_code IN ({_in(OLD_ERROR_CODES)})",
    )
    op.drop_constraint("fk_jobs_tenant_bot_version", "jobs", type_="foreignkey")
    op.execute("DROP TRIGGER bots_current_version_published ON bots")
    op.execute("DROP FUNCTION app.bots_current_version_published()")
    op.drop_constraint("fk_bots_tenant_current_version", "bots", type_="foreignkey")
    op.drop_column("bots", "current_version_id")
    op.execute("DROP TABLE bot_versions")
    op.execute("DROP FUNCTION app.bot_versions_guard()")
