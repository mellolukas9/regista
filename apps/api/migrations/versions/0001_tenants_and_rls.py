"""tenants table and RLS foundation

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA app")
    op.execute("GRANT USAGE ON SCHEMA app TO regista_app")
    op.execute(
        """
        CREATE FUNCTION app.current_tenant_id() RETURNS uuid
          LANGUAGE sql STABLE AS
          $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app.is_platform_admin() RETURNS boolean
          LANGUAGE sql STABLE AS
          $$ SELECT coalesce(current_setting('app.platform_admin', true), '') = 'on' $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION app.current_tenant_id() TO regista_app")
    op.execute("GRANT EXECUTE ON FUNCTION app.is_platform_admin() TO regista_app")

    op.create_table(
        "tenants",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("data_region", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )

    # `tenants` is keyed by `id` (not `tenant_id`) and only platform admins create tenants.
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_select ON tenants FOR SELECT "
        "USING (id = app.current_tenant_id() OR app.is_platform_admin())"
    )
    op.execute(
        "CREATE POLICY tenant_insert ON tenants FOR INSERT WITH CHECK (app.is_platform_admin())"
    )
    op.execute(
        "CREATE POLICY tenant_update ON tenants FOR UPDATE "
        "USING (id = app.current_tenant_id()) WITH CHECK (id = app.current_tenant_id())"
    )
    # No DELETE grant or policy: tenants are deactivated (is_active), never deleted.
    op.execute("GRANT SELECT, INSERT, UPDATE ON tenants TO regista_app")


def downgrade() -> None:
    op.drop_table("tenants")
    op.execute("DROP FUNCTION app.is_platform_admin()")
    op.execute("DROP FUNCTION app.current_tenant_id()")
    op.execute("DROP SCHEMA app")
